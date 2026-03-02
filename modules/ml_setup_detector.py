"""
modules/ml_setup_detector.py  —  Martin Luk Setup Type Auto-Detection
═══════════════════════════════════════════════════════════════════════
Identifies which of Martin Luk's trading setup types a stock is forming.
Used as input for the star rating system (ml_analyzer.py).

Setup types (from MartinLukStockGuidePart1/2):
  1. PB_EMA      — Pullback to rising EMA (9/21/50) with volume dry-up → bounce
  2. BR_RETEST   — Breakout then retest of breakout level + AVWAP confluence
  3. BREAKOUT    — Classic breakout above resistance on volume
  4. EP          — Episodic Pivot — gap up on catalyst (earnings, news)
  5. CHAR_CHG    — Character Change — stock emerging from Stage 1 to Stage 2
  6. PARABOLIC   — Parabolic move → risky, for experienced traders only
  7. UNKNOWN     — Does not match any classified setup

All data access is through data_pipeline.py public functions.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules import data_pipeline as dp

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _volume_dry_up(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    Detect volume dry-up during pullback — Martin Luk's key PB confirmation.
    Volume dry-up = recent avg volume is significantly below (< 50%) the
    20-day baseline. Indicates sellers are exhausted.

    Returns:
        dict with 'is_dry', 'ratio' (recent/baseline), 'recent_avg', 'baseline'.
    """
    threshold = getattr(C, "ML_VOLUME_DRY_UP_RATIO", 0.50)
    if df.empty or len(df) < 30:
        return {"is_dry": False, "ratio": 1.0, "recent_avg": 0, "baseline": 0}

    baseline = float(df["Volume"].tail(30).iloc[:-lookback].mean()) if len(df) >= 30 else 1.0
    recent   = float(df["Volume"].tail(lookback).mean())
    ratio    = recent / baseline if baseline > 0 else 1.0

    return {
        "is_dry":     ratio < threshold,
        "ratio":      round(ratio, 3),
        "recent_avg": int(recent),
        "baseline":   int(baseline),
    }


def _volume_surge(df: pd.DataFrame) -> dict:
    """
    Detect volume surge on the most recent bar — bounce confirmation.
    Martin uses 1.5× avg as confirmation, 2× as strong.
    """
    surge_mult = getattr(C, "ML_VOLUME_SURGE_MULT", 1.5)
    ideal_mult = getattr(C, "ML_IDEAL_VOLUME_SURGE_MULT", 2.0)

    if df.empty or len(df) < 21:
        return {"has_surge": False, "vol_ratio": 0.0, "is_strong": False}

    avg_vol = float(df["Volume"].tail(21).iloc[:-1].mean())
    today_vol = float(df["Volume"].iloc[-1])
    ratio = today_vol / avg_vol if avg_vol > 0 else 0.0

    return {
        "has_surge": ratio >= surge_mult,
        "vol_ratio": round(ratio, 2),
        "is_strong": ratio >= ideal_mult,
    }


def _detect_ema_bounce(df: pd.DataFrame, ema_period: int = 21) -> dict:
    """
    Detect if price recently touched/crossed an EMA and is bouncing back.
    Martin Luk's primary pattern: pullback TO the EMA, then reversal candle.

    A bounce is confirmed when:
      1. Low of recent bars touched or went below EMA
      2. Close recovered above EMA
      3. Close > Open (bullish candle)

    Returns:
        dict with 'is_bouncing', 'ema_touched_date', 'touch_depth_pct'.
    """
    ema_col = f"EMA_{ema_period}"
    if df.empty or len(df) < ema_period + 5 or ema_col not in df.columns:
        return {"is_bouncing": False, "ema_touched_date": None, "touch_depth_pct": None}

    recent = df.tail(5)
    ema_vals = recent[ema_col]
    lows = recent["Low"]
    closes = recent["Close"]
    opens = recent["Open"]

    # Check if any of the last 5 bars' Low touched or went below EMA
    touched = False
    touch_date = None
    touch_depth = None
    for i in range(len(recent)):
        low_val = float(lows.iloc[i])
        ema_val = float(ema_vals.iloc[i])
        if ema_val > 0 and low_val <= ema_val * 1.005:  # within 0.5% counts
            touched = True
            touch_date = str(recent.index[i])[:10]
            touch_depth = (low_val / ema_val - 1.0) * 100.0

    # Bounce = touched + last close above EMA + bullish candle
    last_close = float(closes.iloc[-1])
    last_open = float(opens.iloc[-1])
    last_ema = float(ema_vals.iloc[-1])

    is_bouncing = (
        touched
        and last_close > last_ema
        and last_close > last_open  # bullish candle
    )

    return {
        "is_bouncing":      is_bouncing,
        "ema_touched_date": touch_date,
        "touch_depth_pct":  round(touch_depth, 2) if touch_depth is not None else None,
    }


def _detect_breakout_retest(df: pd.DataFrame, lookback: int = 30) -> dict:
    """
    Detect a breakout followed by a retest pattern.
    Martin Luk: After a breakout, price pulls back to test the breakout
    level (former resistance → now support). Combined with AVWAP confluence.

    Returns:
        dict with 'is_retest', 'breakout_level', 'retest_date', 'avwap_near'.
    """
    if df.empty or len(df) < lookback + 20:
        return {"is_retest": False, "breakout_level": None,
                "retest_date": None, "avwap_near": False}

    # Find the recent breakout: highest close in lookback that was a new high
    recent = df.tail(lookback + 20)
    prior = recent.iloc[:20]
    post = recent.iloc[20:]

    if prior.empty or post.empty:
        return {"is_retest": False, "breakout_level": None,
                "retest_date": None, "avwap_near": False}

    # Resistance = highest close in prior period
    resistance = float(prior["Close"].max())
    # Did price break above resistance in post period?
    breakout_bars = post[post["Close"] > resistance * 1.02]
    if breakout_bars.empty:
        return {"is_retest": False, "breakout_level": None,
                "retest_date": None, "avwap_near": False}

    breakout_date_idx = breakout_bars.index[0]
    after_bo = post.loc[breakout_date_idx:]

    # Check if price came back to test the resistance level (within 2%)
    last_close = float(df["Close"].iloc[-1])
    within_retest = abs(last_close / resistance - 1.0) * 100.0 < 3.0

    # AVWAP confluence check
    avwap_data = dp.get_avwap_from_swing_high(df, lookback_bars=120)
    avwap_near = False
    if avwap_data["avwap_current"] is not None:
        avwap_pct = abs(last_close / avwap_data["avwap_current"] - 1.0) * 100.0
        avwap_near = avwap_pct < 3.0

    return {
        "is_retest":      within_retest,
        "breakout_level": round(resistance, 2),
        "retest_date":    str(breakout_bars.index[0])[:10] if not breakout_bars.empty else None,
        "avwap_near":     avwap_near,
    }


def _detect_character_change(df: pd.DataFrame) -> dict:
    """
    Detect a Character Change — stock transitioning from Stage 1 (base)
    to Stage 2 (uptrend). Martin Luk: "The first sign of life after a long base."

    Signals:
      - EMA 9 crossing above EMA 21 (Golden Cross on EMAs)
      - Volume surge on the cross
      - Price was below 50 EMA for extended period, now reclaiming

    Returns:
        dict with 'is_char_change', 'ema9_cross_ema21', 'reclaiming_ema50'.
    """
    if df.empty or len(df) < 60:
        return {"is_char_change": False, "ema9_cross_ema21": False,
                "reclaiming_ema50": False}

    # Check EMA 9/21 crossover in last 5 bars
    ema9_col = "EMA_9"
    ema21_col = "EMA_21"
    ema50_col = "EMA_50"

    if ema9_col not in df.columns or ema21_col not in df.columns:
        return {"is_char_change": False, "ema9_cross_ema21": False,
                "reclaiming_ema50": False}

    recent5 = df.tail(5)
    prev_bar = df.iloc[-6] if len(df) >= 6 else None

    # EMA9 crossed above EMA21 recently
    ema9_now = float(recent5[ema9_col].iloc[-1])
    ema21_now = float(recent5[ema21_col].iloc[-1])
    ema9_cross = ema9_now > ema21_now

    # Was EMA9 < EMA21 in prior bars?
    cross_happened = False
    if prev_bar is not None:
        ema9_before = float(prev_bar[ema9_col]) if ema9_col in prev_bar.index else 0
        ema21_before = float(prev_bar[ema21_col]) if ema21_col in prev_bar.index else 0
        cross_happened = ema9_cross and (ema9_before <= ema21_before)

    # Reclaiming 50 EMA: was below for 10+ days, now above
    reclaiming = False
    if ema50_col in df.columns and len(df) >= 15:
        was_below = (df["Close"].iloc[-15:-5] < df[ema50_col].iloc[-15:-5]).sum() >= 5
        now_above = float(df["Close"].iloc[-1]) > float(df[ema50_col].iloc[-1])
        reclaiming = was_below and now_above

    is_char = cross_happened or (ema9_cross and reclaiming)

    return {
        "is_char_change":    is_char,
        "ema9_cross_ema21":  cross_happened,
        "reclaiming_ema50":  reclaiming,
    }


def _detect_ep_gap(df: pd.DataFrame) -> dict:
    """
    Detect Episodic Pivot — gap-up on catalyst.
    Martin uses similar criteria to Qullamaggie but with EMA context.
    """
    min_gap = getattr(C, "ML_SETUP_CONFIDENCE_MIN", 5.0)
    if df.empty or len(df) < 21:
        return {"has_gap": False, "gap_pct": 0.0, "vol_multiple": 0.0}

    prev_close = float(df["Close"].iloc[-2])
    today_open = float(df["Open"].iloc[-1])
    today_vol = float(df["Volume"].iloc[-1])
    avg_vol = float(df["Volume"].tail(21).iloc[:-1].mean())

    if prev_close <= 0:
        return {"has_gap": False, "gap_pct": 0.0, "vol_multiple": 0.0}

    gap_pct = (today_open / prev_close - 1.0) * 100.0
    vol_mult = today_vol / avg_vol if avg_vol > 0 else 0.0

    return {
        "has_gap": gap_pct >= 5.0 and vol_mult >= 2.0,
        "gap_pct": round(gap_pct, 2),
        "vol_multiple": round(vol_mult, 2),
    }


def _detect_parabolic(df: pd.DataFrame) -> dict:
    """
    Detect parabolic move — price extremely extended above EMAs.
    Martin: Parabolic stocks can offer quick gains but are very risky.
    Typically >40% above 9 EMA or >60% above 21 EMA.
    """
    ext_9 = getattr(C, "ML_EXTENDED_EMA9_PCT", 15.0)
    ext_21 = getattr(C, "ML_EXTENDED_EMA21_PCT", 20.0)

    if df.empty or len(df) < 30 or "EMA_9" not in df.columns:
        return {"is_parabolic": False, "pct_above_ema9": None, "pct_above_ema21": None}

    last_close = float(df["Close"].iloc[-1])
    ema9 = float(df["EMA_9"].iloc[-1])
    ema21 = float(df["EMA_21"].iloc[-1]) if "EMA_21" in df.columns else ema9

    pct_9 = (last_close / ema9 - 1.0) * 100.0 if ema9 > 0 else 0
    pct_21 = (last_close / ema21 - 1.0) * 100.0 if ema21 > 0 else 0

    is_para = pct_9 > ext_9 * 2.5 or pct_21 > ext_21 * 2.5  # truly parabolic

    return {
        "is_parabolic":    is_para,
        "pct_above_ema9":  round(pct_9, 2),
        "pct_above_ema21": round(pct_21, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def detect_setup_type(df: pd.DataFrame, ticker: str = "") -> dict:
    """
    Analyse a stock's price history and determine which Martin Luk setup
    type(s) it matches.

    Args:
        df:     Enriched OHLCV DataFrame (from get_enriched / get_historical)
        ticker: Stock symbol (for logging only)

    Returns:
        dict with:
            'primary_setup'    : str   — best-matching setup type
            'confidence'       : float — 0.0 to 1.0 confidence score
            'all_setups'       : list  — ranked list of (type, confidence) tuples
            'details'          : dict  — sub-analysis details for each setup
    """
    min_conf = getattr(C, "ML_SETUP_CONFIDENCE_MIN", 0.40)

    empty = {
        "primary_setup": "UNKNOWN",
        "confidence":    0.0,
        "all_setups":    [],
        "details":       {},
    }
    if df is None or df.empty or len(df) < 50:
        return empty

    candidates = []

    # ── 1. PB_EMA: Pullback to rising EMA ────────────────────────────────────
    pb_conf = 0.0
    pb_details = {}
    ema_align = dp.get_ema_alignment(df)
    pb_depth = dp.get_pullback_depth(df)

    # Check bounce from each EMA (9, 21, 50)
    best_bounce = None
    for ema_p in [9, 21, 50]:
        bounce = _detect_ema_bounce(df, ema_period=ema_p)
        if bounce["is_bouncing"]:
            best_bounce = {"ema": ema_p, **bounce}
            break

    vol_dry = _volume_dry_up(df)
    vol_surge_data = _volume_surge(df)

    if best_bounce is not None and best_bounce.get("is_bouncing"):
        pb_conf = 0.50
        if ema_align.get("all_stacked"):
            pb_conf += 0.15
        if ema_align.get("all_rising"):
            pb_conf += 0.10
        if vol_dry.get("is_dry"):
            pb_conf += 0.10
        if vol_surge_data.get("has_surge"):
            pb_conf += 0.10
        if pb_depth.get("pullback_quality") in ("ideal",):
            pb_conf += 0.05
    elif pb_depth.get("pullback_quality") in ("ideal", "acceptable"):
        # Even without a confirmed bounce, a pullback to EMA in a stacked trend
        pb_conf = 0.30
        if ema_align.get("all_stacked"):
            pb_conf += 0.10
        if vol_dry.get("is_dry"):
            pb_conf += 0.10

    pb_details = {
        "ema_alignment": ema_align,
        "pullback_depth": pb_depth,
        "bounce": best_bounce or {},
        "volume_dry_up": vol_dry,
        "volume_surge": vol_surge_data,
    }
    if pb_conf >= min_conf:
        candidates.append(("PB_EMA", round(pb_conf, 3), pb_details))

    # ── 2. BR_RETEST: Breakout retest ────────────────────────────────────────
    br_conf = 0.0
    retest = _detect_breakout_retest(df)
    if retest["is_retest"]:
        br_conf = 0.50
        if retest.get("avwap_near"):
            br_conf += 0.20
        if ema_align.get("all_stacked"):
            br_conf += 0.10
        if vol_dry.get("is_dry"):
            br_conf += 0.10
    br_details = {"retest": retest}
    if br_conf >= min_conf:
        candidates.append(("BR_RETEST", round(br_conf, 3), br_details))

    # ── 3. BREAKOUT: Classic breakout on volume ──────────────────────────────
    bo_conf = 0.0
    if len(df) >= 30:
        # 20-day high breakout
        high_20 = float(df["High"].tail(21).iloc[:-1].max())
        last_close = float(df["Close"].iloc[-1])
        if last_close > high_20 * 1.01:  # >1% above 20-day high
            bo_conf = 0.40
            if vol_surge_data.get("has_surge"):
                bo_conf += 0.20
            if vol_surge_data.get("is_strong"):
                bo_conf += 0.10
            if ema_align.get("all_stacked"):
                bo_conf += 0.10
    bo_details = {"vol_surge": vol_surge_data}
    if bo_conf >= min_conf:
        candidates.append(("BREAKOUT", round(bo_conf, 3), bo_details))

    # ── 4. EP: Episodic Pivot ────────────────────────────────────────────────
    ep_conf = 0.0
    ep_data = _detect_ep_gap(df)
    if ep_data["has_gap"]:
        ep_conf = 0.55
        if ep_data["vol_multiple"] >= 3.0:
            ep_conf += 0.15
        if ep_data["gap_pct"] >= 8.0:
            ep_conf += 0.10
    ep_details = {"gap": ep_data}
    if ep_conf >= min_conf:
        candidates.append(("EP", round(ep_conf, 3), ep_details))

    # ── 5. CHAR_CHG: Character Change ────────────────────────────────────────
    cc_conf = 0.0
    cc_data = _detect_character_change(df)
    if cc_data["is_char_change"]:
        cc_conf = 0.45
        if cc_data["ema9_cross_ema21"]:
            cc_conf += 0.15
        if cc_data["reclaiming_ema50"]:
            cc_conf += 0.15
        if vol_surge_data.get("has_surge"):
            cc_conf += 0.10
    cc_details = {"char_change": cc_data}
    if cc_conf >= min_conf:
        candidates.append(("CHAR_CHG", round(cc_conf, 3), cc_details))

    # ── 6. PARABOLIC: Extreme extension ──────────────────────────────────────
    para_conf = 0.0
    para_data = _detect_parabolic(df)
    if para_data["is_parabolic"]:
        para_conf = 0.50
        pct9 = para_data.get("pct_above_ema9", 0) or 0
        if pct9 > 50:
            para_conf += 0.20
    para_details = {"parabolic": para_data}
    if para_conf >= min_conf:
        candidates.append(("PARABOLIC", round(para_conf, 3), para_details))

    # ── Rank and return ──────────────────────────────────────────────────────
    if not candidates:
        empty["details"] = {
            "ema_alignment": ema_align,
            "pullback_depth": pb_depth,
            "volume_dry_up": vol_dry,
        }
        return empty

    candidates.sort(key=lambda x: x[1], reverse=True)
    best_type, best_conf, best_det = candidates[0]

    # Merge all details
    all_details = {}
    for _, _, det in candidates:
        all_details.update(det)

    return {
        "primary_setup": best_type,
        "confidence":    best_conf,
        "all_setups":    [(t, c) for t, c, _ in candidates],
        "details":       all_details,
    }


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 ENHANCEMENTS — New detection functions added per Martin Luk system
# Reference: MartinLukStockGuidePart1.md + Part2.md + MartinLukCore.md
# ═════════════════════════════════════════════════════════════════════════════

def detect_higher_lows(df: pd.DataFrame, lookback: int = None) -> dict:
    """
    Detect progressively higher swing lows during pullback.

    Martin Luk (Chapter 5): "One of the best pullback setups shows
    progressively higher lows approaching EMA support — sellers are
    losing strength with each wave down."

    Args:
        df:       Enriched OHLCV DataFrame
        lookback: Bars to examine (default: ML_HIGHER_LOW_LOOKBACK)

    Returns:
        dict with 'has_higher_lows', 'count', 'swing_lows', 'quality'
    """
    if lookback is None:
        lookback = getattr(C, "ML_HIGHER_LOW_LOOKBACK", 20)

    empty = {
        "has_higher_lows": False,
        "count": 0,
        "swing_lows": [],
        "quality": "none",
        "adjustment": 0.0,
    }
    if df is None or df.empty or len(df) < lookback:
        return empty

    window = df.tail(lookback)
    lows = window["Low"].values
    n = len(lows)

    # Find swing lows: local minima (lower than neighbors on both sides)
    swing_lows_idx = []
    for i in range(1, n - 1):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows_idx.append(i)

    # Need at least 2 swing lows to establish a trend
    min_count = getattr(C, "ML_HIGHER_LOW_MIN_COUNT", 2)
    if len(swing_lows_idx) < min_count:
        return empty

    swing_low_values = [float(lows[i]) for i in swing_lows_idx]
    swing_low_dates  = [str(window.index[i])[:10] for i in swing_lows_idx]

    # Count ascending sequences
    ascending_count = 0
    for i in range(1, len(swing_low_values)):
        if swing_low_values[i] > swing_low_values[i - 1]:
            ascending_count += 1

    # Quality assessment
    total_pairs = len(swing_low_values) - 1
    if total_pairs <= 0:
        return empty

    ascending_ratio = ascending_count / total_pairs
    has_higher_lows = ascending_count >= min_count - 1

    if ascending_ratio >= 0.85:
        quality = "excellent"
    elif ascending_ratio >= 0.67:
        quality = "good"
    elif ascending_ratio >= 0.50:
        quality = "moderate"
    else:
        quality = "poor"
        has_higher_lows = False

    # Star adjustment
    adj_per = getattr(C, "ML_HIGHER_LOW_ADJ_PER", 0.3)
    max_adj = getattr(C, "ML_HIGHER_LOW_MAX_ADJ", 0.6)
    adjustment = min(ascending_count * adj_per, max_adj) if has_higher_lows else 0.0

    return {
        "has_higher_lows": has_higher_lows,
        "count":           ascending_count,
        "swing_lows":      list(zip(swing_low_dates, [round(v, 2) for v in swing_low_values])),
        "quality":         quality,
        "ascending_ratio": round(ascending_ratio, 2),
        "adjustment":      round(adjustment, 2),
        "detail_zh": (
            f"遞升低點: {ascending_count} 個 (品質: {quality})" if has_higher_lows
            else "無明確遞升低點結構"
        ),
    }


def count_support_confluence(df: pd.DataFrame,
                              entry_price: float = None,
                              radius_pct: float = None) -> dict:
    """
    Count how many support levels converge near the entry price.

    Martin Luk (Chapter 5, 6): "Multiple support confluence is the
    most important factor for a high-probability entry. When EMA +
    AVWAP + Prior High + Unfilled Gap all align at the SAME price
    level, that's the highest-probability entry."

    Args:
        df:           Enriched OHLCV DataFrame
        entry_price:  Price to check confluence around (default: last close)
        radius_pct:   Window ±% to count as "at same level"

    Returns:
        dict with 'count', 'levels', 'assessment', 'adjustment'
    """
    if radius_pct is None:
        radius_pct = getattr(C, "ML_CONFLUENCE_RADIUS_PCT", 1.5)

    if df is None or df.empty or len(df) < 50:
        return {"count": 0, "levels": [], "assessment": "insufficient_data", "adjustment": 0.0}

    if entry_price is None:
        entry_price = float(df["Close"].iloc[-1])

    lo = entry_price * (1 - radius_pct / 100.0)
    hi = entry_price * (1 + radius_pct / 100.0)

    levels_found = []

    # ── Check each EMA ─────────────────────────────────────────────────────
    for period in [9, 21, 50, 150]:
        col = f"EMA_{period}"
        if col in df.columns:
            val = float(df[col].iloc[-1])
            if val > 0 and lo <= val <= hi:
                levels_found.append({
                    "type": f"EMA_{period}",
                    "value": round(val, 2),
                    "pct_from_price": round((val / entry_price - 1) * 100, 2),
                })

    # ── Check AVWAPs ───────────────────────────────────────────────────────
    try:
        avwap_h = dp.get_avwap_from_swing_high(df)
        avwap_l = dp.get_avwap_from_swing_low(df)
        for name, avwap_data in [("AVWAP_supply", avwap_h), ("AVWAP_support", avwap_l)]:
            val = avwap_data.get("avwap_current")
            if val and val > 0 and lo <= val <= hi:
                levels_found.append({
                    "type": name,
                    "value": round(val, 2),
                    "pct_from_price": round((val / entry_price - 1) * 100, 2),
                })
    except Exception:
        pass

    # ── Check prior swing high (breakout retest level) ─────────────────────
    if len(df) >= 30:
        prior_30 = df.iloc[-30:-5]
        if not prior_30.empty:
            prior_high = float(prior_30["High"].max())
            if lo <= prior_high <= hi:
                levels_found.append({
                    "type": "prior_swing_high",
                    "value": round(prior_high, 2),
                    "pct_from_price": round((prior_high / entry_price - 1) * 100, 2),
                })

    # ── Check unfilled gap (gap fill = magnetic support) ──────────────────
    if len(df) >= 5:
        for i in range(-5, -1):
            try:
                prev_close = float(df["Close"].iloc[i - 1])
                bar_open   = float(df["Open"].iloc[i])
                if bar_open > prev_close * 1.02:  # ≥2% gap up
                    gap_fill_lvl = prev_close
                    if lo <= gap_fill_lvl <= hi:
                        levels_found.append({
                            "type": "gap_fill",
                            "value": round(gap_fill_lvl, 2),
                            "pct_from_price": round((gap_fill_lvl / entry_price - 1) * 100, 2),
                        })
                        break
            except (IndexError, ZeroDivisionError):
                pass

    # ── Assessment ─────────────────────────────────────────────────────────
    count = len(levels_found)
    high_prob_threshold = getattr(C, "ML_CONFLUENCE_HIGH_PROB", 3)
    min_threshold       = getattr(C, "ML_CONFLUENCE_MIN_SETUP", 2)
    bonus_adj           = getattr(C, "ML_CONFLUENCE_BONUS_ADJ", 0.4)

    if count >= high_prob_threshold:
        assessment = "high_probability"
        adjustment = bonus_adj * (count - high_prob_threshold + 1)
    elif count >= min_threshold:
        assessment = "acceptable"
        adjustment = 0.2
    elif count == 1:
        assessment = "low"
        adjustment = 0.0
    else:
        assessment = "very_low"
        adjustment = -0.3

    # Cap maximum bonus
    adjustment = min(adjustment, bonus_adj * 2)

    return {
        "count":      count,
        "levels":     levels_found,
        "assessment": assessment,
        "adjustment": round(adjustment, 2),
        "detail_zh": (
            f"支撐匯聚: {count} 個層級 ({assessment})" if count > 0
            else "無明確支撐匯聚"
        ),
    }


def check_chase_lod(current_price: float, lod: float) -> dict:
    """
    Check if current price is already too far above Low of Day.

    Martin Luk (Chapter 4, 9): "If stock is already up > 3% from LOD
    by the time you want to enter → SKIP. You're buying someone else's
    profits. Wait for the next pullback."

    Args:
        current_price: Current price (or close)
        lod:           Low of the current trading day

    Returns:
        dict with 'is_chase', 'pct_above_lod', 'warning', 'recommendation'
    """
    max_chase = getattr(C, "ML_MAX_CHASE_ABOVE_LOD_PCT", 3.0)

    if lod is None or lod <= 0 or current_price is None or current_price <= 0:
        return {
            "is_chase": False, "pct_above_lod": None,
            "warning": "無法計算", "recommendation": "無數據",
        }

    pct_above_lod = (current_price / lod - 1.0) * 100.0
    is_chase = pct_above_lod > max_chase

    return {
        "is_chase":      is_chase,
        "pct_above_lod": round(pct_above_lod, 1),
        "max_allowed":   max_chase,
        "warning": (
            f"⚠ 追價風險: 已較當日低高出 {pct_above_lod:.1f}% (上限 {max_chase}%)"
            if is_chase else f"未追價: 距當日低 +{pct_above_lod:.1f}%"
        ),
        "recommendation": "SKIP — 等待下次回調" if is_chase else "入場時機可接受",
        "adjustment": getattr(C, "ML_CHASE_DIM_E_PENALTY", -0.8) if is_chase else 0.0,
    }


def compute_weekly_trend(df_weekly: pd.DataFrame) -> dict:
    """
    Check weekly chart EMA structure to validate (or veto) daily setup.

    Martin Luk (Chapter 12): "When Daily and Weekly conflict → trust Weekly."
    Weekly W-EMA10 > W-EMA40 AND rising → confirms daily uptrend.
    W-EMA10 < W-EMA40 AND declining → overrules daily bullish setup.

    Args:
        df_weekly: Weekly OHLCV DataFrame (resampled or directly fetched)

    Returns:
        dict with 'weekly_trend', 'w_ema10', 'w_ema40', 'conflict_with_daily',
                  'is_veto', 'adjustment'
    """
    empty = {
        "weekly_trend":        "unknown",
        "w_ema10":             None,
        "w_ema40":             None,
        "ema10_above_ema40":   False,
        "ema10_rising":        False,
        "conflict_with_daily": False,
        "is_veto":             False,
        "adjustment":          0.0,
        "detail_zh":           "週線數據不足",
    }

    if df_weekly is None or df_weekly.empty or len(df_weekly) < 40:
        return empty

    # Compute weekly EMAs
    ema10 = df_weekly["Close"].ewm(span=10, adjust=False).mean()
    ema40 = df_weekly["Close"].ewm(span=40, adjust=False).mean()

    w_ema10 = float(ema10.iloc[-1])
    w_ema40 = float(ema40.iloc[-1])

    ema10_above_ema40 = w_ema10 > w_ema40

    # Check if EMA10 has been rising (compare to N weeks ago)
    min_weeks = getattr(C, "ML_WEEKLY_UPTREND_MIN_WEEKS", 4)
    ema10_rising = False
    if len(ema10) >= min_weeks + 1:
        ema10_rising = float(ema10.iloc[-1]) > float(ema10.iloc[-(min_weeks + 1)])

    ema40_rising = False
    if len(ema40) >= min_weeks + 1:
        ema40_rising = float(ema40.iloc[-1]) > float(ema40.iloc[-(min_weeks + 1)])

    # Weekly trend assessment
    if ema10_above_ema40 and ema10_rising:
        weekly_trend = "uptrend"
        is_veto = False
        adjustment = 0.4
        detail_zh = f"週線上升趨勢: W-EMA10({w_ema10:.2f}) > W-EMA40({w_ema40:.2f}) ✓"
    elif ema10_above_ema40 and not ema10_rising:
        weekly_trend = "uptrend_weakening"
        is_veto = False
        adjustment = 0.1
        detail_zh = f"週線趨勢減弱: W-EMA10 仍 > EMA40 但上升動能減緩"
    elif not ema10_above_ema40 and not ema40_rising:
        # Clearest downtrend — hard veto if enabled
        weekly_trend = "downtrend"
        is_veto = getattr(C, "ML_WEEKLY_EMA_CONFLICT_HARD", True)
        adjustment = getattr(C, "ML_WEEKLY_VETO_PENALTY", -1.5)
        detail_zh = f"⛔ 週線下降趨勢: W-EMA10({w_ema10:.2f}) < W-EMA40({w_ema40:.2f}) — 日線與週線衝突"
    else:
        weekly_trend = "neutral"
        is_veto = False
        adjustment = -0.2
        detail_zh = f"週線中性: W-EMA10({w_ema10:.2f}) vs W-EMA40({w_ema40:.2f})"

    conflict_with_daily = weekly_trend in ("downtrend", "downtrend_weakening")

    return {
        "weekly_trend":        weekly_trend,
        "w_ema10":             round(w_ema10, 2),
        "w_ema40":             round(w_ema40, 2),
        "ema10_above_ema40":   ema10_above_ema40,
        "ema10_rising":        ema10_rising,
        "ema40_rising":        ema40_rising,
        "conflict_with_daily": conflict_with_daily,
        "is_veto":             is_veto,
        "adjustment":          round(adjustment, 2),
        "detail_zh":           detail_zh,
    }


def detect_flush_v_recovery(df_intraday: pd.DataFrame,
                             support_levels: list = None) -> dict:
    """
    Detect flush-to-support then V-shaped recovery pattern.

    Martin Luk (Chapter 7): Preferred intraday setup: stock flushes down
    quickly to a key support level (EMA or AVWAP), then reverses in a
    V-shape (not gradual). Entry trigger: break of prior bar high.

    Args:
        df_intraday:    Intraday OHLCV DataFrame (1-min or 5-min bars)
        support_levels: List of price levels (EMAs, AVWAPs) to check flush to

    Returns:
        dict with 'detected', 'flush_depth_pct', 'trigger_price',
                  'stop_price', 'confidence'
    """
    empty = {
        "detected":       False,
        "flush_depth_pct": 0.0,
        "trigger_price":  None,
        "stop_price":     None,
        "confidence":     0.0,
        "detail_zh":      "無日內數據",
    }

    if df_intraday is None or df_intraday.empty or len(df_intraday) < 5:
        return empty

    flush_max_min = getattr(C, "ML_FLUSH_MAX_MINUTES", 15)
    flush_min_depth = getattr(C, "ML_FLUSH_MIN_DEPTH_PCT", 1.0)
    recovery_min_bars = getattr(C, "ML_VRECOVERY_MIN_BARS", 2)
    recovery_speed = getattr(C, "ML_VRECOVERY_SPEED_RATIO", 0.5)

    opens = df_intraday["Open"].values
    highs = df_intraday["High"].values
    lows = df_intraday["Low"].values
    closes = df_intraday["Close"].values
    n = len(closes)

    # Use first bar open as reference
    open_price = float(opens[0])

    # Find the flush: minimum close in first N bars
    flush_window = min(flush_max_min, n)
    flush_region = closes[:flush_window]
    flush_idx = int(np.argmin(flush_region))
    flush_low = float(lows[flush_idx])

    flush_depth = (open_price - flush_low) / open_price * 100.0 if open_price > 0 else 0.0

    if flush_depth < flush_min_depth:
        return {**empty, "flush_depth_pct": round(flush_depth, 2),
                "detail_zh": f"回撤深度不足 (僅 {flush_depth:.1f}%)"}

    # Check V-recovery: after flush, does price recover strongly?
    after_flush = closes[flush_idx:]
    if len(after_flush) < recovery_min_bars + 1:
        return {**empty, "flush_depth_pct": round(flush_depth, 2),
                "detail_zh": "回調後數據不足以確認 V 型反轉"}

    # Recovery = how much of the flush did price reclaim
    current_close = float(closes[-1])
    recovered_amount = current_close - flush_low
    recovery_ratio = recovered_amount / (open_price - flush_low) if (open_price - flush_low) > 0 else 0.0

    # V-shape = recovery must be at least 50% of flush, with consecutive up bars
    up_bars = sum(1 for i in range(flush_idx + 1, min(flush_idx + 5, n))
                  if closes[i] > closes[i - 1])

    is_v_recovery = (
        recovery_ratio >= recovery_speed
        and up_bars >= recovery_min_bars
    )

    if not is_v_recovery:
        return {
            **empty,
            "flush_depth_pct": round(flush_depth, 2),
            "recovery_ratio":  round(recovery_ratio, 2),
            "detail_zh": f"回調 {flush_depth:.1f}% 但無 V 型反轉 (回復比: {recovery_ratio:.0%})",
        }

    # Entry trigger: high of previous bar (conservative)
    trigger_price = round(float(highs[-2]), 2) if n >= 2 else round(float(highs[-1]), 2)
    stop_price = round(flush_low * (1 - getattr(C, "ML_LOD_STOP_BUFFER_PCT", 0.3) / 100), 2)

    # Confidence
    conf = 0.55
    if up_bars >= 3:
        conf += 0.10
    if recovery_ratio >= 0.75:
        conf += 0.10
    if flush_depth >= 2.5:
        conf += 0.05
    # If price flushed to a known support level
    if support_levels:
        for lvl in support_levels:
            if lvl and abs(flush_low / lvl - 1.0) <= 0.015:
                conf += 0.15
                break

    return {
        "detected":        True,
        "flush_depth_pct": round(flush_depth, 2),
        "recovery_ratio":  round(recovery_ratio, 2),
        "trigger_price":   trigger_price,
        "stop_price":      stop_price,
        "up_bars":         up_bars,
        "flush_idx":       flush_idx,
        "confidence":      round(min(conf, 1.0), 2),
        "detail_zh": (
            f"✓ Flush→V反轉 偵測: 回調 {flush_depth:.1f}% 至支撐, "
            f"回復 {recovery_ratio:.0%}, 入場觸發 ${trigger_price}"
        ),
    }


def detect_orh_breakout(df_intraday: pd.DataFrame,
                         range_minutes: int = None) -> dict:
    """
    Detect Opening Range High (ORH) breakout signal.

    Martin Luk (Chapter 7): For Episodic Pivot (EP) trades, use
    opening range (first N minutes) high/low as entry/stop reference.
    Break above ORH = entry; ORH low = stop.

    Args:
        df_intraday:    Intraday OHLCV DataFrame
        range_minutes:  Opening range length in minutes

    Returns:
        dict with 'orh_price', 'orl_price', 'current_price',
                  'is_above_orh', 'signal', 'stop_price'
    """
    if range_minutes is None:
        range_minutes = getattr(C, "ML_ORH_RANGE_MINUTES", 5)

    empty = {
        "orh_price": None, "orl_price": None,
        "is_above_orh": False, "signal": "NO_SIGNAL",
        "stop_price": None, "confidence": 0.0,
        "detail_zh": "無日內數據",
    }

    if df_intraday is None or df_intraday.empty or len(df_intraday) < range_minutes + 1:
        return empty

    # Opening range = first N bars
    opening_range = df_intraday.head(range_minutes)
    orh = float(opening_range["High"].max())
    orl = float(opening_range["Low"].min())
    current_close = float(df_intraday["Close"].iloc[-1])
    range_size = orh - orl

    is_above_orh = current_close > orh * 1.001  # 0.1% buffer

    # Signal strength
    signal = "NO_SIGNAL"
    confidence = 0.0
    if is_above_orh:
        breakout_ext = (current_close - orh) / orh * 100.0
        vol_surge_data = _volume_surge(df_intraday)
        signal = "ORH_BREAKOUT"
        confidence = 0.50
        if vol_surge_data.get("has_surge"):
            confidence += 0.20
        if breakout_ext > 1.0:
            confidence += 0.10
    elif abs(current_close - orh) / orh < 0.005:  # within 0.5% of ORH
        signal = "AT_ORH"
        confidence = 0.30

    return {
        "orh_price":    round(orh, 2),
        "orl_price":    round(orl, 2),
        "range_size":   round(range_size, 2),
        "current_price": round(current_close, 2),
        "is_above_orh": is_above_orh,
        "signal":       signal,
        "stop_price":   round(orl, 2),  # Stop at ORH low
        "confidence":   round(confidence, 2),
        "detail_zh": (
            f"✓ ORH 突破: 開盤區間 ${orl:.2f}–${orh:.2f}, 當前 ${current_close:.2f}"
            if is_above_orh else
            f"開盤區間高 ${orh:.2f} — {'接近突破' if signal == 'AT_ORH' else '未突破'}"
        ),
    }


def get_intraday_trigger_type(minutes_since_open: int) -> dict:
    """
    Return the appropriate intraday trigger type based on time since open.

    Martin Luk (Chapter 7) time-specific rules:
      0–15 min:   Use 1-minute previous bar high as trigger
      15–60 min:  Use 5-minute previous bar high as trigger
      > 60 min:   Use standard 5-minute consolidation breakout

    Args:
        minutes_since_open: Minutes elapsed since market open

    Returns:
        dict with 'phase', 'timeframe', 'trigger_type', 'description_zh'
    """
    phase1_end = getattr(C, "ML_INTRADAY_PHASE1_MINUTES", 15)
    phase2_end = getattr(C, "ML_INTRADAY_PHASE2_MINUTES", 60)

    if minutes_since_open <= phase1_end:
        return {
            "phase":       1,
            "timeframe":   "1min",
            "trigger_type": "prev_bar_high_1min",
            "description_zh": f"開盤 {minutes_since_open} 分鐘內: 用 1 分鐘前一根 K 線高點突破",
            "stop_type":   "prev_1min_bar_low",
        }
    elif minutes_since_open <= phase2_end:
        return {
            "phase":       2,
            "timeframe":   "5min",
            "trigger_type": "prev_bar_high_5min",
            "description_zh": f"開盤 {minutes_since_open} 分鐘: 用 5 分鐘前一根 K 線高點突破",
            "stop_type":   "prev_5min_bar_low",
        }
    else:
        return {
            "phase":       3,
            "timeframe":   "5min",
            "trigger_type": "consolidation_breakout_5min",
            "description_zh": f"開盤 {minutes_since_open} 分鐘後: 用 5 分鐘整理後突破",
            "stop_type":   "5min_consolidation_low",
        }


def compute_parabolic_checklist(df: pd.DataFrame, direction: str = "long") -> dict:
    """
    Evaluate Martin Luk's Appendix E Parabolic Trade Checklist.

    Reference: MartinLukStockGuidePart2.md Chapter 14, Appendix E
    Separate checklists for Long (index sold off sharply) and
    Short (stock extended far above EMAs).

    Args:
        df:        Daily OHLCV DataFrame
        direction: "long" (buying parabolic down) or "short" (shorting parabolic up)

    Returns:
        dict with 'checklist', 'score', 'max_score', 'pct', 'is_valid',
                  'recommendation', 'special_exits'
    """
    if df is None or df.empty or len(df) < 30:
        return {"is_valid": False, "score": 0, "detail_zh": "數據不足"}

    close = float(df["Close"].iloc[-1])
    checklist = {}

    if direction == "long":
        # Long parabolic: index/stock sold off sharply → catching the bounce
        # Pre-conditions from Chapter 14: TQQ trade example
        ema9 = float(df["EMA_9"].iloc[-1]) if "EMA_9" in df.columns else None
        ema9_criterion = False
        idx_below_ema9_pct = None
        gap_down_count = 0

        if ema9 and ema9 > 0:
            idx_below_ema9_pct = (close / ema9 - 1.0) * 100.0
            min_below = getattr(C, "ML_PARABOLIC_IDX_BELOW_EMA9", 15.0)
            ema9_criterion = idx_below_ema9_pct <= -min_below

        # Count consecutive gap-down days
        for i in range(-1, -6, -1):
            try:
                if float(df["Open"].iloc[i]) < float(df["Close"].iloc[i - 1]) * 0.99:
                    gap_down_count += 1
                else:
                    break
            except IndexError:
                break

        min_gap_downs = getattr(C, "ML_PARABOLIC_GAP_DOWN_COUNT", 3)
        gap_criterion = gap_down_count >= min_gap_downs

        checklist = {
            "consecutive_gap_downs":   {"pass": gap_criterion, "value": gap_down_count,
                                         "description": f"連續跳空低開 ≥ {min_gap_downs} 天: {gap_down_count} 天"},
            "price_far_below_ema9":    {"pass": ema9_criterion, "value": idx_below_ema9_pct,
                                         "description": f"價格低於 9 EMA ≥ 15%: {idx_below_ema9_pct:.1f}%" if idx_below_ema9_pct else "無 EMA 數據"},
            "is_orh_entry_plan":       {"pass": True, "value": "manual",
                                         "description": "計劃用 ORH/1分鐘突破入場 ✓"},
            "full_exit_plan":          {"pass": True, "value": "all_into_strength",
                                         "description": "計劃強勢中全部出場 (非部分減倉) ✓"},
            "no_overnight_plan":       {"pass": True, "value": "intraday",
                                         "description": "拋物線交易通常當日內完成 ✓"},
            "intraday_profit_targets": {"pass": True, "value": "ema/ma",
                                         "description": "利潤目標: 小時 9 EMA / 5分鐘 120-150 EMA ✓"},
        }
        special_exits = ["對下降匯聚 EMA 全部賣出", "有利時在高點強勢全部出場", "絕不持倉過夜"]

    else:  # short
        # Short parabolic: stock extended far above EMAs → reversal candidate
        ema9 = float(df["EMA_9"].iloc[-1]) if "EMA_9" in df.columns else None
        ema21 = float(df["EMA_21"].iloc[-1]) if "EMA_21" in df.columns else None
        far_above_ema9 = ((close / ema9 - 1.0) * 100.0 > 40.0) if ema9 else False
        far_above_ema21 = ((close / ema21 - 1.0) * 100.0 > 60.0) if ema21 else False

        checklist = {
            "stock_far_above_emas":     {"pass": far_above_ema9 or far_above_ema21, "value": True,
                                          "description": "股票大幅高於 EMA (拋物線) ✓" if (far_above_ema9 or far_above_ema21) else "延伸不足"},
            "quick_exit_2_3_days":      {"pass": True, "value": "2-3 days",
                                          "description": "計劃 2-3 日內平倉做空 ✓"},
            "iwm_lag_check":            {"pass": True, "value": "manual",
                                          "description": "IWM 也拋物線延伸? (手動確認)"},
            "smaller_position_gap_risk":{"pass": True, "value": "smaller",
                                          "description": "縮小倉位以應對跳空風險 ✓"},
        }
        special_exits = ["2-3 日強制了結", "接近 50 EMA 減倉", "避免超越盈利數學邊際"]

    # Score
    passed = sum(1 for v in checklist.values() if v.get("pass"))
    total  = len(checklist)
    pct    = passed / total * 100.0 if total > 0 else 0.0
    is_valid = pct >= 75.0

    return {
        "direction":     direction,
        "checklist":     checklist,
        "score":         passed,
        "max_score":     total,
        "pct":           round(pct, 1),
        "is_valid":      is_valid,
        "special_exits": special_exits,
        "recommendation": "✓ 拋物線交易條件達標" if is_valid else "⚠ 拋物線交易條件不足",
        "detail_zh": f"拋物線{'做多' if direction == 'long' else '做空'}檢查清單: {passed}/{total} ({pct:.0f}%)",
    }
