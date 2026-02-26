"""
modules/market_env.py
──────────────────────
Market Environment Classifier  (Minervini Part 5 — Chapters 12-13)

Implements:
  • SPY/QQQ/IWM/DIA regime analysis (Bull / Transition / Bear / Bottom)
  • Distribution day counting (Minervini 12.2)
  • Market breadth: % stocks above SMA200 (via finvizfinance)
  • New High / New Low ratio
  • Sector / industry rotation strength
  • Action matrix: position sizing, aggression, stop tightness per regime
"""

import sys
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import (
    get_enriched,
    get_sector_rankings,
    get_universe,
)

logger = logging.getLogger(__name__)

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def assess(verbose: bool = True) -> dict:
    """
    Full market environment assessment.
    Returns a dict with:
      regime, spy_trend, distribution_days, breadth_pct,
      sector_rankings, action_matrix, leading_sectors, lagging_sectors,
      nh_nl_ratio, assessed_at
    """
    print("  Fetching market index data …")

    # Download enriched data for all four indices
    index_data = {}
    for sym in C.MARKET_INDICES:
        try:
            df = get_enriched(sym, period="1y")
            if df is not None and not df.empty:
                index_data[sym] = df
        except Exception as exc:
            logger.warning(f"Could not fetch {sym}: {exc}")

    if not index_data:
        return {"regime": "UNKNOWN", "error": "Could not fetch index data"}

    # Core analysis
    spy_trend    = _analyze_index(index_data.get("SPY"))
    qqq_trend    = _analyze_index(index_data.get("QQQ"))
    iwm_trend    = _analyze_index(index_data.get("IWM"))

    dist_days    = _count_distribution_days(index_data.get("SPY"))
    qqq_dist     = _count_distribution_days(index_data.get("QQQ"))
    breadth      = _measure_breadth()
    nh_nl        = _nh_nl_ratio()
    sector_df    = _get_sector_rankings()
    leading, lagging = _classify_sectors(sector_df)

    regime       = _classify_regime(spy_trend, qqq_trend, iwm_trend,
                                    dist_days, breadth, nh_nl)
    action       = _action_matrix(regime, dist_days, breadth)

    result = {
        "assessed_at":       date.today().isoformat(),
        "regime":            regime,
        "spy_trend":         spy_trend,
        "qqq_trend":         qqq_trend,
        "iwm_trend":         iwm_trend,
        "distribution_days": dist_days,
        "qqq_dist_days":     qqq_dist,
        "breadth_pct":       breadth,
        "nh_nl_ratio":       nh_nl,
        "sector_rankings":   sector_df.to_dict(orient="records") if sector_df is not None else [],
        "leading_sectors":   leading,
        "lagging_sectors":   lagging,
        "action_matrix":     action,
    }

    if verbose:
        _print_assessment(result)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _analyze_index(df: Optional[pd.DataFrame]) -> dict:
    """Single-index technical analysis."""
    if df is None or df.empty:
        return {"status": "NO DATA"}

    close   = df["Close"]
    last    = float(close.iloc[-1])
    first   = float(close.iloc[0])
    ytd_ret = (last - first) / first * 100

    sma50   = float(close.tail(50).iloc[0]) if len(close) >= 50 else None
    sma150  = float(df["SMA_150"].dropna().iloc[-1]) if "SMA_150" in df.columns else None
    sma200  = float(df["SMA_200"].dropna().iloc[-1]) if "SMA_200" in df.columns else None

    # SMA200 trend: compare current vs 22 days ago
    sma200_rising = False
    if "SMA_200" in df.columns:
        s200 = df["SMA_200"].dropna()
        if len(s200) >= 22:
            sma200_rising = float(s200.iloc[-1]) > float(s200.iloc[-22])

    # Trend score: 0-4 (count of technical positives)
    score = sum([
        sma50  is not None and last > sma50,
        sma150 is not None and last > sma150,
        sma200 is not None and last > sma200,
        sma200_rising,
    ])

    above = {
        "sma50":  last > sma50  if sma50  else None,
        "sma150": last > sma150 if sma150 else None,
        "sma200": last > sma200 if sma200 else None,
    }

    # 52-week
    high52 = float(close.tail(252).max()) if len(close) >= 252 else float(close.max())
    low52  = float(close.tail(252).min()) if len(close) >= 252 else float(close.min())
    pct_from_high = (last - high52) / high52 * 100

    direction = "UPTREND" if score >= 3 else \
                "WEAKENING" if score == 2 else "DOWNTREND"

    return {
        "status":          direction,
        "close":           round(last, 2),
        "ytd_pct":         round(ytd_ret, 1),
        "sma200_rising":   sma200_rising,
        "above_smas":      above,
        "trend_score":     score,
        "pct_from_52w_high": round(pct_from_high, 1),
    }


def _count_distribution_days(df: Optional[pd.DataFrame], window: int = 25) -> int:
    """
    Count distribution days in the last `window` sessions.
    Distribution = close down ≥0.2% on volume higher than prior day.
    (Minervini / IBD distribution day definition)
    """
    if df is None or df.empty or "Volume" not in df.columns:
        return 0

    recent  = df.tail(window + 1).copy()
    if len(recent) < 2:
        return 0

    recent["chg_pct"]  = recent["Close"].pct_change() * 100
    recent["vol_up"]   = recent["Volume"] > recent["Volume"].shift(1)
    dist               = recent[(recent["chg_pct"] <= -0.2) & (recent["vol_up"])]
    return len(dist)


def _measure_breadth() -> Optional[float]:
    """
    Estimate market breadth: % of US stocks above their SMA200.
    Uses finvizfinance screener filter 'ta_sma200' = 'price_above_sma200'.
    Returns percentage (0-100) or None if unavailable.
    """
    try:
        filters_above = {
            "geo_location": "USA",
            "ta_sma200":    "price_above_sma200",
            "average_volume": "o100",
            "price":         "o5",
        }
        df_above = get_universe(filters_above)
        n_above  = len(df_above) if df_above is not None else 0

        filters_total = {
            "geo_location": "USA",
            "average_volume": "o100",
            "price":          "o5",
        }
        df_total = get_universe(filters_total)
        n_total  = len(df_total) if df_total is not None else 1

        if n_total > 0:
            return round(n_above / n_total * 100, 1)
    except Exception as exc:
        logger.warning(f"Breadth measurement failed: {exc}")
    return None


def _nh_nl_ratio() -> Optional[float]:
    """New 52-week High / New 52-week Low ratio from finvizfinance."""
    try:
        filters_nh = {
            "geo_location":   "USA",
            "average_volume": "o100",
            "price":          "o5",
            "ta_highlow52w":  "nh",
        }
        df_nh = get_universe(filters_nh)
        n_nh  = len(df_nh) if df_nh is not None else 0

        filters_nl = {
            "geo_location":   "USA",
            "average_volume": "o100",
            "price":          "o5",
            "ta_highlow52w":  "nl",
        }
        df_nl = get_universe(filters_nl)
        n_nl  = len(df_nl) if df_nl is not None else 0

        if n_nh + n_nl > 0:
            return round(n_nh / (n_nh + n_nl) * 100, 1)
    except Exception as exc:
        logger.warning(f"NH/NL ratio failed: {exc}")
    return None


def _get_sector_rankings() -> Optional[pd.DataFrame]:
    """Fetch sector performance from finvizfinance Group."""
    try:
        df = get_sector_rankings("Sector")
        return df
    except Exception as exc:
        logger.warning(f"Sector rankings failed: {exc}")
    return None


def _classify_sectors(sector_df: Optional[pd.DataFrame]):
    """Split sectors into top/bottom thirds."""
    if sector_df is None or sector_df.empty:
        return [], []
    try:
        perf_col = "Performance (Week)" if "Performance (Week)" in sector_df.columns \
                   else sector_df.columns[1] if len(sector_df.columns) > 1 else None
        if perf_col is None:
            return [], []
        top = sector_df.nlargest(3, perf_col)["Name"].tolist() \
              if "Name" in sector_df.columns else []
        bot = sector_df.nsmallest(3, perf_col)["Name"].tolist() \
              if "Name" in sector_df.columns else []
        return top, bot
    except Exception:
        return [], []


# ═══════════════════════════════════════════════════════════════════════════════
# Regime classification
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_regime(spy: dict, qqq: dict, iwm: dict,
                     dist_days: int, breadth: Optional[float],
                     nh_nl: Optional[float]) -> str:
    """
    Classify the overall market regime.
    Returns: BULL_CONFIRMED / BULL_UNCONFIRMED / TRANSITION /
             BEAR_RALLY / BEAR_CONFIRMED / BOTTOM_FORMING
    """
    spy_score = spy.get("trend_score", 0)
    qqq_score = qqq.get("trend_score", 0)
    iwm_score = iwm.get("trend_score", 0)
    avg_score = (spy_score + qqq_score + iwm_score) / 3

    spy_direction = spy.get("status", "DOWNTREND")

    # Positive signals
    bull_signals = sum([
        avg_score >= 3,
        spy.get("sma200_rising", False),
        breadth is not None and breadth >= 60,
        nh_nl is not None and nh_nl >= 60,
        dist_days <= 3,
    ])

    # Negative signals
    bear_signals = sum([
        avg_score <= 1,
        not spy.get("sma200_rising", True),
        breadth is not None and breadth <= 30,
        nh_nl is not None and nh_nl <= 30,
        dist_days >= 6,
    ])

    if bull_signals >= 4 and dist_days <= 3:
        return "BULL_CONFIRMED"
    if bull_signals >= 3:
        return "BULL_UNCONFIRMED"
    if bear_signals >= 4:
        return "BEAR_CONFIRMED"
    if bear_signals >= 3:
        return "BEAR_RALLY"
    if spy_direction == "DOWNTREND" and avg_score >= 2:
        return "BOTTOM_FORMING"
    return "TRANSITION"


# ═══════════════════════════════════════════════════════════════════════════════
# Action matrix
# ═══════════════════════════════════════════════════════════════════════════════

# (regime) → (max_open_positions, max_portfolio_pct, stop_tightness, note)
_REGIME_ACTIONS = {
    "BULL_CONFIRMED":   (8,  100, "NORMAL",  "Full deployment; buy aggressively on VCP breakouts"),
    "BULL_UNCONFIRMED": (6,   80, "NORMAL",  "Buy select setups; raise stops on winners"),
    "TRANSITION":       (4,   50, "TIGHT",   "Reduce size; only highest-quality A setups"),
    "BOTTOM_FORMING":   (3,   30, "VERY_TIGHT", "Pilot positions only; wait for follow-thru day"),
    "BEAR_RALLY":       (2,   20, "VERY_TIGHT", "Defensive; short-term only; preserve capital"),
    "BEAR_CONFIRMED":   (0,    0, "EXITS",   "No new longs; manage / exit existing positions"),
    "UNKNOWN":          (2,   25, "TIGHT",   "Uncertain data — be cautious"),
}


def _action_matrix(regime: str, dist_days: int, breadth: Optional[float]) -> dict:
    """Return the action parameters for the given regime."""
    max_pos, max_pct, stop_mode, note = _REGIME_ACTIONS.get(
        regime, _REGIME_ACTIONS["UNKNOWN"]
    )

    # Adjust for distribution day accumulation
    if dist_days >= 5:
        max_pos  = max(0, max_pos - 2)
        max_pct  = max(0, max_pct - 20)
        note += "  [DIST DAY WARNING: reduce exposure]"

    return {
        "max_open_positions": max_pos,
        "max_portfolio_pct":  max_pct,
        "stop_mode":          stop_mode,
        "note":               note,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════════════════

_REGIME_EMOJI = {
    "BULL_CONFIRMED":   "🟢",
    "BULL_UNCONFIRMED": "🟢",
    "TRANSITION":       "🟡",
    "BOTTOM_FORMING":   "🔵",
    "BEAR_RALLY":       "🟡",
    "BEAR_CONFIRMED":   "🔴",
    "UNKNOWN":          "⚪",
}

_REGIME_COLOUR = {
    "BULL_CONFIRMED":   _GREEN,
    "BULL_UNCONFIRMED": _GREEN,
    "TRANSITION":       _YELLOW,
    "BOTTOM_FORMING":   _CYAN,
    "BEAR_RALLY":       _YELLOW,
    "BEAR_CONFIRMED":   _RED,
    "UNKNOWN":          _RESET,
}


def _print_assessment(r: dict):
    regime  = r["regime"]
    emoji   = _REGIME_EMOJI.get(regime, "⚪")
    colour  = _REGIME_COLOUR.get(regime, _RESET)
    action  = r["action_matrix"]

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  MARKET ENVIRONMENT{_RESET}  —  {r['assessed_at']}")
    print(f"{'═'*65}")
    print(f"\n  Regime: {emoji}  {colour}{_BOLD}{regime.replace('_', ' ')}{_RESET}")
    print(f"\n  {_BOLD}Index Trends:{_RESET}")
    for sym in C.MARKET_INDICES:
        key = sym.lower() + "_trend"
        t   = r.get(key) or r.get("spy_trend") if sym == "SPY" else \
              r.get("qqq_trend") if sym == "QQQ" else \
              r.get("iwm_trend") if sym == "IWM" else {}
        if not t or t.get("status") == "NO DATA":
            continue
        status  = t.get("status", "N/A")
        sc      = t.get("trend_score", 0)
        ytd     = t.get("ytd_pct", 0)
        from52h = t.get("pct_from_52w_high", 0)
        clr = _GREEN if status == "UPTREND" else _YELLOW if status == "WEAKENING" else _RED
        print(f"   {sym:<5}  {clr}{status:<12}{_RESET}  "
              f"Score {sc}/4  YTD {ytd:+.1f}%  From 52wH {from52h:.1f}%")

    print()
    if r.get("distribution_days") is not None:
        dd_colour = _GREEN if r["distribution_days"] <= 3 else \
                    _YELLOW if r["distribution_days"] <= 5 else _RED
        print(f"  Distribution Days (SPY): {dd_colour}{r['distribution_days']}{_RESET} / 25d"
              f"  |  QQQ: {r.get('qqq_dist_days', 'N/A')}")

    if r.get("breadth_pct") is not None:
        b = r["breadth_pct"]
        b_clr = _GREEN if b >= 60 else _YELLOW if b >= 40 else _RED
        print(f"  Breadth (% above SMA200): {b_clr}{b:.1f}%{_RESET}")

    if r.get("nh_nl_ratio") is not None:
        nh = r["nh_nl_ratio"]
        nh_clr = _GREEN if nh >= 60 else _YELLOW if nh >= 40 else _RED
        print(f"  New High / (NH+NL): {nh_clr}{nh:.1f}%{_RESET}")

    print(f"\n  {_BOLD}Action Matrix:{_RESET}")
    print(f"  Max open positions: {action['max_open_positions']}")
    print(f"  Max portfolio deployment: {action['max_portfolio_pct']}%")
    print(f"  Stop tightness: {action['stop_mode']}")
    print(f"  {action['note']}")

    if r.get("leading_sectors"):
        print(f"\n  {_GREEN}Leading Sectors:  {', '.join(r['leading_sectors'])}{_RESET}")
    if r.get("lagging_sectors"):
        print(f"  {_RED}Lagging Sectors:  {', '.join(r['lagging_sectors'])}{_RESET}")

    print(f"\n{'═'*65}\n")
