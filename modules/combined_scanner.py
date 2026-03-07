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

# ── ANSI colours for terminal output ──────────────────────────────────────────
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_RESET  = "\033[0m"

# ─── Combined scan progress / cancel (module-level) ──────────────────────────
_combined_lock      = threading.Lock()
_combined_cancel    = threading.Event()
_combined_progress  = {"stage": "idle", "pct": 0, "msg": "", "ticker": "", "log_lines": []}
_combined_log_lines = []  # Keep running log of all messages

def set_combined_cancel(event: threading.Event):
    """Register external cancel event from app.py."""
    global _combined_cancel
    _combined_cancel = event

def get_combined_progress() -> dict:
    with _combined_lock:
        return dict(_combined_progress)

def _progress(stage: str, pct: int, msg: str = "", ticker: str = ""):
    """Update combined scan progress with complete logging."""
    import datetime
    with _combined_lock:
        # Build complete log line with timestamp
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if ticker:
            log_line = f"[{ts}] [{stage}] {ticker}: {msg}"
        else:
            log_line = f"[{ts}] [{stage}] {msg}"
        
        # Append to running log (keep last 200 lines to avoid unbounded growth)
        _combined_log_lines.append(log_line)
        if len(_combined_log_lines) > 200:
            _combined_log_lines.pop(0)
        
        # Update progress dict with recent log lines (last 50) for UI
        _combined_progress.update({
            "stage": stage,
            "pct": pct,
            "msg": msg,
            "ticker": ticker,
            "log_lines": list(_combined_log_lines[-50:])  # Last 50 lines for UI
        })

def _log_detail(stage: str, detail: str, ticker: str = "", indent: bool = True):
    """Add detailed log line (for detailed progress within a stage)."""
    import datetime
    with _combined_lock:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        # Use indent for sub-messages
        indent_str = "  " if indent else ""
        if ticker:
            log_line = f"[{ts}] [{stage}] {indent_str}• {ticker}: {detail}"
        else:
            log_line = f"[{ts}] [{stage}] {indent_str}• {detail}"
        
        _combined_log_lines.append(log_line)
        if len(_combined_log_lines) > 200:
            _combined_log_lines.pop(0)
        
        # Update log lines in progress for UI
        _combined_progress.update({
            "log_lines": list(_combined_log_lines[-50:])
        })

def _cancelled() -> bool:
    return _combined_cancel.is_set()


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1.5 — Technical pre-filters (no network calls, pure local computation)
#
# These filters are applied BEFORE the expensive per-ticker yfinance queries.
# They use data already available from Stage 1 (NASDAQ FTP cache: close, avg_vol)
# and from the Stage 2 batch OHLCV download (enriched with SMA/EMA/RSI/ATR).
#
# Goal: Reduce the number of tickers entering Stage 2-3 analysis, which in turn
# reduces get_fundamentals() and get_next_earnings_date() yfinance API calls.
# ═══════════════════════════════════════════════════════════════════════════════

def _prefilter_dollar_volume(sepa_tickers: list, qm_tickers: list,
                              ml_tickers: list | None = None,
                              verbose: bool = True) -> tuple[list, list, list]:
    """
    Stage 1.5A — Pre-filter using NASDAQ FTP cached close × avg_vol data.

    Applied BEFORE the batch OHLCV download to reduce its scope.
    Uses no additional network calls — reads from in-memory/file cache
    that was populated during Stage 1.

    Filters:
      - QM: dollar_vol (close × avg_vol) ≥ QM_SCAN_MIN_DOLLAR_VOL ($5M default)
             This is the QM Stage 2 gate — applied early to avoid downloading
             2-year OHLCV for stocks that will certainly fail.
      - SEPA: dollar_vol ≥ SEPA_MIN_DOLLAR_VOL ($2M default)
              Minervini requires institutional participation; very low dollar
              volume stocks lack institutional interest.
      - ML: dollar_vol ≥ ML_MIN_DOLLAR_VOLUME ($5M default)
            Martin Luk requires institutional-level liquidity.

    Returns:
        (filtered_sepa_tickers, filtered_qm_tickers, filtered_ml_tickers)
    """
    if ml_tickers is None:
        ml_tickers = []

    try:
        from modules.nasdaq_universe import get_cached_ticker_data
        cache = get_cached_ticker_data()
    except Exception as exc:
        logger.warning("[Stage 1.5A] Could not load NASDAQ FTP cache: %s", exc)
        return sepa_tickers, qm_tickers, ml_tickers

    if not cache:
        logger.info("[Stage 1.5A] No cached data available — skipping dollar volume pre-filter")
        return sepa_tickers, qm_tickers, ml_tickers

    qm_min_dv   = getattr(C, "QM_SCAN_MIN_DOLLAR_VOL", 5_000_000)
    sepa_min_dv  = getattr(C, "SEPA_MIN_DOLLAR_VOL", 2_000_000)
    ml_min_dv    = getattr(C, "ML_MIN_DOLLAR_VOLUME", 5_000_000)

    def _dollar_vol(ticker: str) -> float:
        row = cache.get(ticker)
        if not row:
            return float("inf")  # Unknown → keep (don't penalise missing data)
        return row.get("close", 0) * row.get("avg_vol", 0)

    sepa_before = len(sepa_tickers)
    qm_before   = len(qm_tickers)
    ml_before   = len(ml_tickers)

    sepa_filtered = [t for t in sepa_tickers if _dollar_vol(t) >= sepa_min_dv]
    qm_filtered   = [t for t in qm_tickers   if _dollar_vol(t) >= qm_min_dv]
    ml_filtered   = [t for t in ml_tickers    if _dollar_vol(t) >= ml_min_dv]

    sepa_removed = sepa_before - len(sepa_filtered)
    qm_removed   = qm_before  - len(qm_filtered)
    ml_removed   = ml_before   - len(ml_filtered)

    if verbose and (sepa_removed or qm_removed or ml_removed):
        logger.info(
            "[Stage 1.5A] Dollar volume pre-filter: "
            "SEPA %d→%d (−%d, min $%.0fM) | QM %d→%d (−%d, min $%.0fM) | ML %d→%d (−%d, min $%.0fM)",
            sepa_before, len(sepa_filtered), sepa_removed, sepa_min_dv / 1e6,
            qm_before, len(qm_filtered), qm_removed, qm_min_dv / 1e6,
            ml_before, len(ml_filtered), ml_removed, ml_min_dv / 1e6,
        )
    return sepa_filtered, qm_filtered, ml_filtered


def _prefilter_technical(sepa_tickers: list, qm_tickers: list,
                          ml_tickers: list | None = None,
                          enriched_map: dict = None,
                          verbose: bool = True) -> tuple[list, list, list]:
    """
    Stage 1.5B — Fast technical pre-screen using enriched OHLCV data.

    Applied AFTER the batch download (enriched_map is available) but BEFORE
    the full Stage 2-3 parallel analysis.  Uses only locally-computed
    indicators from the batch download — zero additional network calls.

    SEPA pre-screen (will fail TT1-TT8 if any of these fail):
      • Price > SMA200 (TT2)
      • Price > SMA150 (TT1)
      • Price > SMA50  (TT6)
      • SMA50 > SMA200 (partial TT5)

    QM pre-screen (will fail QM Stage 2 gate if any of these fail):
      • ADR ≥ QM_MIN_ADR_PCT (hard veto)
      • 1M momentum > 0 (all QM momentum thresholds are positive)
      • Price > SMA50 (Supplement 14: hard penalty, effectively a gate)

    ML pre-screen (will fail ML Stage 2 gate if any of these fail):
      • Price > EMA50 (MartinLukCore Ch3: not in breakdown)
      • EMA21 or EMA50 must be rising (at least one)

    Returns:
        (filtered_sepa_tickers, filtered_qm_tickers, filtered_ml_tickers)
    """
    import numpy as np

    if ml_tickers is None:
        ml_tickers = []
    if enriched_map is None:
        enriched_map = {}

    def _safe_float(series_or_val, idx=-1) -> float | None:
        """Safely extract a float from a Series or scalar."""
        try:
            if isinstance(series_or_val, pd.Series):
                val = series_or_val.iloc[idx]
            else:
                val = series_or_val
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return None
            return float(val)
        except Exception:
            return None

    def _passes_sepa(ticker: str) -> bool:
        df = enriched_map.get(ticker)
        if df is None or df.empty or len(df) < 200:
            return False
        last = df.iloc[-1]
        close = _safe_float(last.get("Close"))
        if close is None:
            return False

        sma50  = _safe_float(last.get("SMA_50"))
        sma150 = _safe_float(last.get("SMA_150"))
        sma200 = _safe_float(last.get("SMA_200"))

        # TT2: Price > SMA200
        if sma200 and close <= sma200:
            return False
        # TT1: Price > SMA150
        if sma150 and close <= sma150:
            return False
        # TT6: Price > SMA50
        if sma50 and close <= sma50:
            return False
        # Partial TT5: SMA50 > SMA200
        if sma50 and sma200 and sma50 <= sma200:
            return False
        return True

    def _passes_qm(ticker: str) -> bool:
        df = enriched_map.get(ticker)
        if df is None or df.empty or len(df) < 30:
            return False
        last = df.iloc[-1]
        close = _safe_float(last.get("Close"))
        if close is None:
            return False

        # ADR gate (estimated from ATR_14 / close)
        atr14 = _safe_float(last.get("ATRr_14")) or _safe_float(last.get("ATR_14"))
        if atr14:
            adr_pct = (atr14 / close * 100) if close > 0 else 0
            min_adr = getattr(C, "QM_MIN_ADR_PCT", 5.0)
            if adr_pct < min_adr:
                return False

        # 1M momentum positive (basic direction check — QM requires 1M≥25%)
        if len(df) > 22:
            past = _safe_float(df["Close"].iloc[-23])
            if past and past > 0:
                mom_1m = (close / past - 1.0) * 100
                if mom_1m < 0:
                    return False  # Negative 1M return → won't pass QM momentum gate

        # Price > SMA50 (Supplement 14)
        sma50 = _safe_float(last.get("SMA_50"))
        if sma50 and close < sma50:
            return False

        return True

    def _passes_ml(ticker: str) -> bool:
        """ML pre-screen: Price > EMA50, at least EMA21 or EMA50 rising."""
        df = enriched_map.get(ticker)
        if df is None or df.empty or len(df) < 50:
            return False
        last = df.iloc[-1]
        close = _safe_float(last.get("Close"))
        if close is None:
            return False

        # Price must be above EMA50 (Not in breakdown — MartinLukCore Ch3)
        ema50 = _safe_float(last.get("EMA_50"))
        if ema50 and close < ema50:
            return False

        # At least EMA21 or EMA50 must be rising (compared to 5 bars ago)
        ema21_curr = _safe_float(last.get("EMA_21"))
        ema21_prev = _safe_float(df["EMA_21"].iloc[-6]) if "EMA_21" in df.columns and len(df) > 5 else None
        ema50_curr = _safe_float(last.get("EMA_50"))
        ema50_prev = _safe_float(df["EMA_50"].iloc[-6]) if "EMA_50" in df.columns and len(df) > 5 else None
        ema21_rising = ema21_prev is not None and ema21_curr is not None and ema21_curr > ema21_prev
        ema50_rising = ema50_prev is not None and ema50_curr is not None and ema50_curr > ema50_prev
        if not (ema21_rising or ema50_rising):
            return False

        return True

    sepa_before = len(sepa_tickers)
    qm_before   = len(qm_tickers)
    ml_before   = len(ml_tickers)

    sepa_filtered = [t for t in sepa_tickers if _passes_sepa(t)]
    qm_filtered   = [t for t in qm_tickers   if _passes_qm(t)]
    ml_filtered   = [t for t in ml_tickers    if _passes_ml(t)]

    sepa_removed = sepa_before - len(sepa_filtered)
    qm_removed   = qm_before  - len(qm_filtered)
    ml_removed   = ml_before   - len(ml_filtered)

    if verbose:
        logger.info(
            "[Stage 1.5B] Technical pre-screen: "
            "SEPA %d→%d (−%d, SMA alignment) | QM %d→%d (−%d, ADR/momentum/SMA50) | "
            "ML %d→%d (−%d, EMA structure)",
            sepa_before, len(sepa_filtered), sepa_removed,
            qm_before, len(qm_filtered), qm_removed,
            ml_before, len(ml_filtered), ml_removed,
        )
    return sepa_filtered, qm_filtered, ml_filtered


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED SCANNING ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def run_combined_scan(custom_filters: dict | None = None,
                      refresh_rs: bool = False,
                      verbose: bool = True,
                      stage1_source: str | None = None,
                      min_star: float | None = None,
                      top_n: int | None = None,
                      strict_rs: bool = False,
                      include_ml: bool = True,
                      ml_min_star: float | None = None) -> Tuple[dict, dict, dict]:
    """
    Run SEPA, QM, and ML scans with shared Stage 0-3 infrastructure.

    Stage 1 runs SEPA, QM, and ML coarse filters IN PARALLEL, then unions
    the results so each method operates on its correct candidate universe.
    Stage 2 batch download happens ONCE on the union.  Stage 2-3 analysis
    for each method then runs in parallel threads.

    Parameters
    ----------
    custom_filters : override SEPA Stage 1 finviz filters (SEPA only)
    refresh_rs     : force rebuild of RS cache
    verbose        : print progress to console
    stage1_source  : 'finviz' | 'nasdaq_ftp' | None (uses C.STAGE1_SOURCE)
    min_star       : QM minimum star rating for df_passed (default: C.QM_SCAN_MIN_STAR=3.0)
    top_n          : QM result cap (default: C.QM_SCAN_TOP_N)
    strict_rs      : QM strict RS≥90 filter (Supplement 35)
    include_ml     : include Martin Luk scan in combined scan
    ml_min_star    : ML minimum star rating for df_passed (default: C.ML_COMBINED_MIN_STAR=3.0)

    Returns: (sepa_result, qm_result, ml_result) where each is a dict with:
      - 'passed': DataFrame of passed candidates
      - 'all': DataFrame of all Stage 2 candidates
      - 'market_env': market environment assessment
      - 'timing': dict with stage timings
      - 'error': error message if any
      - 'blocked': True if strategy was blocked by bear market gate
    """
    _t_combined_start = _time.perf_counter()
    
    def _elapsed(since=None):
        return _time.perf_counter() - (since if since is not None else _t_combined_start)
    
    _progress("Starting combined scan...", 0, "Initialising")
    _combined_cancel.clear()
    
    _empty3 = ({"passed": pd.DataFrame()}, {"passed": pd.DataFrame()}, {"passed": pd.DataFrame()})
    
    print("\n" + "=" * 70)
    print("  COMBINED SEPA + QM + ML SCAN  —  Unified Daily Review")
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

    # ── ML bear market gate (mirrors run_ml_scan's ML_BLOCK_IN_BEAR logic) ─
    ml_blocked = False
    if not include_ml:
        ml_blocked = True
        logger.info("[Combined] ML excluded by include_ml=False")
    elif getattr(C, "ML_BLOCK_IN_BEAR", True):
        regime = market_env.get("regime", "")
        if regime == "DOWNTREND":
            ml_blocked = True
            logger.warning("[Combined] ML blocked — bear market regime: %s", regime)
            if verbose:
                print(f"[ML] BLOCKED — bear market regime ({regime})")
    
    # ── STAGE 0B: RS Rankings (loaded once for all methods) ────────────────
    _progress("Stage 0 -- RS Rankings", 5, "Building / loading RS cache…")
    _t0b = _time.perf_counter()
    try:
        _ensure_rs_loaded(force_refresh=refresh_rs)
    except Exception as e:
        logger.warning("[Combined] RS load failed: %s", e)
    logger.info("[Timing] RS load: %.1fs", _elapsed(_t0b))
    
    if _cancelled():
        return _empty3
    
    # ── STAGE 1: Triple parallel coarse filters → union ticker universe ───
    # SEPA uses stricter fundamental filters; QM uses price+volume only;
    # ML uses the widest net (price ≥ $5, vol ≥ 300K).
    # Running all three in parallel then taking the union ensures each method
    # operates on its correct candidate universe without limiting the others.
    _progress("Stage 1 -- Triple Coarse Filter", 8, "Running SEPA + QM + ML Stage 1 in parallel…")
    _t1 = _time.perf_counter()

    from modules.screener import run_stage1
    from modules.qm_screener import run_qm_stage1

    sepa_s1_tickers: list = []
    qm_s1_tickers: list = []
    ml_s1_tickers: list = []
    _s1_sepa_error = None
    _s1_qm_error = None
    _s1_ml_error = None

    def _fetch_sepa_s1():
        nonlocal sepa_s1_tickers, _s1_sepa_error
        try:
            _log_detail("Stage 1 -- Triple Coarse Filter", "SEPA Stage 1 starting")
            sepa_s1_tickers = run_stage1(custom_filters, verbose=verbose,
                                         stage1_source=stage1_source)
            logger.info("[Combined S1] SEPA: %d candidates", len(sepa_s1_tickers))
            _log_detail("Stage 1 -- Triple Coarse Filter", f"SEPA Stage 1 completed: {len(sepa_s1_tickers)} candidates")
        except Exception as exc:
            _s1_sepa_error = str(exc)
            logger.error("[Combined S1] SEPA Stage 1 failed: %s", exc)
            _log_detail("Stage 1 -- Triple Coarse Filter", f"SEPA Stage 1 error: {type(exc).__name__}")

    def _fetch_qm_s1():
        nonlocal qm_s1_tickers, _s1_qm_error
        if qm_blocked:
            logger.info("[Combined S1] QM Stage 1 skipped (bear market blocked)")
            _log_detail("Stage 1 -- Triple Coarse Filter", "QM Stage 1 skipped (bear market)")
            return
        try:
            _log_detail("Stage 1 -- Triple Coarse Filter", "QM Stage 1 starting")
            qm_s1_tickers = run_qm_stage1(verbose=verbose,
                                           stage1_source=stage1_source)
            logger.info("[Combined S1] QM: %d candidates", len(qm_s1_tickers))
            _log_detail("Stage 1 -- Triple Coarse Filter", f"QM Stage 1 completed: {len(qm_s1_tickers)} candidates")
        except Exception as exc:
            _s1_qm_error = str(exc)
            logger.error("[Combined S1] QM Stage 1 failed: %s", exc)
            _log_detail("Stage 1 -- Triple Coarse Filter", f"QM Stage 1 error: {type(exc).__name__}")

    def _fetch_ml_s1():
        nonlocal ml_s1_tickers, _s1_ml_error
        if ml_blocked:
            logger.info("[Combined S1] ML Stage 1 skipped (blocked)")
            _log_detail("Stage 1 -- Triple Coarse Filter", "ML Stage 1 skipped (blocked)")
            return
        try:
            from modules.ml_screener import run_ml_stage1
            _log_detail("Stage 1 -- Triple Coarse Filter", "ML Stage 1 starting")
            ml_s1_tickers = run_ml_stage1(verbose=verbose,
                                          stage1_source=stage1_source)
            logger.info("[Combined S1] ML: %d candidates", len(ml_s1_tickers))
            _log_detail("Stage 1 -- Triple Coarse Filter", f"ML Stage 1 completed: {len(ml_s1_tickers)} candidates")
        except Exception as exc:
            _s1_ml_error = str(exc)
            logger.error("[Combined S1] ML Stage 1 failed: %s", exc)
            _log_detail("Stage 1 -- Triple Coarse Filter", f"ML Stage 1 error: {type(exc).__name__}")

    with ThreadPoolExecutor(max_workers=3) as _s1_pool:
        _sf = _s1_pool.submit(_fetch_sepa_s1)
        _qf = _s1_pool.submit(_fetch_qm_s1)
        _mf = _s1_pool.submit(_fetch_ml_s1)
        _sf.result(timeout=1200)
        _qf.result(timeout=1200)
        _mf.result(timeout=1200)

    if _s1_sepa_error and not sepa_s1_tickers:
        logger.error("[Combined] SEPA Stage 1 failed with no results — aborting")
        return _empty3

    # Union: each method gets its full universe; union is used for batch download
    union_set = set(sepa_s1_tickers) | set(qm_s1_tickers) | set(ml_s1_tickers)
    s1_tickers = list(union_set)

    logger.info("[Timing] Stage 1: %.1fs | SEPA=%d QM=%d ML=%d Union=%d",
                _elapsed(_t1), len(sepa_s1_tickers), len(qm_s1_tickers),
                len(ml_s1_tickers), len(s1_tickers))
    if verbose:
        print(f"[Stage 1] SEPA={len(sepa_s1_tickers)} | QM={len(qm_s1_tickers)} "
              f"| ML={len(ml_s1_tickers)} | Union={len(s1_tickers)} unique tickers")

    _log_detail("Stage 1 -- Triple Coarse Filter",
                f"Union: {len(sepa_s1_tickers)} SEPA + {len(qm_s1_tickers)} QM + {len(ml_s1_tickers)} ML = {len(s1_tickers)} total")
    _progress("Stage 1 -- Triple Coarse Filter", 20, f"{len(s1_tickers)} union candidates")

    if _cancelled():
        return _empty3
    if not s1_tickers:
        print("[ERROR] Stage 1 returned no tickers from any method")
        return _empty3

    # ── STAGE 1.5A: Dollar volume pre-filter (before batch download) ─────
    # Use NASDAQ FTP cache data (close × avg_vol) to eliminate stocks with
    # insufficient dollar volume BEFORE downloading 2-year OHLCV for them.
    # This is a cheap local computation that saves significant download time.
    _progress("Stage 1.5 -- Pre-filter", 21, "Applying dollar volume gate…")
    _t15 = _time.perf_counter()

    sepa_pre = list(sepa_s1_tickers)  # Keep original lists for timing report
    qm_pre   = list(qm_s1_tickers)
    ml_pre   = list(ml_s1_tickers)
    sepa_pre, qm_pre, ml_pre = _prefilter_dollar_volume(sepa_pre, qm_pre, ml_pre, verbose=verbose)

    # Recompute the union with filtered lists — this is what gets batch-downloaded
    union_filtered = set(sepa_pre) | set(qm_pre) | set(ml_pre)
    s1_tickers_filtered = list(union_filtered)

    s15a_removed = len(s1_tickers) - len(s1_tickers_filtered)
    logger.info(
        "[Stage 1.5A] Dollar volume pre-filter: %d → %d union tickers (−%d, %.0f%%)",
        len(s1_tickers), len(s1_tickers_filtered), s15a_removed,
        s15a_removed / max(len(s1_tickers), 1) * 100,
    )
    if verbose and s15a_removed > 0:
        print(f"[Stage 1.5A] Dollar volume gate: {len(s1_tickers)}→{len(s1_tickers_filtered)} "
              f"(−{s15a_removed} low $Vol tickers)")

    _log_detail("Stage 1.5 -- Pre-filter",
                f"Dollar volume: {len(s1_tickers)}→{len(s1_tickers_filtered)} (−{s15a_removed})")
    _progress("Stage 1.5 -- Pre-filter", 22,
              f"Dollar volume: {len(s1_tickers)}→{len(s1_tickers_filtered)}")
    
    # ── STAGE 2: Batch download (once for both methods) ──────────────────
    _progress("Stage 2 -- Batch Download", 25, "Downloading price history…")
    _log_detail("Stage 2 -- Batch Download", f"Loading {len(s1_tickers_filtered)} tickers from cache")
    _t2_dl = _time.perf_counter()
    
    try:
        # Monotonic progress: prevent regression when batch_download_and_enrich
        # switches from cache phase (scale 0–1) to download phase (scale 1–N).
        _dl_max_pct = [25]
        def _combined_batch_cb(bi: int, bt: int, msg: str = "") -> None:
            pct = max(_dl_max_pct[0], min(45, 25 + int(bi / max(bt, 1) * 20)))
            _dl_max_pct[0] = pct
            # Add detailed log for every 5 batches
            if bi % 5 == 0 or bi == bt:
                _log_detail("Stage 2 -- Batch Download", f"Progress: {bi}/{bt} batches", indent=False)
            _progress("Stage 2 -- Batch Download", pct, msg)
            _dl_max_pct[0] = pct
            _progress("Stage 2 -- Batch Download", pct, msg)
        enriched_map = batch_download_and_enrich(
            s1_tickers_filtered, period="2y",
            progress_cb=_combined_batch_cb,
        )
    except Exception as e:
        logger.error("[Combined] Batch download failed: %s", e)
        return _empty3
    
    logger.info("[Timing] Batch download: %.1fs -> %d enriched records",
                _elapsed(_t2_dl), len(enriched_map))
    
    if _cancelled():
        return _empty3
    if not enriched_map:
        print("[ERROR] Batch download returned no data")
        return _empty3

    # ── STAGE 1.5B: Technical pre-screen (after batch download) ──────────
    # Use the enriched OHLCV data (SMA50/150/200, ATR, momentum) to
    # aggressively pre-filter tickers BEFORE the full Stage 2-3 analysis.
    # This is a fast local computation — no additional network calls.
    _progress("Stage 1.5B -- Technical Screen", 47, "Quick technical pre-screen…")
    _t15b = _time.perf_counter()

    sepa_screened, qm_screened, ml_screened = _prefilter_technical(
        sepa_pre, qm_pre, ml_pre, enriched_map, verbose=verbose
    )

    s15b_sepa_removed = len(sepa_pre) - len(sepa_screened)
    s15b_qm_removed   = len(qm_pre)  - len(qm_screened)
    s15b_ml_removed   = len(ml_pre)   - len(ml_screened)
    total_pre_screened = len(set(sepa_screened) | set(qm_screened) | set(ml_screened))

    logger.info(
        "[Stage 1.5B] Technical pre-screen in %.1fs: "
        "SEPA %d→%d (−%d) | QM %d→%d (−%d) | ML %d→%d (−%d) | analysis set=%d",
        _elapsed(_t15b),
        len(sepa_pre), len(sepa_screened), s15b_sepa_removed,
        len(qm_pre), len(qm_screened), s15b_qm_removed,
        len(ml_pre), len(ml_screened), s15b_ml_removed,
        total_pre_screened,
    )
    if verbose:
        print(f"[Stage 1.5B] Technical screen: "
              f"SEPA {len(sepa_pre)}→{len(sepa_screened)} | "
              f"QM {len(qm_pre)}→{len(qm_screened)} | "
              f"ML {len(ml_pre)}→{len(ml_screened)} | "
              f"Analysis set: {total_pre_screened}")

    _log_detail("Stage 1.5B -- Technical Screen",
                f"SEPA {len(sepa_pre)}→{len(sepa_screened)} | QM {len(qm_pre)}→{len(qm_screened)} | ML {len(ml_pre)}→{len(ml_screened)}")
    _progress("Stage 1.5B -- Technical Screen", 48,
              f"Analysis set: {total_pre_screened} tickers")

    # ── STAGE 2-3: Run SEPA, QM, and ML in parallel threads ──────────────
    # Each strategy receives ONLY its own pre-screened ticker list, not the
    # full union.  This avoids wasted TT validation on QM/ML-only tickers and
    # wasted QM gate checks on SEPA-only tickers.
    _progress("Stage 2-3 -- Parallel Analysis", 50, "Running SEPA, QM, and ML analyses…")
    _log_detail("Stage 2-3 -- Parallel Analysis",
                f"Starting parallel: SEPA={len(sepa_screened)} QM={len(qm_screened)} ML={len(ml_screened)}")
    _t_parallel = _time.perf_counter()
    
    sepa_result = None
    qm_result = None
    ml_result = None
    sepa_error = None
    qm_error = None
    ml_error = None
    
    def _run_sepa():
        nonlocal sepa_result, sepa_error
        try:
            from modules.screener import run_stage2, run_stage3, _get_sector_leaders

            # Load sector info for SEPA
            _log_detail("Stage 2-3 -- Parallel Analysis", "SEPA: Loading sector info")
            sector_df = get_sector_rankings("Sector")
            sector_leaders = _get_sector_leaders(sector_df)

            # Stage 2 using pre-downloaded union data
            # Only SEPA-screened tickers are sent — QM-only tickers that can't
            # pass TT alignment have already been removed by Stage 1.5B.
            logger.info("[Combined SEPA] Starting Stage 2 with %d tickers", len(sepa_screened))
            _log_detail("Stage 2-3 -- Parallel Analysis", "SEPA: Stage 2 (Trend Template) starting", indent=False)
            s2_results = run_stage2(sepa_screened, sector_leaders, verbose=verbose,
                                    enriched_map=enriched_map, shared=True)
            logger.info("[Combined SEPA] Stage 2 completed: %d passing TT1-TT10", len(s2_results))
            _log_detail("Stage 2-3 -- Parallel Analysis", f"SEPA: Stage 2 done: {len(s2_results)} passed TT1-TT10")

            # Check if s2_results is empty (list or DataFrame)
            if _cancelled():
                sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            is_empty = (isinstance(s2_results, list) and len(s2_results) == 0) or \
                       (isinstance(s2_results, pd.DataFrame) and s2_results.empty)
            if is_empty:
                logger.warning("[Combined SEPA] Stage 2 returned no results")
                _log_detail("Stage 2-3 -- Parallel Analysis", "SEPA: Stage 2 returned no results")
                sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return

            # Stage 3 scoring
            logger.info("[Combined SEPA] Starting Stage 3 scoring")
            _log_detail("Stage 2-3 -- Parallel Analysis", "SEPA: Stage 3 (5-Pillar Scoring) starting", indent=False)
            sepa_df_passed = run_stage3(s2_results, verbose=verbose, shared=True)
            logger.info("[Combined SEPA] Stage 3 completed: %d passing final score", len(sepa_df_passed))
            _log_detail("Stage 2-3 -- Parallel Analysis", f"SEPA: Stage 3 done: {len(sepa_df_passed)} passed scoring")
            
            # Strip "df" (the OHLCV DataFrame stored by run_stage2 for scoring).
            # Keeping it would embed DataFrames inside a column and cause
            # "truth value of a DataFrame is ambiguous" on any subsequent
            # df.notna() / df.where() call during JSON serialisation.
            logger.debug("[Combined SEPA] Stripping 'df' column from %d s2_results", len(s2_results))
            _safe_s2 = [{k: v for k, v in r.items() if k != "df"}
                        for r in s2_results]
            sepa_df_all = pd.DataFrame(_safe_s2) if _safe_s2 else pd.DataFrame()
            logger.info("[Combined SEPA] Assembled all_scored DataFrame: shape %s", sepa_df_all.shape)

            sepa_result = {
                "passed": sepa_df_passed,
                "all": sepa_df_all,
            }
        except Exception as e:
            logger.error("[Combined] SEPA process failed: %s", e, exc_info=True)
            sepa_error = str(e)
            _log_detail("Stage 2-3 -- Parallel Analysis", f"SEPA error: {type(e).__name__}")
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

            logger.info("[Combined QM] Starting Stage 2 with %d tickers", len(qm_screened))
            _log_detail("Stage 2-3 -- Parallel Analysis", "QM: Stage 2 (ADR + Momentum) starting", indent=False)
            # Pass only the QM subset so Stage 2 iterates the intended universe.
            # This avoids scanning the full union map and reduces API pressure.
            qm_enriched = {t: enriched_map[t] for t in qm_screened if t in enriched_map}
            # Stage 2 — QM uses its own pre-screened tickers.  Stage 1.5B already
            # eliminated stocks with insufficient ADR and negative momentum.
            s2_passed = run_qm_stage2(qm_screened, verbose=verbose,
                                      enriched_map=qm_enriched, shared=True)
            logger.info("[Combined QM] Stage 2 completed: %d passing gate", len(s2_passed))
            _log_detail("Stage 2-3 -- Parallel Analysis", f"QM: Stage 2 done: {len(s2_passed)} passed gate")

            # Check if s2_passed is empty (list or DataFrame)
            if _cancelled():
                qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            is_empty = (isinstance(s2_passed, list) and len(s2_passed) == 0) or \
                       (isinstance(s2_passed, pd.DataFrame) and s2_passed.empty)
            if is_empty:
                logger.warning("[Combined QM] Stage 2 returned no results")
                _log_detail("Stage 2-3 -- Parallel Analysis", "QM: Stage 2 returned no results")
                qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return

            # Stage 3 quality scoring (returns all scored rows, capped at QM_SCAN_TOP_N)
            logger.info("[Combined QM] Starting Stage 3 scoring with %d candidates", len(s2_passed))
            _log_detail("Stage 2-3 -- Parallel Analysis", "QM: Stage 3 (6-Dimension Star Rating) starting", indent=False)
            qm_df_all_scored = run_qm_stage3(s2_passed, enriched_cache=qm_enriched)
            if qm_df_all_scored is None:
                logger.warning("[Combined QM] Stage 3 returned None")
                _log_detail("Stage 2-3 -- Parallel Analysis", "QM: Stage 3 returned None")
                qm_df_all_scored = pd.DataFrame()
            elif qm_df_all_scored.empty:
                logger.warning("[Combined QM] Stage 3 returned empty DataFrame")
                _log_detail("Stage 2-3 -- Parallel Analysis", "QM: Stage 3 returned empty")
            else:
                logger.info("[Combined QM] Stage 3 completed: shape %s", qm_df_all_scored.shape)
                _log_detail("Stage 2-3 -- Parallel Analysis", f"QM: Stage 3 done: {len(qm_df_all_scored)} scored")

            # Apply the same post-filters as run_qm_scan() ────────────────
            qm_df_passed = qm_df_all_scored.copy() if not qm_df_all_scored.empty else pd.DataFrame()

            # min_star filter
            # NOTE: Combined scan defaults to QM_SCAN_MIN_STAR (3.0) so the full
            # ≥3★ pool is returned to the frontend for interactive client-side
            # filtering without re-running the scan.
            _min_star = min_star if min_star is not None else getattr(C, "QM_SCAN_MIN_STAR", 3.0)
            if not qm_df_passed.empty and "qm_star" in qm_df_passed.columns:
                before = len(qm_df_passed)
                qm_df_passed = qm_df_passed[qm_df_passed["qm_star"] >= _min_star]
                _log_detail("Stage 2-3 -- Parallel Analysis", f"QM: min_star≥{_min_star}: {before}→{len(qm_df_passed)}")

            # strict_rs filter (Supplement 35: only top RS stocks)
            if strict_rs and not qm_df_passed.empty and "rs_rank" in qm_df_passed.columns:
                rs_min = getattr(C, "QM_RS_STRICT_MIN_RANK", 90.0)
                before = len(qm_df_passed)
                qm_df_passed = qm_df_passed[qm_df_passed["rs_rank"] >= rs_min]
                logger.info("[Combined QM] strict_rs RS≥%.0f: %d→%d", rs_min, before, len(qm_df_passed))
                _log_detail("Stage 2-3 -- Parallel Analysis", f"QM: strict_rs RS≥{rs_min}: {before}→{len(qm_df_passed)}")

            # top_n cap
            _top_n = top_n if top_n is not None else getattr(C, "QM_SCAN_TOP_N", 50)
            if len(qm_df_passed) > _top_n:
                qm_df_passed = qm_df_passed.head(_top_n)
                _log_detail("Stage 2-3 -- Parallel Analysis", f"QM: capped to top {_top_n} results")

            qm_df_all = pd.DataFrame(s2_passed) if s2_passed else pd.DataFrame()

            logger.info("[Combined QM] Final passed count after filters: %d", len(qm_df_passed))
            
            # ── Supplement 7: Scan result count warning ──────────────────────
            # "If I'm getting hundreds of setups, my criteria are too loose"
            # "In a good market there should be a manageable number of setups"
            qm_scan_count_warning = None
            max_results_warn = getattr(C, "QM_SCAN_MAX_RESULTS_WARN", 50)
            if len(qm_df_passed) > max_results_warn:
                qm_scan_count_warning = (
                    f"⚠️ 掃描結果過多 ({len(qm_df_passed)} 個設置 > {max_results_warn} 建議上限) — "
                    f"條件可能太寬鬆。建議：收緊 ADR 門檻或提高最低星級要求 (S7)"
                )
                logger.warning(
                    "[Combined QM S7] %d results exceed warn threshold %d — criteria may be too loose",
                    len(qm_df_passed), max_results_warn
                )
                if verbose:
                    print(f"\n{_YELLOW}{qm_scan_count_warning}{_RESET}")
            
            qm_result = {
                "passed": qm_df_passed,
                "all": qm_df_all,
                "all_scored": qm_df_all_scored,
                "scan_count_warning": qm_scan_count_warning,
            }
        except Exception as e:
            logger.error("[Combined] QM process failed: %s", e, exc_info=True)
            qm_error = str(e)
            qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
    
    def _run_ml():
        nonlocal ml_result, ml_error

        # Bear market / disabled gate — skip entire ML analysis
        if ml_blocked:
            ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame(),
                         "blocked": True}
            return

        try:
            from modules.ml_screener import run_ml_stage2, run_ml_stage3

            logger.info("[Combined ML] Starting Stage 2 with %d tickers", len(ml_screened))
            _log_detail("Stage 2-3 -- Parallel Analysis", "ML: Stage 2 (EMA + ADR + Momentum) starting", indent=False)

            # ⭐ Key optimisation: pass only the ML subset of enriched_map
            # so run_ml_stage2 skips batch_download_and_enrich entirely.
            ml_enriched = {t: enriched_map[t] for t in ml_screened if t in enriched_map}

            # Build channel_map via triple-scanner (mirrors independent ml_scan flow)
            ml_screened_with_channels = list(ml_screened)  # Start with existing screened tickers
            channel_map = {t: "" for t in ml_screened}  # Default empty
            try:
                from modules.ml_scanner_channels import run_triple_scan
                _log_detail("Stage 2-3 -- Parallel Analysis", "ML: Running 3-channel identification", indent=False)
                triple = run_triple_scan(ml_screened, include_gaps=True, include_gainers=True, include_leaders=True)
                
                # Log the triple scan results
                summary = triple.get("summary", {})
                logger.info("[Combined ML TripleScan] GAP=%d GAINER=%d LEADER=%d total_unique=%d",
                            summary.get("gap_count", 0), summary.get("gainer_count", 0),
                            summary.get("leader_count", 0), summary.get("total_unique", 0))
                
                # Inject new candidates from triple scan + annotate existing ones
                # (mirrors independent ml_scan behavior exactly)
                existing = set(ml_screened)
                added = 0
                for item in triple.get("merged", []):
                    ticker = item["ticker"]
                    ch = item.get("channel", "")
                    if ticker not in existing:
                        # Inject as new candidate
                        ml_screened_with_channels.append(ticker)
                        channel_map[ticker] = ch
                        existing.add(ticker)
                        added += 1
                        logger.debug("[Combined ML TripleScan] Injected: %s [%s]", ticker, ch)
                    else:
                        # Annotate existing candidate
                        channel_map[ticker] = ch
                        logger.debug("[Combined ML TripleScan] Annotated: %s [%s]", ticker, ch)
                
                logger.info("[Combined ML TripleScan] Injected %d new + annotated existing | "
                           "final candidate count: %d", added, len(ml_screened_with_channels))
                _log_detail("Stage 2-3 -- Parallel Analysis", 
                           f"ML: Triple-scanner: {added} injected + annotated, total {len(ml_screened_with_channels)}")
            except Exception as exc:
                logger.warning("[Combined ML] Triple-scanner failed (continuing with empty channel map): %s", exc)
                # Fallback: use original mlscreened list with empty channels

            # Stage 2 with potentially expanded candidate list
            s2_passed = run_ml_stage2(ml_screened_with_channels, verbose=verbose,
                                      enriched_map=ml_enriched, channel_map=channel_map)
            logger.info("[Combined ML] Stage 2 completed: %d passing gate", len(s2_passed))
            _log_detail("Stage 2-3 -- Parallel Analysis", f"ML: Stage 2 done: {len(s2_passed)} passed gate")

            if _cancelled():
                ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            if not s2_passed:
                logger.warning("[Combined ML] Stage 2 returned no results")
                _log_detail("Stage 2-3 -- Parallel Analysis", "ML: Stage 2 returned no results")
                ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return

            # Stage 3 scoring — pass enriched_map so it uses cached data (zero yfinance calls)
            logger.info("[Combined ML] Starting Stage 3 scoring with %d candidates", len(s2_passed))
            _log_detail("Stage 2-3 -- Parallel Analysis", "ML: Stage 3 (7-Dimension Star Rating) starting", indent=False)
            ml_df_all_scored = run_ml_stage3(s2_passed, enriched_map=ml_enriched)
            if ml_df_all_scored is None or ml_df_all_scored.empty:
                logger.warning("[Combined ML] Stage 3 returned empty")
                _log_detail("Stage 2-3 -- Parallel Analysis", "ML: Stage 3 returned empty")
                ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
                return
            logger.info("[Combined ML] Stage 3 completed: shape %s", ml_df_all_scored.shape)
            _log_detail("Stage 2-3 -- Parallel Analysis", f"ML: Stage 3 done: {len(ml_df_all_scored)} scored")

            # Apply min_star filter
            _ml_min = ml_min_star if ml_min_star is not None else getattr(C, "ML_COMBINED_MIN_STAR", 3.0)
            ml_df_passed = ml_df_all_scored.copy()
            if not ml_df_passed.empty and "ml_star" in ml_df_passed.columns:
                before = len(ml_df_passed)
                ml_df_passed = ml_df_passed[ml_df_passed["ml_star"] >= _ml_min]
                _log_detail("Stage 2-3 -- Parallel Analysis", f"ML: min_star≥{_ml_min}: {before}→{len(ml_df_passed)}")

            # top_n cap
            _ml_top = top_n if top_n is not None else getattr(C, "QM_SCAN_TOP_N", 50)
            if len(ml_df_passed) > _ml_top:
                ml_df_passed = ml_df_passed.head(_ml_top)
                _log_detail("Stage 2-3 -- Parallel Analysis", f"ML: capped to top {_ml_top} results")

            logger.info("[Combined ML] Final passed count after filters: %d", len(ml_df_passed))

            ml_result = {
                "passed": ml_df_passed,
                "all": ml_df_all_scored,
            }
        except Exception as e:
            logger.error("[Combined] ML process failed: %s", e, exc_info=True)
            ml_error = str(e)
            _log_detail("Stage 2-3 -- Parallel Analysis", f"ML error: {type(e).__name__}")
            ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        sepa_thread = executor.submit(_run_sepa)
        qm_thread = executor.submit(_run_qm)
        ml_thread = executor.submit(_run_ml)
        
        # Wait for all three to complete, capture any exceptions from threads
        try:
            sepa_thread.result(timeout=600)  # 10 min timeout per thread
        except Exception as e:
            logger.error("[Combined] SEPA thread exception (not caught in _run_sepa): %s", e, exc_info=True)
            sepa_error = str(e)
            sepa_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
        
        try:
            qm_thread.result(timeout=600)
        except Exception as e:
            logger.error("[Combined] QM thread exception (not caught in _run_qm): %s", e, exc_info=True)
            qm_error = str(e)
            qm_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}

        try:
            ml_thread.result(timeout=600)
        except Exception as e:
            logger.error("[Combined] ML thread exception (not caught in _run_ml): %s", e, exc_info=True)
            ml_error = str(e)
            ml_result = {"passed": pd.DataFrame(), "all": pd.DataFrame()}
    
    parallel_elapsed = _elapsed(_t_parallel)
    logger.info("[Timing] Parallel Stage 2-3: %.1fs", parallel_elapsed)
    
    # ── Results assembly ──────────────────────────────────────────────────
    _progress("Finalizing results", 95, "Preparing output…")
    
    if sepa_error:
        print(f"[WARNING] SEPA analysis failed: {sepa_error}")
    if qm_error:
        print(f"[WARNING] QM analysis failed: {qm_error}")
    if ml_error:
        print(f"[WARNING] ML analysis failed: {ml_error}")
    
    total_elapsed = _elapsed()
    
    _timing = {
        "stage0": _elapsed(_t0),
        "stage1": _elapsed(_t1),
        "prefilter": _elapsed(_t15),
        "batch_download": _elapsed(_t2_dl),
        "parallel": parallel_elapsed,
        "total": total_elapsed,
        "sepa_s1_count": len(sepa_s1_tickers),
        "qm_s1_count":   len(qm_s1_tickers),
        "ml_s1_count":    len(ml_s1_tickers),
        "union_count":   len(s1_tickers),
        "union_after_prefilter": len(s1_tickers_filtered),
        "sepa_screened": len(sepa_screened),
        "qm_screened":   len(qm_screened),
        "ml_screened":   len(ml_screened),
        "prefilter_removed": s15a_removed + s15b_sepa_removed + s15b_qm_removed,
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
        "scan_count_warning": qm_result.get("scan_count_warning") if qm_result else None,
    }

    ml_final = {
        "passed": ml_result["passed"] if ml_result else pd.DataFrame(),
        "all": ml_result.get("all", pd.DataFrame()) if ml_result else pd.DataFrame(),
        "market_env": market_env,
        "timing": _timing,
        "error": ml_error,
        "blocked": ml_blocked,
    }

    _qm_status_str = "已封鎖(熊市)" if qm_blocked else str(len(qm_final["passed"]))
    _ml_status_str = "已封鎖(熊市)" if ml_blocked else str(len(ml_final["passed"]))
    _progress("Complete", 100,
              f"\u2705 掃描完成: SEPA→{len(sepa_final['passed'])} | QM→{_qm_status_str} | ML→{_ml_status_str} | "
              f"Stage1 {len(s1_tickers)}→預篩{len(s1_tickers_filtered)}→分析 SEPA={len(sepa_screened)} QM={len(qm_screened)} ML={len(ml_screened)} | "
              f"耗時 {total_elapsed:.0f}s")
    
    if verbose:
        print(f"\n[Combined Scan] Complete in {total_elapsed:.1f}s")
        print(f"  Stage 1:   SEPA={len(sepa_s1_tickers)} | QM={len(qm_s1_tickers)} | ML={len(ml_s1_tickers)} "
              f"| Union={len(s1_tickers)}")
        print(f"  Pre-filter: Union {len(s1_tickers)}→{len(s1_tickers_filtered)} ($Vol) | "
              f"SEPA {len(sepa_pre)}→{len(sepa_screened)} | QM {len(qm_pre)}→{len(qm_screened)} | ML {len(ml_pre)}→{len(ml_screened)} (tech)")
        qm_status = "BLOCKED (bear)" if qm_blocked else f"{len(qm_final['passed'])} passed"
        ml_status = "BLOCKED (bear)" if ml_blocked else f"{len(ml_final['passed'])} passed"
        print(f"  Results:   SEPA {len(sepa_final['passed'])} passed | QM {qm_status} | ML {ml_status}")

    logger.info("[Combined Scan] Complete: SEPA=%d | QM=%s | ML=%s | Total=%.1fs | "
                "S1 Union=%d→Prefilter=%d→Screened SEPA=%d QM=%d ML=%d",
                len(sepa_final['passed']),
                "BLOCKED" if qm_blocked else str(len(qm_final['passed'])),
                "BLOCKED" if ml_blocked else str(len(ml_final['passed'])),
                total_elapsed,
                len(s1_tickers), len(s1_tickers_filtered),
                len(sepa_screened), len(qm_screened), len(ml_screened))
    
    return (sepa_final, qm_final, ml_final)
