"""
modules/vcp_detector.py
───────────────────────
Volatility Contraction Pattern (VCP) auto-detection engine.

Core logic uses:
  • pandas_ta ATR(14)      — volatility magnitude
  • pandas_ta BBands(20)   — Bollinger Band Width (volatility proxy)
  • pandas_ta SMA(50/150)  — trend confirmation
  • Custom swing logic     — successive range contractions (T-count)
  • Volume analysis        — drying-up / exhaustion volume

Output per ticker:
  vcp_score       0-100
  t_count         number of contractions identified (T1, T2, T3...)
  contractions    list of {range_pct, vol_ratio} per contraction
  pivot_price     suggested breakout pivot level
  base_depth_pct  total depth of the base (%)
  base_weeks      base duration in weeks
  is_valid_vcp    bool — passes minimum Minervini VCP requirements
  grade           A / B / C / D
  notes           list of diagnostic strings
"""

import sys
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Public entry-point
# ═══════════════════════════════════════════════════════════════════════════════

def detect_vcp(df: pd.DataFrame) -> dict:
    """
    Run full VCP detection on an enriched OHLCV DataFrame.

    `df` must already have technical indicators from data_pipeline.get_technicals()
    (SMA_50, SMA_150, ATR_14, BBB_20_2.0, ABOVE_SMA50, ABOVE_SMA150, etc.)

    Returns a dict with the VCP analysis result.
    """
    empty = _empty_result()

    if df is None or len(df) < 60:
        empty["notes"].append("Insufficient data (need ≥60 days)")
        return empty

    # ── 1. Verify the stock is in an uptrend (Stage 2 context) ──────────────
    trend_ok, trend_notes = _check_trend_context(df)
    notes = list(trend_notes)

    if not trend_ok:
        empty["notes"] = notes + ["Not in Stage 2 uptrend — VCP not applicable"]
        return empty

    # ── 2. Find the most recent base / consolidation period ──────────────────
    base_start, base_end = _find_base(df)
    if base_start is None or (base_end - base_start) < 4 * 5:   # < 4 weeks
        empty["notes"] = notes + ["No valid base found (too short or no consolidation)"]
        return empty

    base_df = df.iloc[base_start:base_end + 1].copy()
    base_days = len(base_df)
    base_weeks = base_days / 5
    base_high = base_df["High"].max()
    base_low  = base_df["Low"].min()
    base_depth_pct = (base_high - base_low) / base_high * 100 if base_high > 0 else 0

    # ── 3. Base quality checks ───────────────────────────────────────────────
    if base_depth_pct > C.VCP_MAX_BASE_DEPTH:
        notes.append(f"Base too deep: {base_depth_pct:.1f}% (max {C.VCP_MAX_BASE_DEPTH}%)")
    if base_weeks < C.VCP_MIN_BASE_WEEKS:
        notes.append(f"Base too short: {base_weeks:.1f}wks (min {C.VCP_MIN_BASE_WEEKS})")
    if base_weeks > C.VCP_MAX_BASE_WEEKS:
        notes.append(f"Base very wide: {base_weeks:.1f}wks")

    # ── 4. Find successive swing contractions (T-count) ──────────────────────
    contractions = _find_contractions(base_df)
    t_count = len(contractions)

    if t_count < 1:
        notes.append("No swing contractions found — not a VCP")
        return {**empty, "base_depth_pct": round(base_depth_pct, 1),
                "base_weeks": round(base_weeks, 1), "notes": notes}

    # Check that contractions are genuinely shrinking
    is_contracting = _verify_contraction_sequence(contractions)
    if not is_contracting:
        notes.append("Contractions not shrinking sequentially — weak VCP")

    # ── 5. ATR trend (is volatility declining through the base?) ─────────────
    atr_contracting, atr_ratio = _check_atr_contraction(base_df)
    if atr_contracting:
        notes.append(f"ATR contracting ✓ (late/early ratio: {atr_ratio:.2f})")
    else:
        notes.append(f"ATR not clearly contracting ({atr_ratio:.2f})")

    # ── 6. BBands width (is it narrowing?) ───────────────────────────────────
    bb_contracting, bb_note = _check_bbands_contraction(base_df)
    notes.append(bb_note)

    # ── 7. Volume dry-up ─────────────────────────────────────────────────────
    vol_dry, vol_ratio = _check_volume_dryup(df, base_end)
    if vol_dry:
        notes.append(f"Volume exhaustion ✓ (recent vol {vol_ratio:.0%} of 50-day avg)")
    else:
        notes.append(f"Volume not yet exhausted ({vol_ratio:.0%} of 50-day avg)")

    # ── 8. Pivot / breakout level ─────────────────────────────────────────────
    pivot = _find_pivot(base_df)

    # ── 9. Scoring ────────────────────────────────────────────────────────────
    score = _score_vcp(
        t_count=t_count,
        contractions=contractions,
        is_contracting=is_contracting,
        atr_contracting=atr_contracting,
        bb_contracting=bb_contracting,
        vol_dry=vol_dry,
        base_depth_pct=base_depth_pct,
        base_weeks=base_weeks,
    )

    is_valid = bool(
        t_count >= C.VCP_MIN_CONTRACTIONS
        and base_weeks >= C.VCP_MIN_BASE_WEEKS
        and base_depth_pct <= C.VCP_MAX_BASE_DEPTH
    )

    grade = _assign_grade(score, is_valid)

    # Final contraction magnitude
    final_contraction_pct = contractions[-1]["range_pct"] if contractions else None
    if final_contraction_pct and final_contraction_pct <= C.VCP_FINAL_CONTRACTION_MAX:
        notes.append(f"Final contraction ≤10% ✓ ({final_contraction_pct:.1f}%)")
    elif final_contraction_pct:
        notes.append(f"Final contraction {final_contraction_pct:.1f}% (target <10%)")

    if is_valid:
        notes.insert(0, f"✅ VCP DETECTED — Grade {grade}, Score {score}/100")
    else:
        notes.insert(0, f"⚠️  Partial VCP — Grade {grade}, Score {score}/100")

    return {
        "vcp_score":        score,
        "is_valid_vcp":     bool(is_valid),
        "grade":            grade,
        "t_count":          t_count,
        "contractions":     contractions,
        "pivot_price":      round(float(pivot), 2) if pivot else None,
        "base_depth_pct":   float(round(base_depth_pct, 1)),
        "base_weeks":       float(round(base_weeks, 1)),
        "atr_contracting":  bool(atr_contracting),
        "bb_contracting":   bool(bb_contracting),
        "vol_dry":          bool(vol_dry),
        "vol_ratio":        float(round(vol_ratio, 2)),
        "notes":            notes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_result() -> dict:
    return {
        "vcp_score":       0,
        "is_valid_vcp":    False,
        "grade":           "D",
        "t_count":         0,
        "contractions":    [],
        "pivot_price":     None,
        "base_depth_pct":  None,
        "base_weeks":      None,
        "atr_contracting": False,
        "bb_contracting":  False,
        "vol_dry":         False,
        "vol_ratio":       1.0,
        "notes":           [],
    }


def _check_trend_context(df: pd.DataFrame) -> tuple:
    """
    Verify stock is in an uptrend (Stage 2) before checking VCP.
    Returns (bool, list_of_notes).
    """
    notes = []
    last = df.iloc[-1]
    close = last["Close"]

    checks_passed = 0
    total_checks = 0

    for sma_col, label in [("SMA_50", "SMA50"), ("SMA_150", "SMA150"), ("SMA_200", "SMA200")]:
        total_checks += 1
        if sma_col in df.columns and not pd.isna(last.get(sma_col)):
            if close > last[sma_col]:
                checks_passed += 1
            else:
                notes.append(f"Price below {label}")
        else:
            # Fallback: compute inline
            sma_val = df["Close"].rolling(int(sma_col.split("_")[1])).mean().iloc[-1]
            if not pd.isna(sma_val) and close > sma_val:
                checks_passed += 1
            else:
                notes.append(f"Price below {label}")

    ok = checks_passed >= 2  # At least above 2 of 3 SMAs

    # C4: Stage 2 also requires proper SMA alignment: SMA50 > SMA150 > SMA200
    def _get_sma(col: str) -> float | None:
        period = int(col.split("_")[1])
        if col in df.columns and not pd.isna(last.get(col)):
            return float(last[col])
        val = df["Close"].rolling(period).mean().iloc[-1]
        return float(val) if not pd.isna(val) else None

    sma50_v  = _get_sma("SMA_50")
    sma150_v = _get_sma("SMA_150")
    sma200_v = _get_sma("SMA_200")

    alignment_ok = (
        sma50_v is not None and sma150_v is not None and sma200_v is not None
        and float(sma50_v) > float(sma150_v) > float(sma200_v)
    )
    if not alignment_ok:
        notes.append("SMA alignment failed (need SMA50 > SMA150 > SMA200)")

    # Require price above ALL 3 SMAs AND proper SMA bull alignment
    ok = (checks_passed == 3) and alignment_ok
    return ok, notes


def _find_base(df: pd.DataFrame) -> tuple:
    """
    Find the most suitable consolidation base.

    C6 fix: Prefer recent bases in the 6-26 week ideal range.
    Penalises excessively long (>26 week) bases so META-style 60-week bases
    don’t dominate; bias toward the most recent valid end date.

    Returns (start_idx, end_idx) into df.iloc, or (None, None).
    """
    min_base_days = int(C.VCP_MIN_BASE_WEEKS * 5)
    max_base_days = int(C.VCP_MAX_BASE_WEEKS * 5)
    ideal_max_days = 26 * 5   # 26 weeks = 130 days (Minervini ideal)
    n = len(df)

    best_start, best_end = None, None

    for end_i in range(n - 1, max(n - 1 - max_base_days, min_base_days), -1):
        local_best_start = None
        local_best_score = -1.0

        for start_i in range(max(0, end_i - max_base_days), end_i - min_base_days):
            segment  = df.iloc[start_i:end_i + 1]
            seg_high = segment["High"].max()
            seg_low  = segment["Low"].min()
            if seg_high == 0:
                continue
            depth_pct = (seg_high - seg_low) / seg_high * 100
            length    = end_i - start_i + 1

            if not (C.VCP_MIN_BASE_DEPTH <= depth_pct <= C.VCP_MAX_BASE_DEPTH
                    and length >= min_base_days):
                continue

            # Prefer bases inside the ideal window; penalise excess length 2:1
            overlong = max(0, length - ideal_max_days)
            score    = length - overlong * 2

            if score > local_best_score:
                local_best_start = start_i
                local_best_score = score

        if local_best_start is not None:
            best_start = local_best_start
            best_end   = end_i
            break   # Most-recent valid end_i found — stop searching

    return best_start, best_end


def _find_contractions(base_df: pd.DataFrame) -> list:
    """
    Identify successive price swing contractions within the base using
    real swing high / swing low detection (C3 fix).

    A contraction is the range from a swing high down to the next swing low.
    Each successive contraction should be smaller (T-1 > T-2 > T-3 …).

    Returns a list of dicts:
      { "range_pct": float, "vol_ratio": float, "seg_high": float, "seg_low": float }
    """
    n = len(base_df)
    if n < 20:
        return []

    highs   = base_df["High"].values.astype(float)
    lows    = base_df["Low"].values.astype(float)
    avg_vol = float(base_df["Volume"].mean()) if "Volume" in base_df.columns else 1.0

    # Adaptive window: ~1/15 of base length, clamped 3–7
    window = max(3, min(7, n // 15))

    # Detect swing highs and swing lows
    swing_highs: list = []   # (bar_index, price)
    swing_lows:  list = []   # (bar_index, price)

    for i in range(window, n - window):
        local_high = float(np.max(highs[i - window: i + window + 1]))
        local_low  = float(np.min(lows[i  - window: i + window + 1]))
        if highs[i] >= local_high:
            swing_highs.append((i, float(highs[i])))
        if lows[i] <= local_low:
            swing_lows.append((i, float(lows[i])))

    # Build contraction sequence: each SH paired with the first SL that follows it
    contractions: list = []
    used_low_idx = -1

    for sh_idx, sh_price in swing_highs:
        for sl_idx, sl_price in swing_lows:
            if sl_idx > sh_idx and sl_idx > used_low_idx:
                range_pct = (sh_price - sl_price) / sh_price * 100.0 if sh_price > 0 else 0.0
                seg_vol   = (
                    float(base_df.iloc[sh_idx: sl_idx + 1]["Volume"].mean())
                    if "Volume" in base_df.columns and sl_idx > sh_idx
                    else avg_vol
                )
                vol_ratio = seg_vol / avg_vol if avg_vol > 0 else 1.0
                contractions.append({
                    "range_pct": round(float(range_pct), 1),
                    "vol_ratio": round(float(vol_ratio), 2),
                    "seg_high":  round(float(sh_price), 2),
                    "seg_low":   round(float(sl_price), 2),
                })
                used_low_idx = sl_idx
                break

    if contractions:
        return contractions

    # Fallback: 2-segment equal split when swing detection yields nothing
    mid = n // 2
    result = []
    for seg in [base_df.iloc[:mid], base_df.iloc[mid:]]:
        sh = float(seg["High"].max())
        sl = float(seg["Low"].min())
        range_pct = (sh - sl) / sh * 100.0 if sh > 0 else 0.0
        vol_ratio = float(seg["Volume"].mean()) / avg_vol if avg_vol > 0 else 1.0
        result.append({
            "range_pct": round(range_pct, 1),
            "vol_ratio": round(vol_ratio, 2),
            "seg_high":  round(sh, 2),
            "seg_low":   round(sl, 2),
        })
    return result


def _verify_contraction_sequence(contractions: list) -> bool:
    """
    Verify that each contraction is progressively smaller than the previous.
    Allows one minor increase if the overall trend is down.
    """
    if len(contractions) < 2:
        return False

    ranges = [c["range_pct"] for c in contractions]
    decreasing_count = sum(
        1 for i in range(1, len(ranges))
        if ranges[i] < ranges[i - 1]
    )
    return decreasing_count >= len(ranges) - 1  # Majority decreasing


def _check_atr_contraction(base_df: pd.DataFrame) -> tuple:
    """
    Check if ATR is declining through the base.
    Compares average ATR in the first half vs second half.
    Returns (is_contracting: bool, late_early_ratio: float).
    """
    atr_col = None
    for col in ["ATR_14", "ATRr_14"]:
        if col in base_df.columns:
            atr_col = col
            break

    if atr_col is None:
        # Compute simple range as proxy
        base_df = base_df.copy()
        base_df["_range"] = base_df["High"] - base_df["Low"]
        atr_col = "_range"

    atr = base_df[atr_col].dropna()
    if len(atr) < 10:
        return False, 1.0

    mid = len(atr) // 2
    early_avg = atr.iloc[:mid].mean()
    late_avg  = atr.iloc[mid:].mean()

    if early_avg == 0:
        return False, 1.0

    ratio = late_avg / early_avg
    return bool(ratio < 0.85), round(float(ratio), 3)   # Declining = ratio < 1


def _check_bbands_contraction(base_df: pd.DataFrame) -> tuple:
    """
    Check if Bollinger Band Width is narrowing in the latter half of the base.
    Returns (is_contracting: bool, note: str).
    """
    bbw_col = "BBB_20_2.0"
    if bbw_col not in base_df.columns:
        # Try alternate naming
        for col in base_df.columns:
            if col.startswith("BBB_"):
                bbw_col = col
                break
        else:
            return False, "BBands width data unavailable"

    bbw = base_df[bbw_col].dropna()
    if len(bbw) < 20:
        return False, "Insufficient BBands data"

    recent_pct25 = bbw.quantile(0.25)
    current_bbw  = bbw.iloc[-1]

    contracting = current_bbw <= recent_pct25 * 1.1
    note = (
        f"BBands width at {current_bbw:.2f} (25th pctile: {recent_pct25:.2f}) ✓"
        if contracting
        else f"BBands width {current_bbw:.2f} above compressed level {recent_pct25:.2f}"
    )
    return bool(contracting), note


def _check_volume_dryup(df: pd.DataFrame, base_end_idx: int) -> tuple:
    """
    Detect volume exhaustion at the end of the base.
    Recent 5-day avg volume vs 50-day avg volume of the full series.
    Returns (is_dry: bool, ratio: float).
    """
    # 50-day average across the broader context
    context_start = max(0, base_end_idx - 50)
    context_vol   = df.iloc[context_start:base_end_idx + 1]["Volume"].mean()

    # Recent 5-day average (end of base)
    recent_end   = min(base_end_idx, len(df) - 1)
    recent_start = max(0, recent_end - 4)
    recent_vol   = df.iloc[recent_start:recent_end + 1]["Volume"].mean()

    if context_vol == 0:
        return False, 1.0

    ratio = recent_vol / context_vol
    return bool(ratio <= C.VCP_VOLUME_DRY_THRESHOLD), round(float(ratio), 3)


def _find_pivot(base_df: pd.DataFrame) -> Optional[float]:
    """
    Identify the pivot / breakout price level from the final handle of the base.

    W3 fix: Uses a 3-bar rolling-max on High to smooth out single-day
    spike outliers (e.g., one anomalous intraday high skewing the level).
    Looks at the last 25% of the base.
    """
    n = len(base_df)
    latter_start = max(0, int(n * 0.75))   # Last 25% of base
    latter = base_df.iloc[latter_start:].copy()
    if latter.empty:
        return float(base_df["High"].max())
    # 3-bar rolling max smooths out single-day spikes
    smoothed = latter["High"].rolling(window=3, min_periods=1).max()
    return float(smoothed.max())


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════════════════════════

def _score_vcp(t_count, contractions, is_contracting,
               atr_contracting, bb_contracting, vol_dry,
               base_depth_pct, base_weeks) -> int:
    """
    Compute a 0-100 VCP quality score.
    """
    score = 0

    # T-count (real swing contractions after C3 fix — no longer artificially inflated)
    t_score = min(t_count * 10, 20)   # Max 20 pts (T-3 ideal)
    score += t_score

    # Contractions are genuinely shrinking
    if is_contracting:
        score += 15

    # ATR declining
    if atr_contracting:
        score += 15

    # BBands width compressed
    if bb_contracting:
        score += 15

    # Volume dry-up (W1: Minervini’s most critical final signal — raised to 25)
    if vol_dry:
        score += 25

    # Base depth quality (12-35% is ideal, per Minervini)
    if C.VCP_MIN_BASE_DEPTH < base_depth_pct <= 35.0:
        score += 5
    elif 35.0 < base_depth_pct <= C.VCP_MAX_BASE_DEPTH:
        score += 2

    # Base width quality (6-16 weeks is ideal)
    if 6 <= base_weeks <= 16:
        score += 5
    elif 4 <= base_weeks < 6:
        score += 2

    # Final contraction < 10% (ideal per Minervini)
    if contractions:
        final_range = contractions[-1]["range_pct"]
        if final_range <= 5.0:
            score += 5
        elif final_range <= 10.0:
            score += 3

    return min(score, 100)


def _assign_grade(score: int, is_valid: bool) -> str:
    if not is_valid:
        return "D"
    if score >= 80:
        return "A"
    elif score >= 60:
        return "B"
    elif score >= 40:
        return "C"
    else:
        return "D"
