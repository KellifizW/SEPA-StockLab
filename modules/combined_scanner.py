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

def run_combined_scan(custom_filters: dict | None = None,
                      refresh_rs: bool = False,
                      verbose: bool = True,
                      stage1_source: str | None = None,
                      min_star: float | None = None,
                      top_n: int | None = None,
                      strict_rs: bool = False) -> Tuple[dict, dict]:
    """
    Run both SEPA and QM scans with shared Stage 0-3 infrastructure.

    Stage 1 runs SEPA and QM coarse filters IN PARALLEL, then unions the
    results so each method operates on its correct candidate universe.
    Stage 2 batch download happens ONCE on the union.  Stage 2-3 analysis
    for each method then runs in parallel threads.

    Parameters
    ----------
    custom_filters : override SEPA Stage 1 finviz filters (SEPA only)
    refresh_rs     : force rebuild of RS cache
    verbose        : print progress to console
    stage1_source  : 'finviz' | 'nasdaq_ftp' | None (uses C.STAGE1_SOURCE)
    min_star       : QM minimum star rating for df_passed (default: C.QM_MIN_STAR_DISPLAY)
    top_n          : QM result cap (default: C.QM_SCAN_TOP_N)
    strict_rs      : QM strict RS≥90 filter (Supplement 35)

    Returns: (sepa_result, qm_result) where each is a dict with:
      - 'passed': DataFrame of passed candidates
      - 'all': DataFrame of all Stage 2 candidates
      - 'market_env': market environment assessment
      - 'timing': dict with stage timings
      - 'error': error message if any
      - 'blocked': True if QM was blocked by bear market gate
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

    # ── QM bear market gate (mirrors run_qm_scan's QM_BLOCK_IN_BEAR logic) ─
    qm_blocked = False
    if getattr(C, "QM_BLOCK_IN_BEAR", True):
        regime = market_env.get("regime", "")
        if regime in ("DOWNTREND", "MARKET_IN_CORRECTION"):
            qm_blocked = True
            logger.warning("[Combined] QM blocked — bear market regime: %s", regime)
            if verbose:
                print(f"[QM] BLOCKED — bear market regime ({regime}), SEPA scan continues")
    
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
    
    # ── STAGE 1: Dual parallel coarse filters → union ticker universe ───────
    # SEPA uses stricter fundamental filters; QM uses price+volume only.
    # Running both in parallel then taking the union ensures each method
    # operates on its correct candidate universe without limiting the other.
    _progress("Stage 1 -- Dual Coarse Filter", 8, "Running SEPA + QM Stage 1 in parallel…")
    _t1 = _time.perf_counter()

    from modules.screener import run_stage1
    from modules.qm_screener import run_qm_stage1

    sepa_s1_tickers: list = []
    qm_s1_tickers: list = []
    _s1_sepa_error = None
    _s1_qm_error = None

    def _fetch_sepa_s1():
        nonlocal sepa_s1_tickers, _s1_sepa_error
        try:
            sepa_s1_tickers = run_stage1(custom_filters, verbose=verbose,
                                         stage1_source=stage1_source)
            logger.info("[Combined S1] SEPA: %d candidates", len(sepa_s1_tickers))
        except Exception as exc:
            _s1_sepa_error = str(exc)
            logger.error("[Combined S1] SEPA Stage 1 failed: %s", exc)

    def _fetch_qm_s1():
        nonlocal qm_s1_tickers, _s1_qm_error
        if qm_blocked:
            logger.info("[Combined S1] QM Stage 1 skipped (bear market blocked)")
            return
        try:
            qm_s1_tickers = run_qm_stage1(verbose=verbose,
                                           stage1_source=stage1_source)
            logger.info("[Combined S1] QM: %d candidates", len(qm_s1_tickers))
        except Exception as exc:
            _s1_qm_error = str(exc)
            logger.error("[Combined S1] QM Stage 1 failed: %s", exc)

    with ThreadPoolExecutor(max_workers=2) as _s1_pool:
        _sf = _s1_pool.submit(_fetch_sepa_s1)
        _qf = _s1_pool.submit(_fetch_qm_s1)
        _sf.result(timeout=1200)
        _qf.result(timeout=1200)

    if _s1_sepa_error and not sepa_s1_tickers:
        logger.error("[Combined] SEPA Stage 1 failed with no results — aborting")
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})

    # Union: QM gets its full universe; SEPA extras beyond QM also included
    union_set = set(sepa_s1_tickers) | set(qm_s1_tickers)
    s1_tickers = list(union_set)

    logger.info("[Timing] Stage 1: %.1fs | SEPA=%d QM=%d Union=%d",
                _elapsed(_t1), len(sepa_s1_tickers), len(qm_s1_tickers), len(s1_tickers))
    if verbose:
        print(f"[Stage 1] SEPA={len(sepa_s1_tickers)} | QM={len(qm_s1_tickers)} "
              f"| Union={len(s1_tickers)} unique tickers")

    _progress("Stage 1 -- Dual Coarse Filter", 20, f"{len(s1_tickers)} union candidates")

    if _cancelled():
        return ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    if not s1_tickers:
        print("[ERROR] Stage 1 returned no tickers from either method")
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

            # Stage 2 using pre-downloaded union data
            # TT1-TT10 will naturally filter out QM-only tickers lacking
            # Minervini fundamentals — no pre-filtering needed here
            s2_results = run_stage2(s1_tickers, sector_leaders, verbose=verbose,
                                    enriched_map=enriched_map, shared=True)

            if _cancelled() or not s2_results:
                sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return

            # Stage 3 scoring
            sepa_df_passed = run_stage3(s2_results, verbose=verbose, shared=True)
            # Strip "df" (the OHLCV DataFrame stored by run_stage2 for scoring).
            # Keeping it would embed DataFrames inside a column and cause
            # "truth value of a DataFrame is ambiguous" on any subsequent
            # df.notna() / df.where() call during JSON serialisation.
            _safe_s2 = [{k: v for k, v in r.items() if k != "df"}
                        for r in s2_results]
            sepa_df_all = pd.DataFrame(_safe_s2) if _safe_s2 else pd.DataFrame()

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

        # Bear market gate — skip entire QM analysis
        if qm_blocked:
            qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame(),
                         "blocked": True}
            return

        try:
            from modules.qm_screener import run_qm_stage2, run_qm_stage3

            # Stage 2 — QM uses the FULL union universe so QM-only tickers
            # (filtered out by SEPA's ROE/EPS requirements) are still evaluated
            s2_passed = run_qm_stage2(s1_tickers, verbose=verbose,
                                      enriched_map=enriched_map, shared=True)

            if _cancelled() or not s2_passed:
                qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return

            # Stage 3 quality scoring (returns all scored rows, capped at QM_SCAN_TOP_N)
            qm_df_all_scored = run_qm_stage3(s2_passed, enriched_cache=enriched_map)
            if qm_df_all_scored is None:
                qm_df_all_scored = pd.DataFrame()

            # Apply the same post-filters as run_qm_scan() ────────────────
            qm_df_passed = qm_df_all_scored.copy() if not qm_df_all_scored.empty else pd.DataFrame()

            # min_star filter
            _min_star = min_star if min_star is not None else getattr(C, "QM_MIN_STAR_DISPLAY", 3.0)
            if not qm_df_passed.empty and "qm_star" in qm_df_passed.columns:
                qm_df_passed = qm_df_passed[qm_df_passed["qm_star"] >= _min_star]

            # strict_rs filter (Supplement 35: only top RS stocks)
            if strict_rs and not qm_df_passed.empty and "rs_rank" in qm_df_passed.columns:
                rs_min = getattr(C, "QM_RS_STRICT_MIN_RANK", 90.0)
                before = len(qm_df_passed)
                qm_df_passed = qm_df_passed[qm_df_passed["rs_rank"] >= rs_min]
                logger.info("[Combined QM] strict_rs RS≥%.0f: %d→%d", rs_min, before, len(qm_df_passed))

            # top_n cap
            _top_n = top_n if top_n is not None else getattr(C, "QM_SCAN_TOP_N", 50)
            qm_df_passed = qm_df_passed.head(_top_n)

            qm_df_all = pd.DataFrame(s2_passed) if s2_passed else pd.DataFrame()

            qm_result = {
                "passed": qm_df_passed,
                "all": qm_df_all,
                "all_scored": qm_df_all_scored,
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
    
    _timing = {
        "stage0": _elapsed(_t0),
        "stage1": _elapsed(_t1),
        "batch_download": _elapsed(_t2_dl),
        "parallel": parallel_elapsed,
        "total": total_elapsed,
        "sepa_s1_count": len(sepa_s1_tickers),
        "qm_s1_count":   len(qm_s1_tickers),
        "union_count":   len(s1_tickers),
    }

    sepa_final = {
        "passed": sepa_result["passed"] if sepa_result else pd.DataFrame(),
        "all": sepa_result.get("all", pd.DataFrame()) if sepa_result else pd.DataFrame(),
        "market_env": market_env,
        "timing": _timing,
        "error": sepa_error,
    }

    qm_final = {
        "passed": qm_result["passed"] if qm_result else pd.DataFrame(),
        "all": qm_result.get("all", pd.DataFrame()) if qm_result else pd.DataFrame(),
        "all_scored": qm_result.get("all_scored", pd.DataFrame()) if qm_result else pd.DataFrame(),
        "market_env": market_env,
        "timing": _timing,
        "error": qm_error,
        "blocked": qm_blocked,
    }
    
    _progress("Combined scan complete", 100, f"Done in {total_elapsed:.1f}s")
    
    if verbose:
        print(f"\n[Combined Scan] Complete in {total_elapsed:.1f}s")
        print(f"  Stage 1: SEPA={len(sepa_s1_tickers)} | QM={len(qm_s1_tickers)} "
              f"| Union={len(s1_tickers)}")
        qm_status = "BLOCKED (bear)" if qm_blocked else f"{len(qm_final['passed'])} passed"
        print(f"  SEPA: {len(sepa_final['passed'])} passed | QM: {qm_status}")

    logger.info("[Combined Scan] Complete: SEPA=%d | QM=%s | Total=%.1fs | "
                "S1 SEPA=%d QM=%d Union=%d",
                len(sepa_final['passed']),
                "BLOCKED" if qm_blocked else str(len(qm_final['passed'])),
                total_elapsed,
                len(sepa_s1_tickers), len(qm_s1_tickers), len(s1_tickers))
    
    return (sepa_final, qm_final)
