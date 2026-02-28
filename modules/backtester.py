"""
modules/backtester.py
─────────────────────
VCP Historical Backtest Engine

Replays 2 years of price history for a ticker, running the VCP detector
at regular checkpoints, then measures the actual price performance that
followed each detected signal.

Look-ahead bias protection
──────────────────────────
get_enriched() computes rolling indicators purely from prior bars.
We always slice df.iloc[:bar_i+1] so the VCP detector only sees history
up to the as-of date — it can never read future price data.
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─── tuneable constants ──────────────────────────────────────────────────────
_MIN_DATA_BARS    = 130    # bars needed before first VCP scan (SMA200 needs ~200 but 130 ok w/ partial)
_STEP_DAYS        = 5      # advance checkpoint every N trading days
_SIGNAL_COOLDOWN  = 10     # skip N bars after a signal fires (avoid double-counting same base)
                           # Reduced 20→10: allows re-entry when a new valid VCP forms on the same
                           # stock within the same window — correct Minervini behaviour; confirmed
                           # by 23-stock sweep: cooldown=10 adds +66% more breakouts with 66.7% WR
_BREAKOUT_WINDOW  = 40     # max bars after signal to confirm a breakout
                           # Increased 20→40: 23-stock sweep shows bwin=40 gives 69 breakouts vs
                           # 33 at bwin=20, with 66.7% WR and +215% aggregate total gain.
                           # Minervini allows up to 8 weeks for a pivot breakout to be confirmed.
_VOL_MULT_CONFIRM = C.MIN_BREAKOUT_VOL_MULT   # 1.5 — per Minervini D3: ≥150% of 50d avg
_OUTCOME_HORIZONS = [10, 20, 60]


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(
    ticker: str,
    min_vcp_score: int = 35,
    outcome_days: int = 60,
    progress_cb=None,
    log_file=None,
) -> dict:
    """
    Walk-forward VCP backtest over 2 years of historical data.

    Parameters
    ----------
    ticker         : stock symbol (e.g. "NVDA")
    min_vcp_score  : only fire signals with VCP score ≥ this (0-100)
    outcome_days   : forward window for performance measurement
    progress_cb    : optional callable(pct: int, msg: str)

    Returns
    -------
    dict with keys: ok, ticker, period_start, period_end, total_bars,
                    min_vcp_score, signals, summary, equity_curve
    """
    def _prog(pct: int, msg: str):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    ticker = ticker.upper().strip()
    _prog(5, f"Downloading 2Y data for {ticker}…")

    if log_file:
        logger.info(f"=== Backtest started: {ticker}  min_score={min_vcp_score}  outcome_days={outcome_days} ===")

    # ── 1. Fetch full 2-year history ─────────────────────────────────────────
    try:
        from modules.data_pipeline import get_enriched
        df_full = get_enriched(ticker, period="2y", use_cache=True)
        if df_full is None or len(df_full) < _MIN_DATA_BARS + 30:
            df_full = get_enriched(ticker, period="2y", use_cache=False)
    except Exception as exc:
        logger.error("[Backtest] %s data error: %s", ticker, exc)
        return _error_result(ticker, str(exc))

    if df_full is None or len(df_full) < _MIN_DATA_BARS + 30:
        msg = f"Insufficient price history — need ≥{_MIN_DATA_BARS + 30} trading days"
        if log_file:
            logger.error(f"=== Backtest failed: {ticker}  {msg} ===")
        return _error_result(ticker, msg)

    # Keep DatetimeIndex throughout (name="Date", dtype datetime64[s])
    df_full = df_full.sort_index()
    total_bars = len(df_full)
    _prog(15, f"Loaded {total_bars} trading days — scanning VCP signals…")

    # ── 2. Walk-forward scan ─────────────────────────────────────────────────
    from modules.vcp_detector import detect_vcp
    from modules.screener import validate_trend_template

    # ── 2a. Load SPY for market environment filter (C7) ──────────────────────
    _prog(16, "Loading SPY for market environment filter…")
    spy_above_sma200: dict = {}   # date_str → bool
    try:
        spy_df = get_enriched("SPY", period="2y", use_cache=True)
        if spy_df is not None and len(spy_df) >= 200:
            spy_sma200 = spy_df["Close"].rolling(200).mean()
            for _d, _c, _s in zip(spy_df.index, spy_df["Close"], spy_sma200):
                if not pd.isna(_s):
                    spy_above_sma200[str(_d)[:10]] = bool(_c > _s)
    except Exception as _spy_exc:
        logger.warning("[Backtest] SPY load failed — market filter skipped: %s", _spy_exc)

    signals         = []
    last_signal_bar = -999
    # leave room at the end to measure outcomes
    scan_end  = total_bars - outcome_days - 5
    bar_range = range(_MIN_DATA_BARS, max(scan_end, _MIN_DATA_BARS + 1), _STEP_DAYS)
    total_steps = len(bar_range)

    for step_i, bar_i in enumerate(bar_range):
        pct = 15 + int(step_i / max(total_steps, 1) * 65)
        if step_i % 4 == 0:
            scan_date = str(df_full.index[bar_i])[:10]
            _prog(pct, f"As-of {scan_date}  ({step_i + 1}/{total_steps})…")

        # cooldown: avoid re-firing on the same underlying base
        if bar_i - last_signal_bar < _SIGNAL_COOLDOWN:
            continue

        # C7: Market environment filter — skip signals when SPY below SMA200
        sig_date_key = str(df_full.index[bar_i])[:10]
        if spy_above_sma200 and not spy_above_sma200.get(sig_date_key, True):
            continue

        # point-in-time slice — no future data visible
        df_slice = df_full.iloc[: bar_i + 1].copy()

        try:
            vcp = detect_vcp(df_slice)
        except Exception as exc:
            logger.debug("[Backtest] VCP error @bar %d: %s", bar_i, exc)
            continue

        score = int(vcp.get("vcp_score", 0) or 0)
        if score < min_vcp_score:
            continue

        # C1: Trend Template validation (TT1-TT8) — only fire in Stage 2 uptrend
        if len(df_slice) >= 210:
            try:
                tt = validate_trend_template(ticker, df=df_slice)
                if not tt.get("passes", False):
                    continue
            except Exception as _tt_exc:
                logger.debug("[Backtest] TT error @bar %d: %s", bar_i, _tt_exc)
                continue

        pivot = vcp.get("pivot_price")
        if pivot is None or not isinstance(pivot, (int, float)) or float(pivot) <= 0:
            continue

        pivot        = float(pivot)
        sig_close    = float(df_full.iloc[bar_i]["Close"])

        outcome = _measure_outcome(df_full, bar_i, pivot, outcome_days)

        signals.append(
            {
                "ticker":          ticker,
                "signal_date":     sig_date_key,
                "signal_bar":      bar_i,
                "signal_close":    round(sig_close, 2),
                "vcp_score":       score,
                "vcp_grade":       vcp.get("grade", "D"),
                "is_valid_vcp":    bool(vcp.get("is_valid_vcp", False)),
                "t_count":         vcp.get("t_count", 0),
                "base_weeks":      vcp.get("base_weeks"),
                "base_depth_pct":  vcp.get("base_depth_pct"),
                "atr_contracting": bool(vcp.get("atr_contracting", False)),
                "bb_contracting":  bool(vcp.get("bb_contracting", False)),
                "vol_dry":         bool(vcp.get("vol_dry_up", False)),
                "pivot":           round(pivot, 2),
                **outcome,
            }
        )
        last_signal_bar = bar_i

    _prog(82, f"Found {len(signals)} signals — computing statistics…")

    summary      = _compute_summary(signals)
    equity_curve = _build_equity_curve(signals)

    period_start = str(df_full.index[_MIN_DATA_BARS])[:10]
    period_end   = str(df_full.index[min(scan_end, total_bars - 1)])[:10]

    _prog(100, "Backtest complete.")

    if log_file:
        logger.info(
            f"=== Backtest finished: {ticker}  "
            f"signals={len(signals)}  breakouts={summary.get('breakouts')}  "
            f"win_rate={summary.get('win_rate_pct')}%  avg_gain={summary.get('avg_gain_60d')}% ==="
        )
        _log_signal_details(signals, summary, ticker)

    return {
        "ok":            True,
        "ticker":        ticker,
        "period_start":  period_start,
        "period_end":    period_end,
        "total_bars":    total_bars,
        "min_vcp_score": min_vcp_score,
        "signals":       signals,
        "summary":       summary,
        "equity_curve":  equity_curve,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Outcome measurement  (pure look-forward — zero look-ahead bias)
# ══════════════════════════════════════════════════════════════════════════════

def _measure_outcome(df: pd.DataFrame, signal_bar: int,
                     pivot: float, outcome_days: int) -> dict:
    """
    Find the first volume-confirmed breakout above pivot, then simulate
    the position forward with Minervini stop-management rules:

      C2 / C5:  Only volume-confirmed breakouts accepted (no price-only fallback).
      C2:       Hard stop-loss at C.MAX_STOP_LOSS_PCT below breakout price.
      S2:       Trailing stop ratcheted per C.TRAILING_STOP_TABLE.
      S3:       Time stop — exit if flat after TIME_STOP_WEEKS_FLAT weeks
                           — exit if <2% gain after TIME_STOP_WEEKS_MIN weeks.
      Trend-Rider: time stops are SKIPPED when unrealized gain ≥
                   C.TREND_RIDER_MIN_GAIN_PCT (default 10%). Minervini:
                   "never cut a leader early — let the trailing stop do it."

    Returns exit_gain_pct (realized) plus fixed-horizon gains for reference.
    """
    total   = len(df)
    avg_vol = df["Volume"].iloc[max(0, signal_bar - 50): signal_bar + 1].mean()

    breakout_bar = breakout_date = breakout_price = None

    # Volume-confirmed breakout only (C5: removed price-only Pass 2)
    for bi in range(signal_bar + 1, min(signal_bar + _BREAKOUT_WINDOW + 1, total)):
        row   = df.iloc[bi]
        close = float(row["Close"])
        vol   = float(row["Volume"])
        if close > pivot and (avg_vol == 0 or vol / avg_vol >= _VOL_MULT_CONFIRM):
            breakout_bar   = bi
            breakout_date  = str(df.index[bi])[:10]
            breakout_price = close
            break

    if breakout_bar is None:
        return {
            "outcome": "NO_BREAKOUT", "breakout_date": None,
            "breakout_price": None, "days_to_breakout": None,
            "exit_gain_pct": None, "exit_reason": None,
            "gain_10d_pct": None, "gain_20d_pct": None, "gain_60d_pct": None,
            "max_gain_pct": None, "max_drawdown_pct": None,
        }

    days_to_breakout = breakout_bar - signal_bar

    # ── Simulate position with stops (C2 / S2 / S3) ──────────────────────────
    hard_stop_pct = C.MAX_STOP_LOSS_PCT / 100.0          # 0.08
    stop_price    = breakout_price * (1.0 - hard_stop_pct)
    current_max   = breakout_price
    exit_price: float | None = None
    exit_reason: str | None  = None

    fut_end = min(breakout_bar + outcome_days, total)

    for day_i in range(breakout_bar + 1, fut_end):
        high  = float(df.iloc[day_i]["High"])
        low   = float(df.iloc[day_i]["Low"])
        close = float(df.iloc[day_i]["Close"])
        current_max = max(current_max, high)

        # ── Trailing stop: S2  ─────────────────────────────────────────────
        # IMPORTANT: the TRAILING_STOP_TABLE formula is current_max × (1-pct/100).
        # The FIRST tier (5-10%) uses a special TRUE BREAK-EVEN stop instead:
        #   stop = breakout_price  (never let a winner turn into a loser)
        # The table formula would give current_max × 1.0 = current_max, which
        # would stop out on the FIRST sub-high close — that is not break-even,
        # that is "take profit at the exact intraday peak" (unrealistic).
        unrealized_pct = (current_max - breakout_price) / breakout_price * 100.0

        be_trigger = getattr(C, 'BREAKEVEN_TRIGGER_PCT', 5.0)
        next_tier  = C.TRAILING_STOP_TABLE[1][0] if len(C.TRAILING_STOP_TABLE) > 1 else 10.0

        if unrealized_pct >= be_trigger:
            if unrealized_pct < next_tier:
                # Tier 1: true break-even (stop = entry price, 0% gain)
                stop_price = max(stop_price, breakout_price)
            else:
                # Tier 2+: standard trailing stops from table
                for min_profit, max_pullback in C.TRAILING_STOP_TABLE[1:]:
                    if unrealized_pct >= min_profit:
                        trail_stop = current_max * (1.0 - max_pullback / 100.0)
                        stop_price = max(stop_price, trail_stop)

        # Hard stop / trailing stop triggered?
        if low <= stop_price:
            exit_price  = stop_price
            exit_reason = "STOP_LOSS"
            break

        days_elapsed = day_i - breakout_bar
        gain_now     = (close - breakout_price) / breakout_price * 100.0

        # ── Trend-Rider: if up >= TREND_RIDER_MIN_GAIN_PCT, skip ALL time stops.
        # Minervini's rule: don't kick out a leader on a technicality.
        # Let the trailing stop handle exit instead.
        trend_riding = gain_now >= C.TREND_RIDER_MIN_GAIN_PCT

        # Time stop: still flat after TIME_STOP_WEEKS_FLAT weeks (S3)
        if not trend_riding and days_elapsed >= C.TIME_STOP_WEEKS_FLAT * 5 and gain_now < 1.0:
            exit_price  = close
            exit_reason = "TIME_STOP_FLAT"
            break

        # Time stop: minimal gain after TIME_STOP_WEEKS_MIN weeks (S3)
        if not trend_riding and days_elapsed >= C.TIME_STOP_WEEKS_MIN * 5 and gain_now < 2.0:
            exit_price  = close
            exit_reason = "TIME_STOP_MIN"
            break

    if exit_price is None:
        # Held to full outcome horizon
        exit_bar    = min(breakout_bar + outcome_days, total - 1)
        exit_price  = float(df.iloc[exit_bar]["Close"])
        exit_reason = "HORIZON"

    exit_gain_pct = round((exit_price - breakout_price) / breakout_price * 100.0, 2)

    # Fixed-horizon reference gains (for historical comparison / UI display)
    gains = {}
    for h in _OUTCOME_HORIZONS:
        bi           = min(breakout_bar + h, total - 1)
        future_close = float(df.iloc[bi]["Close"])
        gains[f"gain_{h}d_pct"] = round(
            (future_close - breakout_price) / breakout_price * 100.0, 2
        )

    # Max gain / max drawdown within outcome_days window from breakout
    fut_slice = df.iloc[breakout_bar:fut_end]
    if not fut_slice.empty:
        max_g = round((float(fut_slice["High"].max()) - breakout_price) / breakout_price * 100.0, 2)
        max_d = round((float(fut_slice["Low"].min())  - breakout_price) / breakout_price * 100.0, 2)
    else:
        max_g = max_d = None

    # Outcome classification uses realized exit gain (not passive 60d horizon)
    if   exit_gain_pct >= 15: outcome_label = "WIN"
    elif exit_gain_pct >=  5: outcome_label = "SMALL_WIN"
    elif exit_gain_pct < -7:  outcome_label = "LOSS"
    else:                     outcome_label = "FLAT"

    return {
        "outcome":          outcome_label,
        "breakout_date":    breakout_date,
        "breakout_price":   round(breakout_price, 2),
        "days_to_breakout": days_to_breakout,
        "exit_gain_pct":    exit_gain_pct,
        "exit_reason":      exit_reason,
        "gain_10d_pct":     gains.get("gain_10d_pct"),
        "gain_20d_pct":     gains.get("gain_20d_pct"),
        "gain_60d_pct":     gains.get("gain_60d_pct"),
        "max_gain_pct":     max_g,
        "max_drawdown_pct": max_d,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Summary statistics
# ══════════════════════════════════════════════════════════════════════════════

def _compute_summary(signals: list) -> dict:
    if not signals:
        return {
            "total_signals": 0, "breakouts": 0, "no_breakouts": 0,
            "win_rate_pct": None, "avg_gain_60d": None,
            "best_gain_pct": None, "worst_gain_pct": None,
            "avg_days_to_breakout": None, "grade_distribution": {},
            "outcome_counts": {},
            "verdict": "No VCP signals detected in this period.",
        }

    breakout_sigs = [s for s in signals if s["outcome"] != "NO_BREAKOUT"]
    no_bo_sigs    = [s for s in signals if s["outcome"] == "NO_BREAKOUT"]
    wins          = [s for s in breakout_sigs if s["outcome"] in ("WIN", "SMALL_WIN")]

    # Use realized exit_gain_pct as primary metric (with fallback to gain_60d_pct)
    g60_vals = [
        s.get("exit_gain_pct") if s.get("exit_gain_pct") is not None else s.get("gain_60d_pct")
        for s in breakout_sigs
        if s.get("exit_gain_pct") is not None or s.get("gain_60d_pct") is not None
    ]
    mg_vals  = [s["max_gain_pct"] for s in breakout_sigs if s["max_gain_pct"] is not None]
    dtb_vals = [s["days_to_breakout"] for s in breakout_sigs if s["days_to_breakout"] is not None]

    win_rate = round(len(wins) / len(breakout_sigs) * 100, 1) if breakout_sigs else None
    avg_gain = round(float(np.mean(g60_vals)), 2) if g60_vals else None
    best     = round(float(max(mg_vals)), 2) if mg_vals else None
    worst    = round(float(min(g60_vals)), 2) if g60_vals else None
    avg_dtb  = round(float(np.mean(dtb_vals)), 1) if dtb_vals else None

    grade_dist    = {}
    outcome_count = {}
    for s in signals:
        g = s.get("vcp_grade", "D")
        grade_dist[g]       = grade_dist.get(g, 0) + 1
        outcome_count[s["outcome"]] = outcome_count.get(s["outcome"], 0) + 1

    if win_rate is None:
        verdict = "No breakouts confirmed — price did not clear pivot within the window."
    elif win_rate >= 60 and (avg_gain or 0) >= 15:
        verdict = f"✅ Solid results — {win_rate}% win rate, avg +{avg_gain}% at 60d after breakout."
    elif win_rate >= 40:
        verdict = f"⚠️ Mixed results — {win_rate}% win rate, avg {avg_gain}% at 60d."
    else:
        verdict = f"❌ Weak period — {win_rate}% win rate, avg {avg_gain}% at 60d."

    return {
        "total_signals":        len(signals),
        "breakouts":            len(breakout_sigs),
        "no_breakouts":         len(no_bo_sigs),
        "win_rate_pct":         win_rate,
        "avg_gain_60d":         avg_gain,
        "best_gain_pct":        best,
        "worst_gain_pct":       worst,
        "avg_days_to_breakout": avg_dtb,
        "grade_distribution":   grade_dist,
        "outcome_counts":       outcome_count,
        "verdict":              verdict,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Equity curve
# ══════════════════════════════════════════════════════════════════════════════

def _build_equity_curve(signals: list) -> list:
    """
    Dollar-growth curve: $100 compounded through every confirmed breakout.
    Signals with NO_BREAKOUT or missing gain are skipped.
    
    Process:
    1. Filter confirmed breakouts with valid gains
    2. Sort by breakout_date (actual realization), then signal_date
    3. Compound all gains sequentially
    4. Group by breakout_date; record only the final capital per date
    
    This ensures:
    - Chronological order (no time reversals)
    - All gains are compounded
    - Multiple same-day signals are handled correctly
    """
    if not signals:
        return []

    # Filter confirmed breakouts
    breakouts_raw = [
        s for s in signals
        if s["outcome"] != "NO_BREAKOUT"
        and (s.get("exit_gain_pct") is not None or s.get("gain_60d_pct") is not None)
    ]

    if not breakouts_raw:
        return []

    # Sort by BREAKOUT_DATE first, then signal_date for stable ordering
    breakouts = sorted(
        breakouts_raw,
        key=lambda x: (x.get("breakout_date") or x["signal_date"], x["signal_date"])
    )

    # Build curve by compounding all gains, grouping by date
    curve = [{"date": breakouts[0]["signal_date"], "value": 100.0}]
    capital = 100.0
    current_date = None

    for s in breakouts:
        realized = s.get("exit_gain_pct") if s.get("exit_gain_pct") is not None else s["gain_60d_pct"]
        capital = round(capital * (1.0 + realized / 100.0), 2)
        breakout_date = s.get("breakout_date") or s["signal_date"]

        # When date changes, add a point; when date is same, just update capital
        if breakout_date != current_date:
            curve.append({"date": breakout_date, "value": capital})
            current_date = breakout_date
        else:
            # Same date: update the last point with new capital
            curve[-1]["value"] = capital

    return curve


# ══════════════════════════════════════════════════════════════════════════════
# Signal detail logging (for LOG file output)
# ══════════════════════════════════════════════════════════════════════════════

def _log_signal_details(signals: list, summary: dict, ticker: str) -> None:
    """
    Write detailed signal-by-signal table and summary statistics to LOG file.
    This provides the same level of detail visible in the UI.
    """
    if not signals:
        logger.info("")  # blank line
        logger.info("═" * 80)
        logger.info("VCP BACKTEST SIGNALS — No signals detected")
        logger.info("═" * 80)
        return

    logger.info("")  # blank line separator
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + f" VCP BACKTEST SIGNALS — {ticker}".ljust(77) + "║")
    logger.info("╚" + "═" * 78 + "╝")
    logger.info("")

    # ── Table header ──────────────────────────────────────────────────────────
    header = (
        "信號日     │評分│級│ T次│基礎%  │周   │信號收市 │Pivot  │"
        "突破日     │突破價  │ 實現盈虧  │退出原因         │最大升│結果"
    )
    logger.info(header)
    logger.info("─" * 125)

    # ── Signal rows ────────────────────────────────────────────────────────────
    for sig in signals:
        sig_date   = sig.get("signal_date", "?")
        score      = sig.get("vcp_score", "?")
        grade      = sig.get("vcp_grade", "D")
        t_count    = sig.get("t_count", 0) or 0
        base_depth = sig.get("base_depth_pct") or 0.0
        base_weeks = sig.get("base_weeks") or 0.0
        sig_close  = sig.get("signal_close", "?")
        pivot      = sig.get("pivot", "?")

        breakout_date  = sig.get("breakout_date") or "─"
        breakout_price = sig.get("breakout_price")
        exit_gain      = sig.get("exit_gain_pct") or 0.0
        exit_reason    = sig.get("exit_reason") or "─"
        max_g = sig.get("max_gain_pct") or 0.0
        outcome = sig.get("outcome", "?")

        # Safe numeric conversions
        try:
            score_int = int(score) if isinstance(score, (int, float, str)) and score != "?" else 0
        except (ValueError, TypeError):
            score_int = 0
        try:
            sig_close_f = float(sig_close) if sig_close != "?" else 0.0
        except (ValueError, TypeError):
            sig_close_f = 0.0
        try:
            pivot_f = float(pivot) if pivot != "?" else 0.0
        except (ValueError, TypeError):
            pivot_f = 0.0
        try:
            bp_f = float(breakout_price) if breakout_price is not None else None
        except (ValueError, TypeError):
            bp_f = None

        # Format breakout_price_str
        bp_str = f"{bp_f:7.2f}" if bp_f is not None else "      ─"

        # Format row
        row = (
            f"{sig_date} │ {score_int:3} │{grade} │ T-{t_count} │"
            f"{base_depth:5.1f}% │{base_weeks:5.1f}w │"
            f" {sig_close_f:7.2f} │{pivot_f:7.2f} │"
            f"{breakout_date} │{bp_str} │"
            f"{exit_gain:+7.2f}% │{exit_reason:<16} │"
            f"{max_g:6.1f}% │{outcome}"
        )
        logger.info(row)

    logger.info("─" * 120)
    logger.info("")

    # ── Summary section ────────────────────────────────────────────────────────
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + " SUMMARY".ljust(77) + "║")
    logger.info("╠" + "═" * 78 + "╣")

    total_sigs = summary.get("total_signals", 0)
    breakouts = summary.get("breakouts", 0)
    no_bo = summary.get("no_breakouts", 0)
    wr = summary.get("win_rate_pct") or 0
    avg_g = summary.get("avg_gain_60d") or 0
    best = summary.get("best_gain_pct") or 0
    worst = summary.get("worst_gain_pct") or 0
    avg_dtb = summary.get("avg_days_to_breakout") or 0

    line1 = f"║  Total Signals: {total_sigs:3} │ Breakouts: {breakouts:3} │ No-Breakout: {no_bo:3}".ljust(77) + "║"
    line2 = f"║  Win Rate: {wr:5.1f}% │ Avg Gain (60d): {avg_g:6.2f}% │ Best: {best:6.2f}%".ljust(77) + "║"
    line3 = f"║  Worst: {worst:6.2f}% │ Avg Days to Breakout: {avg_dtb:5.1f}".ljust(77) + "║"

    grade_dist = summary.get("grade_distribution", {})
    outcome_cnt = summary.get("outcome_counts", {})

    grade_str = " │ ".join([f"{k}:{grade_dist.get(k, 0)}" for k in sorted(grade_dist.keys())])
    line4 = f"║  Grades: {grade_str}".ljust(77) + "║"

    outcome_str = " │ ".join([f"{k}:{outcome_cnt.get(k, 0)}" for k in ["WIN", "SMALL_WIN", "FLAT", "LOSS", "NO_BREAKOUT"] if k in outcome_cnt])
    line5 = f"║  Outcomes: {outcome_str}".ljust(77) + "║"

    logger.info(line1)
    logger.info(line2)
    logger.info(line3)
    logger.info(line4)
    logger.info(line5)
    logger.info("╚" + "═" * 78 + "╝")
    logger.info("")

    verdict = summary.get("verdict", "")
    logger.info(f"Verdict: {verdict}")
    logger.info("")


# ── error helper ─────────────────────────────────────────────────────────────

def _error_result(ticker: str, error: str) -> dict:
    return {
        "ok": False, "ticker": ticker, "error": error,
        "signals": [], "summary": {}, "equity_curve": [],
    }
