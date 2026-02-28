"""
modules/qm_setup_detector.py  —  Qullamaggie Setup Type Auto-Detection
═══════════════════════════════════════════════════════════════════════
Identifies which of the seven Qullamaggie setup types a stock is forming.
Used as input for the star rating system (qm_analyzer.py).

Setup types (Section 12 of QullamaggieStockguide.md):
  1. High Tight Flag (HTF)          — 100%+ prior gain + <25% pullback + tight base
  2. Ladder Breakout                — repeated stair-step consolidations
  3. Failed-Breakout Reinforce      — false breakout → tighter base → re-entry
  4. Episodic Pivot (EP)            — catalyst gap-up from long base/flat range
  5. Reverse Parabolic Long         — extreme momentum stock; 30-50%+ pullback bounce
  6. 50-SMA Surf                    — slower stock surfing 50SMA (lower ADR variant)
  7. Short Breakdown                — consolidation breaks DOWN (for reference only)

All data access is through data_pipeline.py public functions.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _max_gain_to_peak(df: pd.DataFrame, lookback: int = 252) -> float:
    """
    Maximum gain from any trough to the period's highest close.
    Used to detect "High Tight Flag" prior big move (≥100%).
    """
    if df.empty or len(df) < 20:
        return 0.0
    recent = df.tail(lookback)
    peak     = float(recent["Close"].max())
    low_before_peak = float(recent["Close"][:recent["Close"].idxmax()].min()) \
        if len(recent["Close"][:recent["Close"].idxmax()]) > 0 else float(recent["Close"].min())
    if low_before_peak <= 0:
        return 0.0
    return (peak / low_before_peak - 1.0) * 100.0


def _pullback_depth_from_peak(df: pd.DataFrame, lookback: int = 60) -> float:
    """
    Max drawdown from the rolling 252-day peak to the most recent low.
    Used for High Tight Flag detection: pullback must be < 20-25%.
    """
    if df.empty or len(df) < 20:
        return 100.0
    recent   = df.tail(252)
    peak     = float(recent["High"].max())
    recent2  = df.tail(lookback)
    cur_low  = float(recent2["Low"].min())
    if peak <= 0:
        return 100.0
    return (peak - cur_low) / peak * 100.0


def _days_since_major_prior_trend(df: pd.DataFrame,
                                  min_prior_flat_days: int = 40) -> int:
    """
    Estimate days since the stock was in a prolonged sideways/flat period.
    Used for Episodic Pivot detection.  Returns the number of flat days
    immediately preceding the current run, or 0 if there was no flat period.
    """
    if df.empty or len(df) < min_prior_flat_days + 20:
        return 0

    # Look back 1 year; count the pre-run flat period
    long  = df.tail(252)
    close = long["Close"]
    last  = float(close.iloc[-1])

    # Walking backward, find the point where price was within ±10% of its
    # current value for a prolonged stretch
    flat_count = 0
    i = len(close) - 1
    threshold = last * 0.10
    while i >= 0 and abs(float(close.iloc[i]) - last) <= threshold:
        i -= 1
    # Now find the flat region before the run
    run_start = i + 1
    # Before that, look for flat trading
    flat_segment = close.iloc[:run_start]
    if len(flat_segment) < min_prior_flat_days:
        return 0
    flat_range_pct = (float(flat_segment.max()) - float(flat_segment.min())) / float(flat_segment.mean() + 1e-9) * 100
    if flat_range_pct < 30:  # stayed within 30% band for a prolonged period
        return len(flat_segment)
    return 0


def _detect_gap_up(df: pd.DataFrame,
                   min_gap_pct: float = None,
                   max_gap_pct: float = None) -> dict:
    """
    Detect an overnight gap-up in the most recent bar.
    Gap = today's Open > yesterday's Close by min_gap_pct%.
    Also checks for follow-through (Close > Open).
    """
    min_gap = min_gap_pct or getattr(C, "QM_EP_MIN_GAP_UP_PCT", 5.0)
    max_gap = max_gap_pct or getattr(C, "QM_EP_MAX_GAP_UP_PCT", 15.0)

    empty = {"has_gap": False, "gap_pct": 0.0, "vol_multiple": 0.0,
             "follow_through": False}
    if df.empty or len(df) < 2:
        return empty

    prev_close  = float(df["Close"].iloc[-2])
    today_open  = float(df["Open"].iloc[-1])
    today_close = float(df["Close"].iloc[-1])
    today_vol   = float(df["Volume"].iloc[-1])
    avg_vol_20  = float(df["Volume"].tail(21).iloc[:-1].mean()) if len(df) >= 21 else today_vol

    if prev_close <= 0:
        return empty

    gap_pct = (today_open / prev_close - 1.0) * 100.0
    vol_mult = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
    follow   = today_close > today_open  # closed in upper half of gap bar

    has_gap = (gap_pct >= min_gap) and (gap_pct <= max_gap)
    return {
        "has_gap":       has_gap,
        "gap_pct":       round(gap_pct, 2),
        "vol_multiple":  round(vol_mult, 2),
        "follow_through": follow,
    }


def _count_ladder_stages(df: pd.DataFrame, lookback: int = 200) -> int:
    """
    Count the number of identifiable 'staircase' stages in the recent move.
    A stage = significant rally followed by sideways consolidation.
    Returns number of complete rally→consolidation cycles detected.
    """
    if df.empty or len(df) < 40:
        return 0

    recent  = df.tail(lookback)
    close   = recent["Close"]
    stages  = 0
    min_rally_pct = 20.0     # min rally between consolidations
    min_consol    = 5        # min bars in consolidation

    i       = 0
    n       = len(close)
    in_rally = False

    while i < n - 10:
        # Find a local rally of at least min_rally_pct
        start = i
        max_val = float(close.iloc[start])
        peak_idx = start
        for j in range(start + 1, min(start + 30, n)):
            if float(close.iloc[j]) > max_val:
                max_val = float(close.iloc[j])
                peak_idx = j

        base = float(close.iloc[start])
        rally = (max_val / base - 1.0) * 100.0 if base > 0 else 0

        if rally >= min_rally_pct and peak_idx > start + 3:
            # Look for consolidation after peak
            post_peak = close.iloc[peak_idx: min(peak_idx + 30, n)]
            if len(post_peak) >= min_consol:
                pp_range = (float(post_peak.max()) - float(post_peak.min())) / float(post_peak.mean() + 1e-9) * 100
                if pp_range < 20:  # tight sideways = consolidation
                    stages += 1
                    i = peak_idx + min_consol
                    continue
        i += 5

    return stages


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def detect_setup_type(df: pd.DataFrame, ticker: str = "") -> dict:
    """
    Analyse a stock's price history and determine which Qullamaggie setup
    type(s) it matches.

    Args:
        df:     Enriched OHLCV DataFrame (from get_enriched / get_historical)
        ticker: Ticker symbol (for logging)

    Returns:
        dict with:
            'primary_type'    : str — best-matched setup type name
            'type_code'       : str — short code ('HTF', 'LADDER', 'EP', etc.)
            'all_types'       : list[str] — all detected types (may overlap)
            'confidence'      : float — 0–1 confidence in primary detection
            'details'         : dict — type-specific metrics used for detection
            'description_zh'  : str — Traditional Chinese description
            'description_en'  : str — English description
    """
    from modules.data_pipeline import (
        get_momentum_returns, get_ma_alignment,
        get_higher_lows, get_adr
    )

    if df.empty or len(df) < 30:
        return _unknown_result(ticker)

    # ── Collect base metrics ───────────────────────────────────────────────
    mom         = get_momentum_returns(df)
    ma          = get_ma_alignment(df)
    hl          = get_higher_lows(df)
    adr         = get_adr(df)
    gap         = _detect_gap_up(df)
    prior_gain  = _max_gain_to_peak(df, lookback=252)
    pullback    = _pullback_depth_from_peak(df, lookback=40)
    flat_days   = _days_since_major_prior_trend(df)
    ladder_ct   = _count_ladder_stages(df)
    surf_ma     = ma.get("surfing_ma", 0)
    has_hl      = hl.get("has_higher_lows", False)
    close       = float(df["Close"].iloc[-1])

    # Recent volume dryup check (tighter = better consolidation)
    recent5_vol  = float(df["Volume"].tail(5).mean())
    avg_vol_20   = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else recent5_vol
    vol_dryup    = (recent5_vol < avg_vol_20 * 0.7) if avg_vol_20 > 0 else False

    all_types  = []
    scores: dict[str, float] = {}

    # ── 1. High Tight Flag ─────────────────────────────────────────────────
    htf_score = 0.0
    if prior_gain >= 100.0:
        htf_score += 0.4
    elif prior_gain >= 60.0:
        htf_score += 0.2
    if pullback <= 20.0:
        htf_score += 0.3
    elif pullback <= 25.0:
        htf_score += 0.15
    if has_hl:
        htf_score += 0.15
    if surf_ma in (10, 20) and ma.get("all_ma_rising", False):
        htf_score += 0.1
    if vol_dryup:
        htf_score += 0.05
    scores["HTF"] = min(htf_score, 1.0)
    if htf_score >= 0.5:
        all_types.append("HTF")

    # ── 2. Episodic Pivot ──────────────────────────────────────────────────
    ep_score = 0.0
    if gap["has_gap"]:
        ep_score += 0.4
        if gap["vol_multiple"] >= getattr(C, "QM_EP_MIN_VOL_MULT", 3.0):
            ep_score += 0.3
        if gap["follow_through"]:
            ep_score += 0.1
        if flat_days >= 20:
            ep_score += 0.2  # Was dormant before gap = classic EP
    scores["EP"] = min(ep_score, 1.0)
    if ep_score >= 0.4:
        all_types.append("EP")

    # ── 3. Ladder / Staircase ─────────────────────────────────────────────
    ladder_score = 0.0
    if ladder_ct >= 3:
        ladder_score += 0.5
    elif ladder_ct == 2:
        ladder_score += 0.3
    elif ladder_ct == 1:
        ladder_score += 0.1
    if has_hl and surf_ma > 0:
        ladder_score += 0.3
    if ma.get("all_ma_rising", False):
        ladder_score += 0.2
    scores["LADDER"] = min(ladder_score, 1.0)
    if ladder_score >= 0.4:
        all_types.append("LADDER")

    # ── 4. 50-SMA Surf ────────────────────────────────────────────────────
    surf50_score = 0.0
    if surf_ma == 50:
        surf50_score += 0.5
        if has_hl:
            surf50_score += 0.3
        if ma.get("sma_50_rising", False):
            surf50_score += 0.2
    scores["SURF_50"] = min(surf50_score, 1.0)
    if surf50_score >= 0.5:
        all_types.append("SURF_50")

    # ── 5. Reverse Parabolic Long ─────────────────────────────────────────
    rpl_score = 0.0
    m6 = mom.get("6m") or 0
    if m6 >= 150:                  # was previously parabolic
        rpl_score += 0.3
    big_pullback = max(prior_gain, pullback)
    if pullback >= 30 and pullback <= 60:  # 30-60% controlled pullback
        rpl_score += 0.3
    sma_200 = ma.get("sma_200")    # bouncing off 200SMA
    if sma_200 is not None:
        pct_vs_200 = (close / sma_200 - 1.0) * 100.0 if sma_200 > 0 else None
        if pct_vs_200 is not None and abs(pct_vs_200) <= 5:
            rpl_score += 0.4
    scores["RPL"] = min(rpl_score, 1.0)
    if rpl_score >= 0.4:
        all_types.append("RPL")

    # ── 6. Standard Breakout Flag (fallback) ─────────────────────────────
    flag_score = 0.0
    if has_hl:
        flag_score += 0.3
    if surf_ma in (10, 20) and not "HTF" in all_types:
        flag_score += 0.4
    if ma.get("sma_20_rising", False):
        flag_score += 0.2
    if vol_dryup:
        flag_score += 0.1
    scores["FLAG"] = min(flag_score, 1.0)
    if flag_score >= 0.4 and "HTF" not in all_types:
        all_types.append("FLAG")

    # ── Choose primary type ────────────────────────────────────────────────
    if not scores:
        return _unknown_result(ticker)

    primary_code = max(scores, key=scores.__getitem__)
    confidence   = scores[primary_code]

    # Descriptions
    type_meta = {
        "HTF":     ("🏆 High Tight Flag",
                    "短期大漲100%+，極窄回調<25%，在10/20SMA上整理",
                    "100%+ prior gain with <25% tight consolidation near 10/20SMA"),
        "EP":      ("📰 Episodic Pivot",
                    "長期橫盤後因重大事件（通常是盈利超預期）跳空大漲",
                    "Long dormant stock gapping up strongly on catalytic event (often earnings surprise)"),
        "LADDER":  ("🪜 階梯連續突破",
                    "多次台階式整理，每次整理都是新的進場機會",
                    "Staircase pattern: multiple rally→consolidation cycles, each a new entry"),
        "SURF_50": ("🐢 50SMA衝浪",
                    "在50SMA上整理，較慢但有效的動量股",
                    "Consolidating at 50SMA — slower but valid momentum stock"),
        "RPL":     ("🔄 反向拋物線做多",
                    "極端動量股暴跌30-60%後在長期均線彈跳，反轉機會",
                    "Extreme momentum stock pulled back 30-60% bouncing off long-term MA"),
        "FLAG":    ("🚩 旗形突破",
                    "標準旗形或三角旗形，等待放量突破上緣",
                    "Standard flag/pennant consolidation awaiting volume breakout"),
    }
    meta = type_meta.get(primary_code,
                         ("❓ 未知形態", "未能識別明確的整理形態", "Unidentified consolidation pattern"))

    # Gather detail dict
    details = {
        "prior_gain_pct":    round(prior_gain, 1),
        "pullback_pct":      round(pullback, 1),
        "ladder_stages":     ladder_ct,
        "flat_days_before":  flat_days,
        "gap_pct":           gap["gap_pct"],
        "gap_vol_mult":      gap["vol_multiple"],
        "surfing_ma":        surf_ma,
        "has_higher_lows":   has_hl,
        "num_higher_lows":   hl.get("num_lows", 0),
        "vol_dryup":         vol_dryup,
        "all_scores":        {k: round(v, 2) for k, v in scores.items()},
    }

    logger.debug("[SetupDetect] %s → %s (conf=%.2f) all=%s",
                 ticker, primary_code, confidence, all_types)

    return {
        "primary_type":    meta[0],
        "type_code":       primary_code,
        "all_types":       all_types,
        "confidence":      round(confidence, 2),
        "details":         details,
        "description_zh":  meta[1],
        "description_en":  meta[2],
    }


def _unknown_result(ticker: str = "") -> dict:
    return {
        "primary_type":    "❓ 未識別",
        "type_code":       "UNKNOWN",
        "all_types":       [],
        "confidence":      0.0,
        "details":         {},
        "description_zh":  "資料不足，無法識別形態",
        "description_en":  "Insufficient data to identify setup type",
    }


def setup_type_tooltip(type_code: str) -> str:
    """
    Return a user-friendly Traditional Chinese tooltip explaining the setup type.
    Used in the web UI for the tooltip/popover on each setup type badge.
    """
    tooltips = {
        "HTF": (
            "🏆 High Tight Flag（高緊旗形）\n"
            "短期內先大漲 100%+，然後以極窄的回調（<25%）形成旗形整理。\n"
            "成交量在整理期間萎縮，均線全部向上傾斜。\n"
            "Qullamaggie 認為這是所有形態中最強大的單一 Setup。\n"
            "'If you could just trade one setup... you're gonna make so much money.'"
        ),
        "EP": (
            "📰 Episodic Pivot（事件驅動突破）\n"
            "股票長期橫盤（通常數月至一年），因重大催化劑（如大幅盈利超預期）\n"
            "跳空大漲超過 5-10%，成交量放大 3 倍以上。\n"
            "強調沉睡越久、突破越猛。需要分辨股票之前已大漲（差）vs 長期橫盤（好）。"
        ),
        "LADDER": (
            "🪜 階梯連續突破（Ladder Pattern）\n"
            "股票以 '漲→整理→漲→整理' 的階梯式方式運動。\n"
            "每一個整理形態都是獨立的交易機會。\n"
            "均線會按順序降級：騎乘10SMA → 20SMA → 50SMA，跌破50SMA後結束趨勢。"
        ),
        "SURF_50": (
            "🐢 50SMA衝浪（50-SMA Surf）\n"
            "較慢的動量股，整理時緊貼 50SMA 上方。\n"
            "50SMA 向上傾斜，Higher Lows 在 50SMA 附近形成。\n"
            "適合 ADR 在 5-8% 範圍、機構型的股票。"
        ),
        "RPL": (
            "🔄 反向拋物線做多（Reverse Parabolic Long）\n"
            "先前曾是拋物線式暴漲（漲 150%+）的極端動量股，\n"
            "回調 30-60% 後在長期均線（100/150/200SMA）附近形成支撐並彈跳。\n"
            "屬於進階 Setup，需要較高的技術判斷能力。"
        ),
        "FLAG": (
            "🚩 旗形突破（Flag/Pennant Breakout）\n"
            "標準旗形或三角旗形整理，Higher Lows 形成，成交量萎縮。\n"
            "等待放量突破上緣作為進場信號。\n"
            "最常見的 Qullamaggie 形態，適合所有級別的交易者。"
        ),
        "UNKNOWN": (
            "❓ 未能識別明確形態\n"
            "資料不足或形態不清晰，建議觀望。\n"
            "Qullamaggie：'雜亂的圖表是頭痛，不是交易機會。'"
        ),
    }
    return tooltips.get(type_code, tooltips["UNKNOWN"])
