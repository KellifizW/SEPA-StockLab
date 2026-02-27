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
import re as _re
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import requests as _requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import (
    get_enriched,
    get_sector_rankings,
    get_universe,
)

logger = logging.getLogger(__name__)

# ── Progress tracking (mirroring screener pattern) ────────────────────────────
_market_lock = threading.Lock()
_market_progress = {"stage": "idle", "pct": 0, "msg": "", "step": 0, "total_steps": 7}

def get_market_progress() -> dict:
    with _market_lock:
        return dict(_market_progress)

def _mprog(stage: str, pct: int, msg: str = "", step: int = 0):
    logger.info("[Progress %d%%] %s — %s", pct, stage, msg)
    with _market_lock:
        _market_progress.update({"stage": stage, "pct": pct, "msg": msg,
                                  "step": step, "total_steps": 7})

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
    _mprog("Fetching index data", 5, f"Downloading {', '.join(C.MARKET_INDICES.keys())}…", step=1)
    logger.info("Fetching market index data for %s …", C.MARKET_INDICES)

    # Download enriched data for all four indices
    index_data = {}
    idx_names = list(C.MARKET_INDICES.keys())
    for i, sym in enumerate(idx_names):
        _mprog("Fetching index data", 5 + i * 3,
               f"Downloading {sym} ({i+1}/{len(idx_names)})…", step=1)
        try:
            df = get_enriched(sym, period="1y")
            if df is not None and not df.empty:
                index_data[sym] = df
                logger.info("  %s: fetched %d rows, last close=%.2f",
                            sym, len(df), float(df['Close'].iloc[-1]))
            else:
                logger.warning("  %s: returned empty/None dataframe", sym)
        except Exception as exc:
            logger.warning("Could not fetch %s: %s", sym, exc)

    if not index_data:
        logger.error("Could not fetch ANY index data — returning UNKNOWN regime")
        _mprog("Error", 100, "Could not fetch any index data", step=0)
        return {"regime": "UNKNOWN", "error": "Could not fetch index data"}

    logger.info("Successfully fetched %d/%d indices: %s",
                len(index_data), len(C.MARKET_INDICES), list(index_data.keys()))

    # Core analysis
    _mprog("Analysing index trends", 20, "SPY / QQQ / IWM trend analysis…", step=2)
    logger.info("--- Analysing index trends ---")
    spy_trend    = _analyze_index(index_data.get("SPY"))
    logger.info("  SPY trend: %s", spy_trend)
    qqq_trend    = _analyze_index(index_data.get("QQQ"))
    logger.info("  QQQ trend: %s", qqq_trend)
    iwm_trend    = _analyze_index(index_data.get("IWM"))
    logger.info("  IWM trend: %s", iwm_trend)
    dia_trend    = _analyze_index(index_data.get("DIA"))
    logger.info("  DIA trend: %s", dia_trend)

    _mprog("Counting distribution days", 30, "Checking last 25 sessions…", step=3)
    logger.info("--- Counting distribution days ---")
    dist_days    = _count_distribution_days(index_data.get("SPY"))
    qqq_dist     = _count_distribution_days(index_data.get("QQQ"))
    logger.info("  SPY dist_days=%d  QQQ dist_days=%d", dist_days, qqq_dist)

    _mprog("Measuring market breadth", 40, "Fetching %% stocks above SMA200 (finviz)…", step=4)
    logger.info("--- Measuring breadth (%%above SMA200) ---")
    breadth      = _measure_breadth()
    logger.info("  breadth_pct=%s", breadth)

    _mprog("Computing NH/NL ratio", 55, "Fetching 52-week new highs & lows (finviz)…", step=5)
    logger.info("--- Computing NH/NL ratio ---")
    nh_nl        = _nh_nl_ratio()
    logger.info("  nh_nl_ratio=%s", nh_nl)

    _mprog("Fetching sector rankings", 70, "Querying sector performance (finviz)…", step=6)
    logger.info("--- Fetching sector rankings ---")
    sector_df    = _get_sector_rankings()
    leading, lagging = _classify_sectors(sector_df)
    logger.info("  leading=%s  lagging=%s", leading, lagging)

    _mprog("Classifying regime", 85, "Computing regime & action matrix…", step=7)
    logger.info("--- Classifying regime ---")
    regime       = _classify_regime(spy_trend, qqq_trend, iwm_trend,
                                    dist_days, breadth, nh_nl)
    action       = _action_matrix(regime, dist_days, breadth)
    logger.info("  regime=%s  action=%s", regime, action)

    result = {
        "assessed_at":       date.today().isoformat(),
        "regime":            regime,
        "spy_trend":         spy_trend,
        "qqq_trend":         qqq_trend,
        "iwm_trend":         iwm_trend,
        "dia_trend":         dia_trend,
        "distribution_days": dist_days,
        "qqq_dist_days":     qqq_dist,
        "breadth_pct":       breadth,
        "nh_nl_ratio":       nh_nl,
        "sector_rankings":   sector_df.to_dict(orient="records") if sector_df is not None else [],
        "leading_sectors":   leading,
        "lagging_sectors":   lagging,
        "action_matrix":     action,
    }

    logger.info("Assessment complete: regime=%s, breadth=%s, nh_nl=%s, dist_days=%d",
                regime, breadth, nh_nl, dist_days)
    _mprog("Complete", 100, f"Regime: {regime}", step=7)

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


# ── Fast finviz count (page-1 only) ────────────────────────────────────────────────

_FINVIZ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}

def _finviz_quick_count(f_param: str) -> Optional[int]:
    """
    Fetch ONLY page 1 of finviz screener and parse the total count.
    This takes ~0.5s instead of the 3-6 minutes needed to download all pages.

    Args:
        f_param: comma-separated finviz filter codes, e.g.
                 'geo_usa,sh_avgvol_o100,sh_price_o5,ta_sma200_pa'
    Returns:
        Total matching stock count, or None on error.
    """
    try:
        url = f"https://finviz.com/screener.ashx?v=111&f={f_param}"
        resp = _requests.get(url, headers=_FINVIZ_HEADERS, timeout=15)
        resp.raise_for_status()

        # Parse: '#1 / 4239 Total'  or  '0 Total'
        m = _re.search(r'#1\s*/\s*([\d,]+)\s*Total', resp.text)
        if m:
            return int(m.group(1).replace(',', ''))
        if '0 Total' in resp.text:
            return 0
        logger.warning("_finviz_quick_count: could not parse total from page")
        return None
    except Exception as exc:
        logger.warning("_finviz_quick_count(%s) failed: %s", f_param, exc)
        return None


def _measure_breadth() -> Optional[float]:
    """
    Estimate market breadth: % of US stocks above their SMA200.
    Uses fast page-1-only finviz scrape (< 2 seconds total).
    Returns percentage (0-100) or None if unavailable.
    """
    try:
        _mprog("Measuring market breadth", 42,
               "Counting stocks above SMA200…", step=4)
        n_above = _finviz_quick_count(
            "geo_usa,sh_avgvol_o100,sh_price_o5,ta_sma200_pa")
        logger.debug("Breadth: n_above=%s", n_above)

        _mprog("Measuring market breadth", 46,
               "Counting total stocks…", step=4)
        n_total = _finviz_quick_count(
            "geo_usa,sh_avgvol_o100,sh_price_o5")
        logger.debug("Breadth: n_total=%s", n_total)

        if n_above is not None and n_total and n_total > 0:
            pct = round(n_above / n_total * 100, 1)
            logger.info("Breadth result: %d/%d = %.1f%%",
                        n_above, n_total, pct)
            return pct
        else:
            logger.warning("Breadth: got n_above=%s n_total=%s",
                           n_above, n_total)
    except Exception as exc:
        logger.warning("Breadth measurement failed: %s", exc)
    return None


def _nh_nl_ratio() -> Optional[float]:
    """New 52-week High / New 52-week Low ratio via fast finviz count."""
    try:
        _mprog("Computing NH/NL ratio", 57,
               "Counting 52-week new highs…", step=5)
        n_nh = _finviz_quick_count(
            "geo_usa,sh_avgvol_o100,sh_price_o5,ta_highlow52w_nh")
        logger.debug("NH/NL: n_nh=%s", n_nh)

        _mprog("Computing NH/NL ratio", 62,
               "Counting 52-week new lows…", step=5)
        n_nl = _finviz_quick_count(
            "geo_usa,sh_avgvol_o100,sh_price_o5,ta_highlow52w_nl")
        logger.debug("NH/NL: n_nl=%s", n_nl)

        if n_nh is not None and n_nl is not None and (n_nh + n_nl) > 0:
            ratio = round(n_nh / (n_nh + n_nl) * 100, 1)
            logger.info("NH/NL result: %d NH, %d NL → ratio=%.1f%%",
                        n_nh, n_nl, ratio)
            return ratio
        else:
            logger.warning("NH/NL: got n_nh=%s n_nl=%s", n_nh, n_nl)
    except Exception as exc:
        logger.warning("NH/NL ratio failed: %s", exc)
    return None


def _get_sector_rankings() -> Optional[pd.DataFrame]:
    """Fetch sector performance from finvizfinance Group."""
    try:
        df = get_sector_rankings("Sector")
        if df is not None:
            logger.info("Sector rankings: %d sectors fetched", len(df))
            logger.debug("Sector data:\n%s", df.to_string())
        else:
            logger.warning("Sector rankings returned None")
        return df
    except Exception as exc:
        logger.warning("Sector rankings failed: %s", exc)
    return None


def _classify_sectors(sector_df: Optional[pd.DataFrame]):
    """Split sectors into top/bottom thirds."""
    if sector_df is None or sector_df.empty:
        return [], []
    try:
        # finvizfinance returns columns like "Perf Week", not "Performance (Week)"
        perf_candidates = ["Perf Week", "Performance (Week)"]
        perf_col = None
        for c in perf_candidates:
            if c in sector_df.columns:
                perf_col = c
                break
        if perf_col is None and len(sector_df.columns) > 1:
            perf_col = sector_df.columns[1]
        if perf_col is None or "Name" not in sector_df.columns:
            return [], []

        # Values may be strings like "4.69%" — convert to float
        df = sector_df.copy()
        df[perf_col] = pd.to_numeric(
            df[perf_col].astype(str).str.replace("%", "", regex=False),
            errors="coerce",
        )
        df = df.dropna(subset=[perf_col])
        if df.empty:
            return [], []

        top = df.nlargest(3, perf_col)["Name"].tolist()
        bot = df.nsmallest(3, perf_col)["Name"].tolist()
        logger.debug("Sector classify: col=%s  top=%s  bot=%s", perf_col, top, bot)
        return top, bot
    except Exception as exc:
        logger.warning("_classify_sectors failed: %s", exc)
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

    logger.debug("Regime inputs: spy_score=%d qqq_score=%d iwm_score=%d "
                 "avg=%.1f dist=%d breadth=%s nh_nl=%s",
                 spy_score, qqq_score, iwm_score, avg_score,
                 dist_days, breadth, nh_nl)
    logger.debug("Regime signals: bull=%d bear=%d spy_dir=%s",
                 bull_signals, bear_signals, spy_direction)

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
