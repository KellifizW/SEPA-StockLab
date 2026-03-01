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
