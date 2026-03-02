"""
modules/qm_backtester.py
────────────────────────
QM (Qullamaggie) Historical Backtest Engine

Walk-forward simulation over 2 years of price history for a single ticker.
At each checkpoint we run the full QM qualification funnel (ADR gate → momentum
→ Stage 3 star rating) using only point-in-time data.  Qualifying signals are
traded using QM's 3-phase stop system:
  • Phase 1 (Day 1)  : stop = LOD − 0.5% buffer
  • Phase 2 (Day 2)  : move to break-even
  • Phase 3 (Day 3+) : trail on 10-day SMA (soft stop; exit on close < SMA10)

Profit-taking rules (Sections 9 / Supplement 4/33):
  • Day 3–5: sell 25–50% of position when unrealised gain ≥ C.QM_PROFIT_TAKE_1ST_GAIN
  • Extended (>60% above 10SMA): sell immediately
  • Trailing stop terminates remainder

Look-ahead bias protection
──────────────────────────
All indicators (SMA, ADR, momentum, etc.) are computed from rolling windows.
We slice  df.iloc[:bar_i+1]  at every checkpoint so the QM detectors can never
see price data from the future.

Phase 2 (Portfolio-level) backtest is implemented in qm_portfolio_backtester.py.
"""

from __future__ import annotations

import sys
import logging
import threading
import math
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─── Tuneable constants (overridden by trader_config QM_BT_* values) ─────────
_MIN_DATA_BARS    = getattr(C, "QM_BT_MIN_DATA_BARS",   130)
_STEP_DAYS        = getattr(C, "QM_BT_STEP_DAYS",         5)
_SIGNAL_COOLDOWN  = getattr(C, "QM_BT_SIGNAL_COOLDOWN",  10)
_BREAKOUT_WINDOW  = getattr(C, "QM_BT_BREAKOUT_WINDOW",  20)
_MAX_HOLD_DAYS    = getattr(C, "QM_BT_MAX_HOLD_DAYS",   120)
_DEFAULT_PERIOD   = getattr(C, "QM_BT_DEFAULT_PERIOD",  "2y")
_OUTCOME_HORIZONS = getattr(C, "QM_BT_OUTCOME_HORIZONS", [10, 20, 60])

_WIN_THRESHOLD       = getattr(C, "QM_BT_WIN_THRESHOLD",       10.0)
_SMALL_WIN_THRESHOLD = getattr(C, "QM_BT_SMALL_WIN_THRESHOLD",  3.0)
_LOSS_THRESHOLD      = getattr(C, "QM_BT_LOSS_THRESHOLD",       -7.0)

# QM Phase stop rules
_DAY1_STOP_BUFFER   = getattr(C, "QM_DAY1_STOP_BELOW_LOD_PCT",  0.5)   # %
_TRAIL_MA_PERIOD    = getattr(C, "QM_TRAIL_MA_PERIOD",           10)
_EXTENDED_EXTREME   = getattr(C, "QM_EXTENDED_SMA10_EXTREME",   60.0)   # % above 10SMA → sell
_PROFIT_TAKE_DAY_MIN = getattr(C, "QM_PROFIT_TAKE_DAY_MIN",      3)
_PROFIT_TAKE_DAY_MAX = getattr(C, "QM_PROFIT_TAKE_DAY_MAX",      5)
_PROFIT_TAKE_GAIN   = getattr(C, "QM_PROFIT_TAKE_1ST_GAIN",     10.0)   # % gain threshold
_PROFIT_5STAR_GAIN  = getattr(C, "QM_PROFIT_TAKE_5STAR_GAIN",   20.0)   # % for 5+ star
_VO_MULT            = getattr(C, "QM_MIN_BREAKOUT_VOL_MULT",    1.5)
_MAX_CHASE_PCT      = getattr(C, "QM_MAX_ENTRY_ABOVE_BO_PCT",   3.0)    # %


# ══════════════════════════════════════════════════════════════════════════════
# Progress / cancel state (mirrors VCP backtester pattern)
# ══════════════════════════════════════════════════════════════════════════════

_qm_bt_lock     = threading.Lock()
_qm_bt_progress = {"stage": "idle", "pct": 0, "msg": "", "ticker": ""}
_qm_bt_cancel   = threading.Event()


def get_qm_bt_progress() -> dict:
    with _qm_bt_lock:
        return dict(_qm_bt_progress)


def set_qm_bt_cancel(event: threading.Event | None = None) -> None:
    global _qm_bt_cancel
    _qm_bt_cancel = event or threading.Event()


def _is_bt_cancelled() -> bool:
    return _qm_bt_cancel.is_set()


def _bt_prog(pct: int, msg: str, progress_cb: Callable | None = None) -> None:
    with _qm_bt_lock:
        _qm_bt_progress.update({"pct": pct, "msg": msg})
    if progress_cb:
        try:
            progress_cb(pct, msg)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Public API — single-stock backtest
# ══════════════════════════════════════════════════════════════════════════════

def run_qm_backtest(
    ticker: str,
    min_star: float = 3.0,
    max_hold_days: int = _MAX_HOLD_DAYS,
    progress_cb: Callable | None = None,
    log_file=None,
    debug_mode: bool = False,  # If True, use relaxed gate thresholds for testing
) -> dict:
    """
    Walk-forward QM backtest over 2 years of historical data.

    Parameters
    ----------
    ticker        : stock symbol e.g. "NVDA"
    min_star      : minimum QM star rating to fire a signal (default 3.0)
    max_hold_days : horizon exit after this many bars (default 120)
    progress_cb   : optional callable(pct: int, msg: str)
    log_file      : optional log file path for detailed signal logging

    Returns
    -------
    dict with keys: ok, ticker, period_start, period_end, total_bars,
                    min_star, signals, summary, equity_curve
    """
    def _prog(pct: int, msg: str):
        _bt_prog(pct, msg, progress_cb)

    ticker = ticker.upper().strip()
    _prog(5, f"Downloading {_DEFAULT_PERIOD} data for {ticker}…")

    if log_file:
        logger.info(
            "=== QM Backtest started: %s  min_star=%.1f  max_hold=%d  debug=%s ===",
            ticker, min_star, max_hold_days, debug_mode
        )

    # ── 1. Fetch full history ─────────────────────────────────────────────────
    try:
        from modules.data_pipeline import get_enriched
        df_full = get_enriched(ticker, period=_DEFAULT_PERIOD, use_cache=True)
        if df_full is None or len(df_full) < _MIN_DATA_BARS + 30:
            df_full = get_enriched(ticker, period=_DEFAULT_PERIOD, use_cache=False)
    except Exception as exc:
        logger.error("[QM BT] %s data error: %s", ticker, exc)
        return _error_result(ticker, str(exc))

    if df_full is None or len(df_full) < _MIN_DATA_BARS + 30:
        msg = f"Insufficient price history — need ≥{_MIN_DATA_BARS + 30} trading days"
        return _error_result(ticker, msg)

    df_full = df_full.sort_index()
    total_bars = len(df_full)
    _prog(15, f"Loaded {total_bars} trading days — running QM walk-forward scan…")

    # ── 2. Pre-build SPY market filter ───────────────────────────────────────
    _prog(16, "Loading SPY for market environment gate…")
    spy_above_sma200: dict[str, bool] = {}
    try:
        spy_df = get_enriched("SPY", period=_DEFAULT_PERIOD, use_cache=True)
        if spy_df is not None and len(spy_df) >= 200:
            spy_sma200 = spy_df["Close"].rolling(200).mean()
            for _d, _c, _s in zip(spy_df.index, spy_df["Close"], spy_sma200):
                if not pd.isna(_s):
                    spy_above_sma200[str(_d)[:10]] = bool(_c > _s)
    except Exception as _spy_exc:
        logger.warning("[QM BT] SPY load failed — market filter skipped: %s", _spy_exc)

    # ── 3. Walk-forward scan loop ─────────────────────────────────────────────
    signals: list[dict] = []
    last_signal_bar = -999
    scan_end  = total_bars - max_hold_days - 5
    bar_range = range(_MIN_DATA_BARS, max(scan_end, _MIN_DATA_BARS + 1), _STEP_DAYS)
    total_steps = len(bar_range)
    
    logger.info(f"[QM BT] Walk-forward config: MIN_DATA_BARS={_MIN_DATA_BARS}, STEP_DAYS={_STEP_DAYS}, scan from bar {_MIN_DATA_BARS} to {scan_end}, total_steps={total_steps}")

    for step_i, bar_i in enumerate(bar_range):
        if _is_bt_cancelled():
            logger.info("[QM BT] Cancelled at step %d/%d", step_i, total_steps)
            break

        pct = 15 + int(step_i / max(total_steps, 1) * 65)
        if step_i % 4 == 0:
            scan_date = str(df_full.index[bar_i])[:10]
            _prog(pct, f"As-of {scan_date}  ({step_i + 1}/{total_steps})…")

        # Cooldown — avoid re-firing on the same underlying base
        if bar_i - last_signal_bar < _SIGNAL_COOLDOWN:
            continue

        # Market gate: skip signals when SPY below SMA200
        sig_date_key = str(df_full.index[bar_i])[:10]
        if spy_above_sma200 and not spy_above_sma200.get(sig_date_key, True):
            continue

        # Point-in-time slice — STRICTLY no future data
        df_slice = df_full.iloc[: bar_i + 1].copy()

        # ── QM Stage 2 gate ──────────────────────────────────────────────────
        stage2 = _qm_stage2_check(ticker, df_slice, debug_mode=debug_mode)
        if stage2 is None:
            # Log Stage 2 failures (sample every 50 bars)
            if step_i % 50 == 0:
                logger.debug(f"[QM BT Stage2] Bar {bar_i}/{total_bars} REJECTED (ADR/DV/momentum/consolidation gate)")
            continue

        # Log Stage 2 pass (every bar)
        adr_val = stage2.get('adr')
        dv_val = stage2.get('dollar_volume_m')
        logger.debug(f"[QM BT Stage2 PASS] Bar {bar_i}: ADR={adr_val:.2f}%, DV=${dv_val:.1f}M")

        # ── QM Stage 3 — star rating ─────────────────────────────────────────
        star_info = _qm_stage3_score(ticker, df_slice, stage2, debug_mode=debug_mode)
        if star_info is None:
            continue
        star_rating = star_info.get("star_rating", 0.0)
        if star_rating < min_star:
            continue

        setup_type = star_info.get("setup_type", "FLAG")

        # ── Breakout level: 6-day consolidation high ─────────────────────────
        window_days = max(getattr(C, "QM_CONSOL_WINDOW_DAYS", 6), 3)
        recent_slice = df_slice.iloc[-window_days:]
        if recent_slice.empty:
            continue
        breakout_level = float(recent_slice["High"].max())
        if breakout_level <= 0:
            continue

        sig_close = float(df_full.iloc[bar_i]["Close"])

        # ── Measure outcome ──────────────────────────────────────────────────
        outcome = _qm_measure_outcome(
            df_full, bar_i, breakout_level, star_rating, max_hold_days
        )

        # Simplify setup_type for table display (extract only type_code + primary_type)
        setup_display = setup_type
        if isinstance(setup_type, dict):
            type_code = setup_type.get("type_code", "?")
            primary = setup_type.get("primary_type", "")
            # Show as: "HTF" or "🏆 High Tight Flag" if primary is available
            setup_display = primary if primary else type_code
            logger.debug(f"[QM BT] Signal {sig_date_key}: simplified setup_type from dict → '{setup_display}'")
        else:
            logger.debug(f"[QM BT] Signal {sig_date_key}: setup_type already simplified → '{setup_type}'")

        signals.append({
            "ticker":        ticker,
            "signal_date":   sig_date_key,
            "signal_bar":    bar_i,
            "signal_close":  round(sig_close, 2),
            "star_rating":   round(star_rating, 1),
            "star_label":    _star_label(star_rating),
            "setup_type":    setup_display,
            "setup_type_code": setup_type.get("type_code", "?") if isinstance(setup_type, dict) else setup_type,
            "adr":           stage2.get("adr"),
            "dollar_vol_m":  stage2.get("dollar_volume_m"),
            "mom_1m":        stage2.get("mom_1m"),
            "mom_3m":        stage2.get("mom_3m"),
            "mom_6m":        stage2.get("mom_6m"),
            "breakout_level": round(breakout_level, 2),
            **outcome,
        })
        last_signal_bar = bar_i

    _prog(82, f"Found {len(signals)} signals — computing statistics…")

    summary      = _compute_qm_summary(signals)
    equity_curve = _build_equity_curve(signals)

    period_start = str(df_full.index[_MIN_DATA_BARS])[:10]
    period_end   = str(df_full.index[min(scan_end, total_bars - 1)])[:10]

    _prog(100, "QM Backtest complete.")

    if log_file:
        breakouts_val = summary.get("breakouts")
        wr_val = summary.get("win_rate_pct")
        avg_val = summary.get("avg_realized_gain")
        logger.info(
            "=== QM Backtest finished: %s  signals=%d  breakouts=%s  wr=%s%%  avg=%s%% ===",
            ticker, len(signals),
            breakouts_val if breakouts_val is not None else "?",
            wr_val if wr_val is not None else "?",
            avg_val if avg_val is not None else "?",
        )
        _log_signal_details(signals, summary, ticker)

    return {
        "ok":           True,
        "ticker":       ticker,
        "period_start": period_start,
        "period_end":   period_end,
        "total_bars":   total_bars,
        "min_star":     min_star,
        "signals":      signals,
        "summary":      summary,
        "equity_curve": equity_curve,
    }


# ══════════════════════════════════════════════════════════════════════════════
# QM qualification helpers (point-in-time; slice must be pre-cut)
# ══════════════════════════════════════════════════════════════════════════════

def _qm_stage2_check(ticker: str, df: pd.DataFrame, debug_mode: bool = False) -> dict | None:
    """
    Inline Stage 2 gate using data_pipeline helpers.
    Returns metric dict if passes; None if vetoed.
    All four criteria must pass:
      1. ADR ≥ QM_MIN_ADR_PCT (5%)  — hard veto
      2. Dollar volume ≥ QM_SCAN_MIN_DOLLAR_VOL ($5M)
      3. Momentum: at least ONE of 1M≥25% / 3M≥50% / 6M≥150%
      4. 6-day range proximity (within consolidation rectangle)
    """
    from modules.data_pipeline import (
        get_adr, get_dollar_volume, get_momentum_returns, get_6day_range_proximity
    )

    if df is None or len(df) < 30:
        return None

    try:
        adr = get_adr(df)
    except Exception as e:
        logger.debug(f"[QM BT Stage2] ADR calc error: {e}")
        return None

    min_adr = getattr(C, "QM_MIN_ADR_PCT", 5.0)
    if debug_mode:
        min_adr = 1.0  # Very relaxed for testing
    if adr < min_adr:
        logger.debug(f"[QM BT Stage2] ADR VETO: {adr:.2f}% < {min_adr}%")
        return None

    try:
        dv = get_dollar_volume(df)
    except Exception:
        return None
    min_dv = getattr(C, "QM_SCAN_MIN_DOLLAR_VOL", 5_000_000)
    if debug_mode:
        min_dv = 100_000  # Very relaxed for testing
    if dv < min_dv:
        logger.debug(f"[QM BT Stage2] DOLLAR VOL VETO: ${dv:,.0f} < ${min_dv:,.0f}")
        return None

    try:
        mom = get_momentum_returns(df)
    except Exception:
        return None
    m1 = mom.get("1m")
    m3 = mom.get("3m")
    m6 = mom.get("6m")
    passes_1m = m1 is not None and m1 >= getattr(C, "QM_MOMENTUM_1M_MIN_PCT", 25.0)
    passes_3m = m3 is not None and m3 >= getattr(C, "QM_MOMENTUM_3M_MIN_PCT", 50.0)
    passes_6m = m6 is not None and m6 >= getattr(C, "QM_MOMENTUM_6M_MIN_PCT", 150.0)
    if debug_mode:
        # In debug mode, just require positive momentum from any period
        passes_1m = m1 is not None and m1 >= 0.0
        passes_3m = m3 is not None and m3 >= 0.0
        passes_6m = m6 is not None and m6 >= 0.0
    if not (passes_1m or passes_3m or passes_6m):
        logger.debug(f"[QM BT Stage2] MOMENTUM VETO: 1M={m1}% 3M={m3}% 6M={m6}% (need >=25%/50%/150%)")
        return None

    try:
        rng = get_6day_range_proximity(df)
    except Exception:
        return None
    
    # Consolidation check: relax for backtest to include trending stocks
    # Debug mode: completely disabled
    # Non-debug mode: require proximity to high OR low (not both) to allow uptrends
    if not debug_mode:
        if not (rng.get("near_high", False) or rng.get("near_low", False)):
            pct_from_high = rng.get("pct_from_high")
            pct_from_low = rng.get("pct_from_low")
            logger.debug(f"[QM BT Stage2] CONSOLIDATION VETO: near_high={rng.get('near_high')} near_low={rng.get('near_low')} pct_from_high={pct_from_high}% pct_from_low={pct_from_low}%")
            return None

    close = float(df["Close"].iloc[-1])
    return {
        "ticker":          ticker,
        "close":           round(close, 2),
        "adr":             round(adr, 2),
        "dollar_volume_m": round(dv / 1_000_000, 2),
        "mom_1m":          round(m1, 1) if m1 is not None else None,
        "mom_3m":          round(m3, 1) if m3 is not None else None,
        "mom_6m":          round(m6, 1) if m6 is not None else None,
        "passes_1m":       passes_1m,
        "passes_3m":       passes_3m,
        "passes_6m":       passes_6m,
    }


def _qm_stage3_score(ticker: str, df: pd.DataFrame, stage2: dict, debug_mode: bool = False) -> dict | None:
    """
    Run the full QM 6-dimension star rating on a point-in-time slice.
    Returns dict with star_rating and setup_type; None if veto (all MAs down).
    In debug_mode, returns a minimum viable star rating even if analyze_qm fails.
    """
    try:
        from modules.qm_analyzer import analyze_qm
        from modules.rs_ranking import get_rs_rank
    except ImportError as exc:
        logger.warning("[QM BT] analyze_qm import failed: %s", exc)
        if debug_mode:
            return {"star_rating": 3.0, "setup_type": "DEBUG_FLAG"}
        return None

    # All MAs declining veto (relaxed in debug mode)
    try:
        from modules.data_pipeline import get_ma_alignment
        ma = get_ma_alignment(df)
        if ma.get("all_ma_declining", False) and not debug_mode:
            return None
    except Exception:
        pass

    try:
        rs_rank = get_rs_rank(ticker)
    except Exception:
        rs_rank = 50.0   # neutral fallback

    try:
        result = analyze_qm(ticker, df, rs_rank=rs_rank, print_report=False)
    except Exception as exc:
        logger.debug("[QM BT Stage3] %s analyze_qm error: %s", ticker, exc)
        if debug_mode:
            # In debug mode, return a minimal star rating so we can test the rest of the pipeline
            return {"star_rating": 3.0, "setup_type": "DEBUG_FALLBACK"}
        return None

    if result is None:
        if debug_mode:
            return {"star_rating": 3.0, "setup_type": "DEBUG_NONE"}
        return None

    # Note: analyze_qm returns 'stars' (not 'star_rating') and 'setup_type'
    star_rating = float(result.get("stars", result.get("capped_stars", 0.0)) or 0.0)
    setup_type  = result.get("setup_type", "FLAG") or "FLAG"  # Keep as dict, don't stringify
    
    # In debug mode, override vetoes and provide a minimum viable star rating
    if debug_mode and result.get("veto") and star_rating <= 0.0:
        # The stock was vetoed (usually ADR < 5%) but we want to test Stage 3 in debug mode
        star_rating = 3.0
        logger.debug(
            "[QM BT Stage3] %s debug mode overriding veto='%s', using 3.0 stars",
            ticker, result.get("veto")
        )
    
    return {
        "star_rating": star_rating,
        "setup_type":  setup_type,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Outcome measurement — QM 3-phase stop simulation
# ══════════════════════════════════════════════════════════════════════════════

def _qm_measure_outcome(
    df: pd.DataFrame,
    signal_bar: int,
    breakout_level: float,
    star_rating: float,
    max_hold_days: int,
) -> dict:
    """
    Simulate a QM trade from signal_bar forward using the 3-phase stop system.

    Entry:  First bar after signal where close > breakout_level with vol ≥ 1.5× avg
    Phase 1 (Day 1)  : stop = entry day Low × (1 − LOD_BUFFER%)
    Phase 2 (Day 2)  : move stop to break-even (entry price)
    Phase 3 (Day 3+) : trail on 10SMA; exit when close < SMA10
    Profit-taking    : partial sell on Day 3–5 if unrealised ≥ threshold
    Extended exit    : sell immediately if price > 10SMA × 1.6

    Returns dict of trade outcome fields (no look-ahead — all from signal_bar+1).
    """
    total   = len(df)
    avg_vol = df["Volume"].iloc[max(0, signal_bar - 50): signal_bar + 1].mean()

    breakout_bar   = None
    breakout_date  = None
    breakout_price = None

    # ── Search for volume-confirmed breakout ─────────────────────────────────
    for bi in range(signal_bar + 1, min(signal_bar + _BREAKOUT_WINDOW + 1, total)):
        row   = df.iloc[bi]
        close = float(row["Close"])
        vol   = float(row["Volume"])
        # Volume confirmation required (QM Section 5.6)
        if close > breakout_level and (avg_vol == 0 or vol / avg_vol >= _VO_MULT):
            # Chase check: don't simulate if entry would be > 3% above pivot
            if (close - breakout_level) / breakout_level * 100 <= _MAX_CHASE_PCT:
                breakout_bar   = bi
                breakout_date  = str(df.index[bi])[:10]
                breakout_price = close
            break

    if breakout_bar is None or breakout_price is None:
        return _no_breakout_result()

    days_to_breakout = breakout_bar - signal_bar

    # ── Phase 1: Day 1 stop = LOD × (1 − buffer%) ───────────────────────────
    entry_price = breakout_price
    entry_day_low = float(df.iloc[breakout_bar]["Low"])
    stop_price = entry_day_low * (1.0 - _DAY1_STOP_BUFFER / 100.0)

    current_max    = entry_price
    exit_price     = None
    exit_reason    = None
    peak_gain_pct  = 0.0
    partial_sold   = False   # track first partial profit taking
    holding_day    = 0

    fut_end = min(breakout_bar + max_hold_days, total)

    # pre-compute SMA10 for the full forward window (no look-ahead: rolling from data up to that bar)
    # We use the full df to compute since we'll read bar-by-bar going forward
    sma10_series = df["Close"].rolling(_TRAIL_MA_PERIOD).mean()

    for day_i in range(breakout_bar + 1, fut_end):
        holding_day += 1
        high  = float(df.iloc[day_i]["High"])
        low   = float(df.iloc[day_i]["Low"])
        close = float(df.iloc[day_i]["Close"])
        current_max = max(current_max, high)
        unrealised_pct = (close - entry_price) / entry_price * 100.0

        # ── Phase 2 (Day 2): move to break-even ─────────────────────────────
        if holding_day == getattr(C, "QM_DAY2_BREAKEVEN_TRIGGER", 2):
            stop_price = max(stop_price, entry_price)

        # ── Phase 3 (Day 3+): 10SMA trailing stop ───────────────────────────
        if holding_day >= 3:
            sma10_val = sma10_series.iloc[day_i]
            if pd.notna(sma10_val):
                sma10_val = float(sma10_val)
                # Soft stop: ratchet up, never below break-even
                stop_price = max(stop_price, entry_price)
                # Extended stock exit (Supplement 4/33): >60% above 10SMA → sell
                if close > sma10_val * (1.0 + _EXTENDED_EXTREME / 100.0):
                    exit_price  = close
                    exit_reason = "EXTENDED_EXTREME"
                    break
                # Trail exit: close below 10SMA
                if close < sma10_val:
                    exit_price  = close
                    exit_reason = "SMA10_TRAIL"
                    break

            # Profit taking: Day 3–5 partial sell (record as event; full-position exit approximation)
            if not partial_sold and _PROFIT_TAKE_DAY_MIN <= holding_day <= _PROFIT_TAKE_DAY_MAX:
                pt_threshold = _PROFIT_5STAR_GAIN if star_rating >= 5.0 else _PROFIT_TAKE_GAIN
                if unrealised_pct >= pt_threshold:
                    partial_sold = True
                    # In single-stock mode we note the event but continue trailing the remainder
                    logger.debug(
                        "[QM BT] ProfitTake event day=%d  gain=%.1f%%  star=%.1f",
                        holding_day, unrealised_pct, star_rating,
                    )

        # ── Hard stop hit? ────────────────────────────────────────────────────
        if low <= stop_price:
            exit_price  = min(stop_price, close)   # realistic fill
            exit_reason = "STOP_LOSS"
            break

        # ── Update peak gain tracking ─────────────────────────────────────────
        peak_gain_pct = max(peak_gain_pct, (high - entry_price) / entry_price * 100.0)

    # ── Horizon exit ─────────────────────────────────────────────────────────
    if exit_price is None:
        exit_bar    = min(breakout_bar + max_hold_days, total - 1)
        exit_price  = float(df.iloc[exit_bar]["Close"])
        exit_reason = "HORIZON"

    realized_gain = round((exit_price - entry_price) / entry_price * 100.0, 2)
    max_gain      = round(peak_gain_pct, 2)
    max_drawdown  = None
    try:
        fut_slice = df.iloc[breakout_bar: fut_end]
        if not fut_slice.empty:
            max_drawdown = round(
                (float(fut_slice["Low"].min()) - entry_price) / entry_price * 100.0, 2
            )
    except Exception:
        pass

    # ── Fixed-horizon reference returns ──────────────────────────────────────
    horizon_gains: dict[str, float | None] = {}
    for h in _OUTCOME_HORIZONS:
        bi = min(breakout_bar + h, total - 1)
        try:
            hval = float(df.iloc[bi]["Close"])
            horizon_gains[f"gain_{h}d_pct"] = round(
                (hval - entry_price) / entry_price * 100.0, 2
            )
        except Exception:
            horizon_gains[f"gain_{h}d_pct"] = None

    # Outcome classification (realized exit gain)
    if   realized_gain >= _WIN_THRESHOLD:        outcome_label = "WIN"
    elif realized_gain >= _SMALL_WIN_THRESHOLD:  outcome_label = "SMALL_WIN"
    elif realized_gain <= _LOSS_THRESHOLD:       outcome_label = "LOSS"
    else:                                        outcome_label = "FLAT"

    return {
        "outcome":          outcome_label,
        "breakout_date":    breakout_date,
        "breakout_price":   round(entry_price, 2),
        "days_to_breakout": days_to_breakout,
        "exit_date":        str(df.index[min(breakout_bar + holding_day, total - 1)])[:10],
        "holding_days":     holding_day,
        "exit_gain_pct":    realized_gain,
        "exit_reason":      exit_reason,
        "partial_profit":   partial_sold,
        "max_gain_pct":     max_gain,
        "max_drawdown_pct": max_drawdown,
        **horizon_gains,
    }


def _no_breakout_result() -> dict:
    base = {f"gain_{h}d_pct": None for h in _OUTCOME_HORIZONS}
    return {
        "outcome":           "NO_BREAKOUT",
        "breakout_date":     None,
        "breakout_price":    None,
        "days_to_breakout":  None,
        "exit_date":         None,
        "holding_days":      None,
        "exit_gain_pct":     None,
        "exit_reason":       None,
        "partial_profit":    False,
        "max_gain_pct":      None,
        "max_drawdown_pct":  None,
        **base,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Summary statistics
# ══════════════════════════════════════════════════════════════════════════════

def _compute_qm_summary(signals: list[dict]) -> dict:
    """
    Compute comprehensive summary statistics for all signals.
    Includes by-setup-type and by-star-rating breakdowns.
    """
    empty = {
        "total_signals": 0, "breakouts": 0, "no_breakouts": 0,
        "win_rate_pct": None, "avg_realized_gain": None,
        "best_gain_pct": None, "worst_gain_pct": None,
        "profit_factor": None, "avg_win_pct": None, "avg_loss_pct": None,
        "avg_days_to_breakout": None, "avg_hold_days": None,
        "outcome_counts": {}, "setup_breakdown": {}, "star_breakdown": {},
        "verdict": "No QM signals detected in this period.",
    }
    if not signals:
        return empty

    breakout_sigs = [s for s in signals if s.get("outcome") != "NO_BREAKOUT"
                     and s.get("exit_gain_pct") is not None]
    no_bo_sigs    = [s for s in signals if s.get("outcome") == "NO_BREAKOUT"]

    if not breakout_sigs:
        return {**empty,
                "total_signals": len(signals),
                "no_breakouts": len(no_bo_sigs),
                "verdict": "No volume-confirmed breakouts found — price did not clear pivot."}

    wins       = [s for s in breakout_sigs if s["outcome"] in ("WIN", "SMALL_WIN")]
    losses     = [s for s in breakout_sigs if s["outcome"] == "LOSS"]
    gains      = [s["exit_gain_pct"] for s in breakout_sigs]
    pos_gains  = [g for g in gains if g > 0]
    neg_gains  = [g for g in gains if g < 0]

    win_rate   = round(len(wins) / len(breakout_sigs) * 100.0, 1)
    avg_gain   = round(float(np.mean(gains)), 2)
    best       = round(float(max([s.get("max_gain_pct") or g for s, g in zip(breakout_sigs, gains)])), 2)
    worst      = round(float(min(gains)), 2)
    avg_win    = round(float(np.mean(pos_gains)), 2) if pos_gains else None
    avg_loss   = round(float(np.mean(neg_gains)), 2) if neg_gains else None

    gross_win  = sum(pos_gains) if pos_gains else 0
    gross_loss = abs(sum(neg_gains)) if neg_gains else 0
    pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    dtb_vals  = [s["days_to_breakout"] for s in breakout_sigs if s.get("days_to_breakout")]
    hold_vals = [s["holding_days"]     for s in breakout_sigs if s.get("holding_days")]
    avg_dtb  = round(float(np.mean(dtb_vals)), 1) if dtb_vals else None
    avg_hold = round(float(np.mean(hold_vals)), 1) if hold_vals else None

    # Outcome counts
    outcome_count: dict[str, int] = {}
    for s in signals:
        k = s.get("outcome", "UNKNOWN")
        outcome_count[k] = outcome_count.get(k, 0) + 1

    # By-setup-type breakdown
    setup_bd: dict[str, dict] = {}
    for s in breakout_sigs:
        # Handle setup_type if it's a dict
        st = s.get("setup_type", "UNKNOWN")
        if isinstance(st, dict) and 'type_code' in st:
            st = st['type_code']
        st = str(st or "UNKNOWN")
        g   = s["exit_gain_pct"]
        hit = s["outcome"] in ("WIN", "SMALL_WIN")
        if st not in setup_bd:
            setup_bd[st] = {"count": 0, "wins": 0, "gains": []}
        setup_bd[st]["count"] += 1
        setup_bd[st]["wins"]  += int(hit)
        setup_bd[st]["gains"].append(g)

    setup_breakdown = {
        st: {
            "count":    v["count"],
            "wins":     v["wins"],
            "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] else None,
            "avg_gain": round(float(np.mean(v["gains"])), 2) if v["gains"] else None,
        }
        for st, v in sorted(setup_bd.items(), key=lambda x: -x[1]["count"])
    }

    # By-star-rating breakdown
    star_bd: dict[str, dict] = {}
    for s in breakout_sigs:
        sr  = s.get("star_rating", 0.0) or 0.0
        lbl = s.get("star_label", "3★") or "3★"
        g   = s["exit_gain_pct"]
        hit = s["outcome"] in ("WIN", "SMALL_WIN")
        if lbl not in star_bd:
            star_bd[lbl] = {"count": 0, "wins": 0, "gains": [], "min_star": sr}
        star_bd[lbl]["count"] += 1
        star_bd[lbl]["wins"]  += int(hit)
        star_bd[lbl]["gains"].append(g)

    star_breakdown = {
        lbl: {
            "count":    v["count"],
            "wins":     v["wins"],
            "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] else None,
            "avg_gain": round(float(np.mean(v["gains"])), 2) if v["gains"] else None,
        }
        for lbl, v in sorted(star_bd.items(), key=lambda x: x[1].get("min_star", 0))
    }

    # Verdict
    if avg_gain is None:
        avg_gain_str = "N/A"
    else:
        avg_gain_str = f"{avg_gain:+.2f}" if abs(avg_gain) >= 1 else f"{avg_gain:+.4f}"
    
    pf_str = f"{pf:.2f}" if pf is not None else "N/A"
    
    if win_rate >= 50 and avg_gain is not None and avg_gain >= 10:
        verdict = f"✅ 策略有效 — 勝率 {win_rate}%，平均實現回報 +{avg_gain}%"
    elif win_rate >= 40 and (pf or 0) >= 1.0:
        verdict = f"⚠️ 結果參差 — 勝率 {win_rate}%，平均回報 {avg_gain_str}%。Profit Factor {pf_str}"
    else:
        verdict = f"❌ 策略效果偏弱 — 勝率 {win_rate}%，平均回報 {avg_gain_str}%"

    return {
        "total_signals":        len(signals),
        "breakouts":            len(breakout_sigs),
        "no_breakouts":         len(no_bo_sigs),
        "win_rate_pct":         win_rate,
        "avg_realized_gain":    avg_gain,
        "best_gain_pct":        best,
        "worst_gain_pct":       worst,
        "avg_win_pct":          avg_win,
        "avg_loss_pct":         avg_loss,
        "profit_factor":        pf,
        "avg_days_to_breakout": avg_dtb,
        "avg_hold_days":        avg_hold,
        "outcome_counts":       outcome_count,
        "setup_breakdown":      setup_breakdown,
        "star_breakdown":       star_breakdown,
        "verdict":              verdict,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Equity curve (compounded from realized gains, chronological)
# ══════════════════════════════════════════════════════════════════════════════

def _build_equity_curve(signals: list[dict]) -> list[dict]:
    """$100 compounded through all confirmed breakouts (realized gains)."""
    if not signals:
        return []

    breakouts = [
        s for s in signals
        if s.get("outcome") != "NO_BREAKOUT" and s.get("exit_gain_pct") is not None
    ]
    if not breakouts:
        return []

    breakouts = sorted(
        breakouts,
        key=lambda x: (x.get("exit_date") or x.get("breakout_date") or x["signal_date"],
                       x["signal_date"]),
    )

    capital      = 100.0
    curve        = [{"date": breakouts[0]["signal_date"], "value": 100.0}]
    current_date = None

    for s in breakouts:
        realized = s["exit_gain_pct"]
        capital  = round(capital * (1.0 + realized / 100.0), 2)
        ref_date = s.get("exit_date") or s.get("breakout_date") or s["signal_date"]

        if ref_date != current_date:
            curve.append({"date": ref_date, "value": capital})
            current_date = ref_date
        else:
            curve[-1]["value"] = capital

    return curve


# ══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ══════════════════════════════════════════════════════════════════════════════

def _star_label(star: float) -> str:
    """Map numeric star rating to display label bucket."""
    if star >= 5.5:  return "5.5+★"
    if star >= 5.0:  return "5★"
    if star >= 4.0:  return "4★"
    if star >= 3.0:  return "3★"
    return "<3★"


def _error_result(ticker: str, error: str) -> dict:
    return {
        "ok": False, "ticker": ticker, "error": error,
        "signals": [], "summary": {}, "equity_curve": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Signal detail logging  (for log file output)
# ══════════════════════════════════════════════════════════════════════════════

def _log_signal_details(signals: list[dict], summary: dict, ticker: str) -> None:
    logger.info("")
    logger.info("╔" + "═" * 100 + "╗")
    logger.info("║" + f" QM BACKTEST SIGNALS — {ticker}".ljust(99) + "║")
    logger.info("╚" + "═" * 100 + "╝")
    logger.info("")

    header = (
        "信號日     │ ★   │Setup  │信號收市  │BO Level  │突破日     │"
        "突破價   │持日│ 實現盈虧  │退出原因         │結果"
    )
    logger.info(header)
    logger.info("─" * 115)

    for sig in signals:
        r_gain  = sig.get("exit_gain_pct")
        r_str   = f"{r_gain:+7.2f}%" if r_gain is not None else "     N/A"
        bo_price = sig.get("breakout_price")
        bp_str  = f"${bo_price:.2f}" if bo_price is not None else "   ─"
        
        sr = sig.get('star_rating')
        sr_str = f"{sr:3.1f}" if sr is not None else "  ─"
        
        hd = sig.get('holding_days')
        hd_str = str(hd) if hd is not None else "─"
        
        # Setup type - handle if it's a dict or string
        setup_type_raw = sig.get('setup_type', '?')
        if isinstance(setup_type_raw, dict) and 'type_code' in setup_type_raw:
            setup_str = setup_type_raw['type_code']
        else:
            setup_str = str(setup_type_raw or '?')
        setup_str = (setup_str or '?')[:7]
        
        # Signal close - protect None
        sig_close = sig.get('signal_close')
        sc_str = f"${sig_close:7.2f}" if sig_close is not None else "    N/A"
        
        # Breakout level - protect None
        bo_level = sig.get('breakout_level')
        bo_str = f"${bo_level:7.2f}" if bo_level is not None else "    N/A"
        
        row = (
            f"{sig.get('signal_date','?')} │ "
            f"{sr_str} │"
            f"{setup_str:<7s}│"
            f"{sc_str} │"
            f"{bo_str} │"
            f"{(sig.get('breakout_date','─') or '─'):<10s} │"
            f"{bp_str:<9s}│"
            f"{hd_str:<4s}│"
            f"{r_str} │"
            f"{(sig.get('exit_reason') or '─'):<16s} │"
            f"{sig.get('outcome','?')}"
        )
        logger.info(row)

    logger.info("─" * 115)
    logger.info("")

    # Summary
    s = summary
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + " SUMMARY".ljust(77) + "║")
    logger.info("╠" + "═" * 78 + "╣")

    lines = [
        f"║  Total: {s.get('total_signals',0)}  Breakouts: {s.get('breakouts',0)}  No-Breakout: {s.get('no_breakouts',0)}",
        f"║  Win Rate: {s.get('win_rate_pct') or 'N/A'}%  Avg Realized: {s.get('avg_realized_gain') or 'N/A'}%",
        f"║  Best: {s.get('best_gain_pct') or 'N/A'}%  Worst: {s.get('worst_gain_pct') or 'N/A'}%",
        f"║  Avg Win: {s.get('avg_win_pct') or 'N/A'}%  Avg Loss: {s.get('avg_loss_pct') or 'N/A'}%",
        f"║  Profit Factor: {s.get('profit_factor') or 'N/A'}  Avg Hold: {s.get('avg_hold_days') or 'N/A'} days",
    ]
    for l in lines:
        logger.info(l.ljust(78) + "║")

    logger.info("║" + " Setup Breakdown:".ljust(77) + "║")
    for st, bd in s.get("setup_breakdown", {}).items():
        l = f"║    {st:<12s}: {bd['count']} trades  WR={bd.get('win_rate') or '?'}%  Avg={bd.get('avg_gain') or '?'}%"
        logger.info(l.ljust(78) + "║")

    logger.info("╚" + "═" * 78 + "╝")
    logger.info("")
    logger.info(f"Verdict: {s.get('verdict','')}")
    logger.info("")
