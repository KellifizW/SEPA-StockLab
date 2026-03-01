"""
modules/combined_scanner.py
═══════════════════════════════════════════════════════════════════════════════
Unified daily scanner combining SEPA (Minervini) and QM (Qullamaggie) methods.

Instead of running two separate scans with duplicate data fetches, this module:
  1. Runs Stage 1 ONCE to get a common ticker universe
  2. Loads RS rankings ONCE
  3. Downloads price data ONCE using batch_download_and_enrich
  4. Runs SEPA Stage 2-3 and QM Stage 2-3 IN PARALLEL using ThreadPoolExecutor
  5. Returns both result sets separately for their respective strategy pages

Time savings: ~40-60% (mostly from single yfinance batch download)
"""

import sys
import time as _time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_sector_rankings, batch_download_and_enrich
from modules.rs_ranking import _ensure_rs_loaded
from modules.market_env import assess

logger = logging.getLogger(__name__)

# ─── Combined scan progress / cancel (module-level) ──────────────────────────
_combined_lock      = threading.Lock()
_combined_cancel    = threading.Event()
_combined_progress  = {"stage": "idle", "pct": 0, "msg": "", "ticker": ""}

def set_combined_cancel(event: threading.Event):
    """Register external cancel event from app.py."""
    global _combined_cancel
    _combined_cancel = event

def get_combined_progress() -> dict:
    with _combined_lock:
        return dict(_combined_progress)

def _progress(stage: str, pct: int, msg: str = "", ticker: str = ""):
    with _combined_lock:
        _combined_progress.update({"stage": stage, "pct": pct, "msg": msg, "ticker": ticker})

def _cancelled() -> bool:
    return _combined_cancel.is_set()


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED SCANNING ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def run_combined_scan(custom_filters: dict = None,
                      refresh_rs: bool = False,
                      verbose: bool = True,
                      stage1_source: str = None) -> Tuple[dict, dict]:
    """
    Run both SEPA and QM scans with shared Stage 0-3 infrastructure.
    
    Returns: (sepa_result, qm_result) where each is a dict with:
      - 'passed': DataFrame of passed candidates
      - 'all': DataFrame of all Stage 2 candidates 
      - 'market_env': market environment assessment
      - 'timing': dict with stage timings
      - 'error': error message if any
    """
    _t_combined_start = _time.perf_counter()
    
    def _elapsed(since=None):
        return _time.perf_counter() - (since if since is not None else _t_combined_start)
    
    _progress("Starting combined scan...", 0, "Initialising")
    _combined_cancel.clear()
    
    print("\n" + "=" * 70)
    print("  COMBINED SEPA + QM SCAN  —  Unified Daily Review")
    print("=" * 70)
    
    # ── STAGE 0: Market environment ────────────────────────────────────────
    _progress("Stage 0 -- Market Env", 2, "Assessing market regime...")
    _t0 = _time.perf_counter()
    try:
        market_env = assess(verbose=False)
    except Exception as e:
        logger.warning("[Combined] Market env failed: %s", e)
        market_env = {"regime": "UNKNOWN", "error": str(e)}
    logger.info("[Timing] Market env: %.1fs", _elapsed(_t0))
    if verbose:
        print(f"\n[Market] Regime: {market_env.get('regime', '?')}")
    
    # ── STAGE 0B: RS Rankings (loaded once for both methods) ───────────────
    _progress("Stage 0 -- RS Rankings", 5, "Building / loading RS cache…")
    _t0b = _time.perf_counter()
    try:
        _ensure_rs_loaded(force_refresh=refresh_rs)
    except Exception as e:
        logger.warning("[Combined] RS load failed: %s", e)
    logger.info("[Timing] RS load: %.1fs", _elapsed(_t0b))
    
    if _cancelled():
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    # ── STAGE 1: Coarse filter (once, yields common ticker universe) ───────
    _progress("Stage 1 -- Coarse Filter", 8, "Running screener…")
    _t1 = _time.perf_counter()
    
    from modules.screener import run_stage1
    try:
        s1_tickers = run_stage1(custom_filters, verbose=verbose, stage1_source=stage1_source)
    except Exception as e:
        logger.error("[Combined] Stage 1 failed: %s", e)
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    logger.info("[Timing] Stage 1: %.1fs -> %d candidates", _elapsed(_t1), len(s1_tickers))
    if verbose:
        print(f"[Stage 1] {len(s1_tickers)} candidate tickers")
    
    _progress("Stage 1 -- Coarse Filter", 20, f"{len(s1_tickers)} candidates")
    
    if _cancelled():
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    if not s1_tickers:
        print("[ERROR] Stage 1 returned no tickers")
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    # ── STAGE 2: Batch download (once for both methods) ──────────────────
    _progress("Stage 2 -- Batch Download", 25, "Downloading price history…")
    _t2_dl = _time.perf_counter()
    
    try:
        enriched_map = batch_download_and_enrich(
            s1_tickers, period="2y",
            progress_cb=lambda bi, bt, msg: _progress(
                "Stage 2 -- Batch Download",
                25 + int(bi / bt * 20),
                msg,
            ),
        )
    except Exception as e:
        logger.error("[Combined] Batch download failed: %s", e)
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    logger.info("[Timing] Batch download: %.1fs -> %d enriched records",
                _elapsed(_t2_dl), len(enriched_map))
    
    if _cancelled():
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    if not enriched_map:
        print("[ERROR] Batch download returned no data")
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    # ── STAGE 2-3: Run SEPA and QM in parallel threads ────────────────────
    _progress("Stage 2-3 -- Parallel Analysis", 50, "Running SEPA and QM analyses…")
    _t_parallel = _time.perf_counter()
    
    sepa_result = None
    qm_result = None
    sepa_error = None
    qm_error = None
    
    def _run_sepa():
        nonlocal sepa_result, sepa_error
        try:
            from modules.screener import run_stage2, run_stage3, _get_sector_leaders
            
            # Load sector info for SEPA
            sector_df = get_sector_rankings("Sector")
            sector_leaders = _get_sector_leaders(sector_df)
            
            # Stage 2 using pre-downloaded data
            s2_results = run_stage2(s1_tickers, sector_leaders, verbose=verbose,
                                   enriched_map=enriched_map, shared=True)
            
            if _cancelled() or not s2_results:
                sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            
            # Stage 3 scoring
            sepa_df_passed = run_stage3(s2_results, verbose=verbose, shared=True)
            sepa_df_all = pd.DataFrame(s2_results) if s2_results else pd.DataFrame()
            
            sepa_result = {
                "passed": sepa_df_passed,
                "all": sepa_df_all,
            }
        except Exception as e:
            logger.error("[Combined] SEPA process failed: %s", e)
            sepa_error = str(e)
            sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
    
    def _run_qm():
        nonlocal qm_result, qm_error
        try:
            from modules.qm_screener import run_qm_stage2, run_qm_stage3
            
            # Stage 2 using pre-downloaded data
            s2_passed = run_qm_stage2(s1_tickers, verbose=verbose,
                                      enriched_map=enriched_map, shared=True)
            
            if _cancelled() or not s2_passed:
                qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            
            # Stage 3 quality scoring
            qm_df_passed = run_qm_stage3(s2_passed, enriched_cache=enriched_map)
            if qm_df_passed is None or qm_df_passed.empty:
                qm_df_passed = pd.DataFrame()
            qm_df_all = pd.DataFrame(s2_passed) if s2_passed else pd.DataFrame()
            
            qm_result = {
                "passed": qm_df_passed,
                "all": qm_df_all,
            }
        except Exception as e:
            logger.error("[Combined] QM process failed: %s", e)
            qm_error = str(e)
            qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        sepa_thread = executor.submit(_run_sepa)
        qm_thread = executor.submit(_run_qm)
        
        # Wait for both to complete
        sepa_thread.result(timeout=600)  # 10 min timeout per thread
        qm_thread.result(timeout=600)
    
    parallel_elapsed = _elapsed(_t_parallel)
    logger.info("[Timing] Parallel Stage 2-3: %.1fs", parallel_elapsed)
    
    # ── Results assembly ──────────────────────────────────────────────────
    _progress("Finalizing results", 95, "Preparing output…")
    
    if sepa_error:
        print(f"[WARNING] SEPA analysis failed: {sepa_error}")
    if qm_error:
        print(f"[WARNING] QM analysis failed: {qm_error}")
    
    total_elapsed = _elapsed()
    
    sepa_final = {
        "passed": sepa_result["passed"] if sepa_result else pd.DataFrame(),
        "all": sepa_result.get("all", pd.DataFrame()) if sepa_result else pd.DataFrame(),
        "market_env": market_env,
        "timing": {
            "stage0": _elapsed(_t0),
            "stage1": _elapsed(_t1),
            "batch_download": _elapsed(_t2_dl),
            "parallel": parallel_elapsed,
            "total": total_elapsed,
        },
        "error": sepa_error,
    }
    
    qm_final = {
        "passed": qm_result["passed"] if qm_result else pd.DataFrame(),
        "all": qm_result.get("all", pd.DataFrame()) if qm_result else pd.DataFrame(),
        "market_env": market_env,
        "timing": {
            "stage0": _elapsed(_t0),
            "stage1": _elapsed(_t1),
            "batch_download": _elapsed(_t2_dl),
            "parallel": parallel_elapsed,
            "total": total_elapsed,
        },
        "error": qm_error,
    }
    
    _progress("Combined scan complete", 100, f"Done in {total_elapsed:.1f}s")
    
    if verbose:
        print(f"\n[Combined Scan] Complete in {total_elapsed:.1f}s")
        print(f"  SEPA: {len(sepa_final['passed'])} passed | "
              f"QM: {len(qm_final['passed'])} passed")
    
    logger.info("[Combined Scan] Complete: SEPA %d | QM %d | Total time %.1fs",
                len(sepa_final['passed']), len(qm_final['passed']), total_elapsed)
    
    return (sepa_final, qm_final)
