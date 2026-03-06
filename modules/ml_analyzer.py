"""
modules/ml_analyzer.py  —  Martin Luk 7-Dimension Pullback Rating Engine
═══════════════════════════════════════════════════════════════════════
Implements Martin Luk's pullback quality scoring system for deep
single-stock analysis.

Seven rating dimensions:
  A. EMA Structure       — stacking (9>21>50>150) + rising + slope
  B. Pullback Quality    — depth + type + volume dry-up during PB
  C. AVWAP Confluence    — support/reclaim from swing high/low AVWAP
  D. Volume Pattern      — dry-up on PB + surge on bounce
  E. Risk/Reward         — stop distance (must be <2.5%) + R:R ratio
  F. Relative Strength   — vs market momentum
  G. Market Environment  — regime check

Star rating → position sizing via formula:
  Shares = (Account × Risk%) / (Entry − Stop)
  Risk% = 0.50% per trade (max 0.75%)
  Max stop loss = 2.5%

All market data goes exclusively through data_pipeline.py.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ── ANSI terminal colours ───────────────────────────────────────────────────
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


# ─────────────────────────────────────────────────────────────────────────────
# Dimension A — EMA Structure
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_a(df: pd.DataFrame) -> dict:
    """
    A: EMA Structure scoring.
    Martin Luk's ideal: EMA9 > EMA21 > EMA50 > EMA150, all rising,
    price above all EMAs, with steep slope on 21 EMA.

    Adjustment range: [-2.0, +2.0]
    """
    from modules.data_pipeline import get_ema_alignment, get_ema_slope

    ema = get_ema_alignment(df)
    slope_21 = get_ema_slope(df, period=21)
    slope_50 = get_ema_slope(df, period=50)

    adj = 0.0
    detail = {}

    # Stacking
    if ema.get("all_stacked"):
        adj += 0.8
        detail["stacking"] = "完美排列 EMA9>21>50>150 ✓"
    elif ema.get("ema_21") and ema.get("ema_50"):
        e21 = ema.get("ema_21", 0) or 0
        e50 = ema.get("ema_50", 0) or 0
        if e21 > e50:
            adj += 0.4
            detail["stacking"] = "部分排列 EMA21>50 ✓"
        else:
            adj -= 0.3
            detail["stacking"] = "排列不佳"
    else:
        adj -= 0.5
        detail["stacking"] = "無EMA排列數據"

    # Rising
    if ema.get("all_rising"):
        adj += 0.5
        detail["rising"] = "所有EMA均上升 ✓"
    elif ema.get("ema_21_rising"):
        adj += 0.2
        detail["rising"] = "21 EMA上升"
    else:
        adj -= 0.3
        detail["rising"] = "EMA未上升"

    # Slope of 21 EMA
    slope_dir = slope_21.get("direction", "unknown")
    if slope_dir == "rising_fast":
        adj += 0.4
        detail["slope"] = "21 EMA 斜率陡峭 ✓"
    elif slope_dir == "rising":
        adj += 0.2
        detail["slope"] = "21 EMA 斜率上升"
    elif slope_dir == "flat":
        detail["slope"] = "21 EMA 斜率持平"
    else:
        adj -= 0.3
        detail["slope"] = "21 EMA 斜率下降"

    detail["ema_alignment"] = ema
    detail["slope_21"] = slope_21

    # ── Weekly chart check (Chapter 12 — "trust weekly over daily") ────────
    # Fetch weekly data and apply compute_weekly_trend()
    try:
        from modules.ml_setup_detector import compute_weekly_trend
        # Resample daily df to weekly
        df_weekly = df.resample("W").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna()
        wt = compute_weekly_trend(df_weekly)
        detail["weekly_trend"] = wt
        adj += wt["adjustment"]
        if wt.get("is_veto"):
            detail["weekly_veto"] = True
            # Propagate veto status to caller via is_veto flag
            adj = max(-2.0, min(adj, 2.0))
            detail["adjustment"] = round(adj, 2)
            return {"score": round(adj, 2), "detail": detail,
                    "is_veto": True, "veto_reason": "WEEKLY_CONFLICT"}
    except Exception as exc:
        logger.debug("[ML DimA] Weekly trend check skipped: %s", exc)
        detail["weekly_trend"] = {"weekly_trend": "unknown", "detail_zh": "週線計算跳過"}

    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension B — Pullback Quality
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_b(df: pd.DataFrame) -> dict:
    """
    B: Pullback Quality scoring.
    Martin Luk's best pullback: to 21 EMA with volume dry-up, then bounce.
    Worse: below 50 EMA = broken trend.

    Adjustment range: [-2.0, +2.0]
    """
    from modules.data_pipeline import get_pullback_depth

    pb = get_pullback_depth(df)
    adj = 0.0
    detail = {}

    quality = pb.get("pullback_quality", "unknown")
    nearest = pb.get("nearest_ema")
    too_ext = pb.get("too_extended", False)

    if quality == "ideal":
        adj += 1.0
        detail["quality"] = f"理想回調至 EMA{nearest} ✓"
    elif quality == "acceptable":
        adj += 0.4
        detail["quality"] = f"可接受回調至 EMA{nearest}"
    elif quality == "broken":
        adj -= 1.0
        detail["quality"] = "趨勢已破壞 (低於50 EMA)"
    else:
        adj -= 0.2
        detail["quality"] = "回調品質未知"

    if too_ext:
        adj -= 0.8
        detail["extended"] = "⚠ 過度延伸 — 避免追高"
    else:
        detail["extended"] = "未過度延伸 ✓"

    # Depth from recent high
    depth = pb.get("depth_pct", 0) or 0
    if -10 <= depth <= -3:
        adj += 0.3
        detail["depth"] = f"健康回調 {depth:.1f}%"
    elif depth > -3:
        detail["depth"] = f"微回調 {depth:.1f}%"
    elif depth < -15:
        adj -= 0.3
        detail["depth"] = f"深度回調 {depth:.1f}% — 注意"

    # ── Higher-low structure (Chapter 5 — ascending lows = sellers exhausting) ──
    try:
        from modules.ml_setup_detector import detect_higher_lows
        hl_data = detect_higher_lows(df)
        detail["higher_lows"] = hl_data
        adj += hl_data.get("adjustment", 0.0)
    except Exception as exc:
        logger.debug("[ML DimB] Higher-low detection failed: %s", exc)

    detail["pullback"] = pb
    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension C — AVWAP Confluence
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_c(df: pd.DataFrame) -> dict:
    """
    C: Support Confluence scoring (expanded from AVWAP-only).
    Martin Luk (Chapter 5, 6): "Multiple support confluence is the MOST
    IMPORTANT factor for a high-probability pullback entry."

    Now counts ALL support levels (EMA + AVWAP + Prior High + Gap Fill)
    converging at the same price zone. 3+ = high-probability entry bonus.

    Adjustment range: [-2.0, +2.5]  (expanded from original [-1.5, +1.5])
    """
    from modules.data_pipeline import get_avwap_from_swing_high, get_avwap_from_swing_low
    from modules.ml_setup_detector import count_support_confluence

    avwap_h = get_avwap_from_swing_high(df)
    avwap_l = get_avwap_from_swing_low(df)

    adj = 0.0
    detail = {}

    # ── Original AVWAP directional check (retained) ──────────────────────
    above_supply  = avwap_h.get("above_avwap", False)
    above_support = avwap_l.get("above_avwap", False)

    if above_supply and above_support:
        adj += 1.0
        detail["avwap_status"] = "高於供給+支撐 AVWAP ✓✓"
    elif above_support:
        adj += 0.4
        detail["avwap_status"] = "高於支撐 AVWAP ✓"
    elif above_supply:
        adj += 0.2
        detail["avwap_status"] = "高於供給 AVWAP (罕見)"
    else:
        adj -= 0.3
        detail["avwap_status"] = "低於兩條 AVWAP"

    # AVWAP proximity bonus (near support = potential bounce)
    l_pct = avwap_l.get("price_vs_avwap_pct")
    if l_pct is not None and 0 <= l_pct <= 2.0:
        adj += 0.3
        detail["support_proximity"] = f"接近支撐 AVWAP ({l_pct:.1f}%) — 潛在反彈點"

    # Break-and-Retest bonus (Martin's favorite AVWAP pattern)
    # Price was below AVWAP supply, broke above, now testing from above
    h_pct = avwap_h.get("price_vs_avwap_pct")
    if h_pct is not None and above_supply and 0 <= h_pct <= 3.0:
        adj += 0.4
        detail["break_retest"] = f"Break & Retest 形態 — 前供給 AVWAP 轉支撐 (+{h_pct:.1f}%) ✓"

    # ── NEW: Full support confluence count ───────────────────────────────
    try:
        confluence = count_support_confluence(df)
        detail["confluence"] = confluence
        adj += confluence.get("adjustment", 0.0)
        detail["confluence_count"] = confluence.get("count", 0)
        detail["confluence_levels"] = confluence.get("levels", [])
    except Exception as exc:
        logger.debug("[ML DimC] Confluence count failed: %s", exc)

    detail["avwap_high"] = {k: v for k, v in avwap_h.items() if k != "avwap_series"}
    detail["avwap_low"]  = {k: v for k, v in avwap_l.items() if k != "avwap_series"}

    adj = max(-2.0, min(adj, 2.5))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension D — Volume Pattern
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_d(df: pd.DataFrame) -> dict:
    """
    D: Volume Pattern scoring.
    Martin Luk: Volume dry-up during pullback = sellers exhausted (bullish).
    Volume surge on bounce day = confirmation.

    Adjustment range: [-1.5, +1.5]
    """
    adj = 0.0
    detail = {}

    if df.empty or len(df) < 30:
        return {"score": 0.0, "detail": {"status": "數據不足"}, "is_veto": False}

    # Volume dry-up in last 10 bars vs prior 20
    baseline = float(df["Volume"].tail(30).iloc[:-10].mean())
    recent_vol = float(df["Volume"].tail(10).mean())
    dry_ratio = recent_vol / baseline if baseline > 0 else 1.0
    dry_thresh = getattr(C, "ML_VOLUME_DRY_UP_RATIO", 0.50)

    if dry_ratio < dry_thresh:
        adj += 0.6
        detail["dry_up"] = f"成交量萎縮 ✓ (比率: {dry_ratio:.2f})"
    elif dry_ratio < 0.75:
        adj += 0.3
        detail["dry_up"] = f"成交量略縮 (比率: {dry_ratio:.2f})"
    else:
        detail["dry_up"] = f"成交量正常 (比率: {dry_ratio:.2f})"

    # Today's volume vs avg (surge check)
    avg_vol_20 = float(df["Volume"].tail(20).mean())
    today_vol = float(df["Volume"].iloc[-1])
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
    surge_mult = getattr(C, "ML_VOLUME_SURGE_MULT", 1.5)
    ideal_mult = getattr(C, "ML_IDEAL_VOLUME_SURGE_MULT", 2.0)

    if vol_ratio >= ideal_mult:
        adj += 0.6
        detail["surge"] = f"強烈放量 ✓✓ ({vol_ratio:.1f}×)"
    elif vol_ratio >= surge_mult:
        adj += 0.3
        detail["surge"] = f"適度放量 ✓ ({vol_ratio:.1f}×)"
    else:
        detail["surge"] = f"今日成交量: {vol_ratio:.1f}× 平均"

    detail["dry_ratio"] = round(dry_ratio, 3)
    detail["vol_ratio"] = round(vol_ratio, 2)
    adj = max(-1.5, min(adj, 1.5))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension E — Risk/Reward
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_e(df: pd.DataFrame) -> dict:
    """
    E: Risk/Reward scoring.
    Martin Luk: Stop must be < 2.5% from entry. Ideal: 1.0-1.5%.
    R:R computed from stop distance and potential upside (EMA slope).

    Adjustment range: [-2.0, +2.0] with VETO when stop > 2.5%.
    """
    adj = 0.0
    detail = {}
    is_veto = False

    if df.empty or len(df) < 14:
        return {"score": 0.0, "detail": {"status": "數據不足"}, "is_veto": False}

    close = float(df["Close"].iloc[-1])
    low = float(df["Low"].iloc[-1])
    max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
    ideal_stop = getattr(C, "ML_IDEAL_STOP_LOSS_PCT", 1.5)

    # Stop distance = (entry - low of day) / entry
    if close > 0 and low > 0:
        stop_pct = (close - low) / close * 100.0
    else:
        stop_pct = 99.0

    if stop_pct <= ideal_stop:
        adj += 1.0
        detail["stop"] = f"理想止損距離 {stop_pct:.1f}% ✓"
    elif stop_pct <= max_stop:
        adj += 0.3
        detail["stop"] = f"可接受止損 {stop_pct:.1f}%"
    else:
        adj -= 1.5
        is_veto = True
        detail["stop"] = f"⛔ 止損過大 {stop_pct:.1f}% > {max_stop}% 上限"

    # ATR-based stop quality
    if "ATR_14" in df.columns:
        atr = float(df["ATR_14"].iloc[-1])
        if atr > 0 and close > 0:
            atr_pct = (atr / close) * 100.0
            if atr_pct <= max_stop:
                adj += 0.3
                detail["atr_stop"] = f"ATR止損 {atr_pct:.1f}% 在範圍內 ✓"
            else:
                adj -= 0.2
                detail["atr_stop"] = f"ATR止損 {atr_pct:.1f}% 偏大"

    # R:R estimation (3R target typical for Martin)
    if stop_pct > 0:
        rr_3 = 3.0  # Target 3R
        potential_gain = stop_pct * rr_3
        detail["rr_3r_target"] = f"3R 目標: +{potential_gain:.1f}%"
    else:
        detail["rr_3r_target"] = "N/A"

    # ── LOD Chase Check (Chapter 4, 9 — ">3% from LOD → SKIP") ──────────
    # Using intraday low as proxy for LOD when only daily data available
    try:
        from modules.ml_setup_detector import check_chase_lod
        lod = float(df["Low"].iloc[-1])
        chase_data = check_chase_lod(close, lod)
        detail["chase_check"] = chase_data
        if chase_data.get("is_chase") and getattr(C, "ML_CHASE_WARNING_ENABLED", True):
            chase_penalty = getattr(C, "ML_CHASE_DIM_E_PENALTY", -0.8)
            adj += chase_penalty
            detail["chase_warning"] = chase_data["warning"]
    except Exception as exc:
        logger.debug("[ML DimE] Chase check failed: %s", exc)

    detail["stop_pct"] = round(stop_pct, 2)
    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": is_veto}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension F — Relative Strength
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_f(df: pd.DataFrame, rs_rank: Optional[float] = None) -> dict:
    """
    F: Relative Strength scoring.
    Uses momentum returns as a proxy for RS rank if not provided.

    Adjustment range: [-1.0, +1.0]
    """
    from modules.data_pipeline import get_momentum_returns

    adj = 0.0
    detail = {}

    if rs_rank is not None:
        if rs_rank >= 90:
            adj += 0.8
            detail["rs"] = f"RS 排名 {rs_rank:.0f} 百分位 — 市場頂級 ✓"
        elif rs_rank >= 70:
            adj += 0.4
            detail["rs"] = f"RS 排名 {rs_rank:.0f} 百分位 — 很強"
        elif rs_rank >= 50:
            detail["rs"] = f"RS 排名 {rs_rank:.0f} 百分位 — 一般"
        else:
            adj -= 0.5
            detail["rs"] = f"RS 排名 {rs_rank:.0f} 百分位 — 偏弱"
    else:
        # Use momentum as proxy
        mom = get_momentum_returns(df)
        m3 = mom.get("3m") or 0
        m6 = mom.get("6m") or 0
        if m3 >= 50 and m6 >= 100:
            adj += 0.6
            detail["rs"] = f"動量極強 (3M: {m3:.0f}%, 6M: {m6:.0f}%) ✓"
        elif m3 >= 30:
            adj += 0.3
            detail["rs"] = f"動量偏強 (3M: {m3:.0f}%)"
        elif m3 >= 0:
            detail["rs"] = f"動量一般 (3M: {m3:.0f}%)"
        else:
            adj -= 0.4
            detail["rs"] = f"動量偏弱 (3M: {m3:.0f}%)"

    adj = max(-1.0, min(adj, 1.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension G — Market Environment
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_g(market_regime: Optional[str] = None) -> dict:
    """
    G: Market Environment scoring.
    Martin reduces activity in corrections, blocks in downtrend.

    Adjustment range: [-2.0, +1.0] with VETO for DOWNTREND.

    Strategy:
      1. Try live assess() for real-time regime
      2. Fallback to DuckDB cached market_env_history (≤7 days old)
      3. Last resort: regime = UNKNOWN → adj = 0.0
    """
    adj = 0.0
    detail: dict = {}
    veto = False
    regime = "UNKNOWN"

    if market_regime:
        regime = market_regime
        detail["source"] = "injected"
    else:
        # ── Attempt 1: live market assessment ────────────────────────────
        try:
            from modules.market_env import assess as mkt_assess
            mkt = mkt_assess(verbose=False)
            regime = mkt.get("regime", "UNKNOWN") if isinstance(mkt, dict) else "UNKNOWN"
            detail["source"] = "live"
        except Exception as exc:
            logger.debug("[ML DimG] live assess() failed: %s — trying DB cache", exc)
            # ── Attempt 2: DuckDB cached regime ──────────────────────────
            try:
                from modules.db import query_market_env_history
                _mkt_df = query_market_env_history(days=7)
                if _mkt_df is not None and not _mkt_df.empty:
                    regime = str(_mkt_df.iloc[0].get("regime", "UNKNOWN"))
                    detail["source"] = "cached"
                    logger.info("[ML DimG] Using cached regime from DB: %s", regime)
                else:
                    detail["source"] = "unavailable"
                    logger.warning("[ML DimG] No cached market_env in DB either")
            except Exception as exc2:
                logger.debug("[ML DimG] DB cache also failed: %s", exc2)
                detail["source"] = "unavailable"

    detail["regime"] = regime

    # ── Map regime → adjustment ──────────────────────────────────────────
    if regime in ("CONFIRMED_UPTREND", "BULL_CONFIRMED"):
        adj += 0.8
        detail["market"] = "確認上升趨勢 ✓"
    elif regime == "BULL_UNCONFIRMED":
        adj += 0.4
        detail["market"] = "牛市未確認 — 觀望為主"
    elif regime in ("BULL_EARLY", "BOTTOM_FORMING"):
        adj += 0.3
        detail["market"] = "牛市初期 / 築底中 — 可小量試探"
    elif regime in ("UPTREND_UNDER_PRESSURE", "TRANSITION"):
        adj += 0.0
        detail["market"] = "過渡期 / 上升趨勢受壓 — 減少倉位"
    elif regime in ("CHOPPY", "BEAR_RALLY"):
        adj -= 0.5
        detail["market"] = "震盪市 / 熊市反彈 — 減少交易頻率"
    elif regime in ("MARKET_IN_CORRECTION", "BEAR_CONFIRMED"):
        adj -= 1.5
        detail["market"] = "市場修正 / 確認熊市 — 停止新倉"
    elif regime == "DOWNTREND":
        adj -= 2.0
        veto = True
        detail["market"] = "⛔ 下降趨勢 — 停止交易"
    else:
        adj += 0.0
        detail["market"] = f"市場狀態: {regime}" if regime != "UNKNOWN" else "市場環境數據不可用"

    adj = max(-2.0, min(adj, 1.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": veto}


# ─────────────────────────────────────────────────────────────────────────────
# Star rating aggregation
# ─────────────────────────────────────────────────────────────────────────────

def compute_star_rating(dim_scores: dict[str, dict],
                         base: float = None) -> dict:
    """
    Aggregate 7-dimension scores into a final star rating (0-5 scale).

    Returns:
        dict with 'stars', 'capped_stars', 'recommendation', 'position_sizing'
    """
    base = base if base is not None else getattr(C, "ML_STAR_BASE", 2.5)
    weights = {
        "A": getattr(C, "ML_DIM_A_WEIGHT", 0.20),
        "B": getattr(C, "ML_DIM_B_WEIGHT", 0.20),
        "C": getattr(C, "ML_DIM_C_WEIGHT", 0.15),
        "D": getattr(C, "ML_DIM_D_WEIGHT", 0.15),
        "E": getattr(C, "ML_DIM_E_WEIGHT", 0.15),
        "F": getattr(C, "ML_DIM_F_WEIGHT", 0.10),
        "G": getattr(C, "ML_DIM_G_WEIGHT", 0.05),
    }

    # Check vetoes (including new weekly veto from Dim A)
    weekly_veto = dim_scores.get("A", {}).get("is_veto", False)
    risk_veto   = dim_scores.get("E", {}).get("is_veto", False)
    mkt_veto    = dim_scores.get("G", {}).get("is_veto", False)

    if weekly_veto and getattr(C, "ML_WEEKLY_EMA_CONFLICT_HARD", True):
        return {
            "stars": 0.0, "capped_stars": 0.0,
            "recommendation": "PASS",
            "recommendation_zh": "放棄 — 週線趨勢衝突 (日線與週線背道)",
            "position_pct": 0.0, "shares": 0,
            "veto": "WEEKLY",
        }

    if risk_veto:
        return {
            "stars": 0.0, "capped_stars": 0.0,
            "recommendation": "PASS",
            "recommendation_zh": "放棄 — 止損距離過大",
            "position_pct": 0.0, "shares": 0,
            "veto": "RISK",
        }
    if mkt_veto:
        return {
            "stars": 0.0, "capped_stars": 0.0,
            "recommendation": "PASS",
            "recommendation_zh": "放棄 — 熊市否決",
            "position_pct": 0.0, "shares": 0,
            "veto": "MARKET",
        }

    # Weighted sum
    weight_sum = sum(weights.values())
    total_adj = 0.0
    for dim_key, w in weights.items():
        score = dim_scores.get(dim_key, {}).get("score", 0.0)
        total_adj += score * (w / weight_sum)

    # Scale: each dimension [-2, +2]; weighted sum ~[-2, +2]; final ~[0.5, 4.5]
    raw_stars = base + total_adj * 2.0
    star_max = getattr(C, "ML_STAR_MAX", 5.0)
    capped = round(max(0.0, min(raw_stars, star_max)) * 2) / 2  # round to 0.5

    # Recommendation
    if capped >= 4.5:
        rec, rec_zh = "STRONG BUY", "強力買入"
    elif capped >= 4.0:
        rec, rec_zh = "BUY", "買入"
    elif capped >= 3.0:
        rec, rec_zh = "WATCH", "觀察"
    elif capped >= 2.5:
        rec, rec_zh = "WEAK", "偏弱"
    else:
        rec, rec_zh = "PASS", "放棄"

    return {
        "stars": round(raw_stars, 2),
        "capped_stars": capped,
        "recommendation": rec,
        "recommendation_zh": rec_zh,
        "veto": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Trade plan builder (Martin Luk formula-based)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_entry_levels(df: pd.DataFrame, close: float,
                          trade_plan: dict) -> dict:
    """
    Compute quantified entry price levels for Martin Luk's entry notes.

    Returns EMA values, AVWAP levels, ideal entry zone, max chase price,
    and support/resistance levels with actual dollar amounts.
    """
    from modules.data_pipeline import (
        get_avwap_from_swing_high, get_avwap_from_swing_low,
    )

    result: dict = {"close": round(close, 2)}

    # ── EMA price levels ──────────────────────────────────────────────────
    for period in [9, 21, 50, 150]:
        col = f"EMA_{period}"
        if col in df.columns:
            val = float(df[col].iloc[-1])
            result[f"ema{period}"] = round(val, 2)
            result[f"ema{period}_pct"] = round((close - val) / val * 100, 2) if val > 0 else 0
        else:
            result[f"ema{period}"] = None
            result[f"ema{period}_pct"] = None

    # ── AVWAP levels ──────────────────────────────────────────────────────
    avwap_high = get_avwap_from_swing_high(df)
    avwap_low = get_avwap_from_swing_low(df)
    result["avwap_high"] = round(avwap_high.get("avwap_value", 0), 2) if avwap_high.get("avwap_value") else None
    result["avwap_low"] = round(avwap_low.get("avwap_value", 0), 2) if avwap_low.get("avwap_value") else None
    result["above_avwap_high"] = avwap_high.get("above_avwap", False)
    result["above_avwap_low"] = avwap_low.get("above_avwap", False)

    # ── Previous day high/low ─────────────────────────────────────────────
    if len(df) >= 2:
        result["prev_high"] = round(float(df["High"].iloc[-2]), 2)
        result["prev_low"] = round(float(df["Low"].iloc[-2]), 2)
        result["prev_close"] = round(float(df["Close"].iloc[-2]), 2)
    else:
        result["prev_high"] = result["prev_low"] = result["prev_close"] = None

    # ── LOD and max chase price ───────────────────────────────────────────
    lod = float(df["Low"].iloc[-1]) if len(df) >= 1 else close
    max_chase_pct = getattr(C, "ML_WATCH_MAX_CHASE_PCT", 3.0)
    result["lod"] = round(lod, 2)
    result["max_chase_price"] = round(lod * (1 + max_chase_pct / 100), 2)

    # ── Ideal entry zone (between 21 EMA and close, near support AVWAP) ──
    ema21 = result.get("ema21")
    entry_stop = trade_plan.get("stop")
    if ema21 and ema21 < close:
        result["ideal_entry_low"] = round(ema21, 2)
        result["ideal_entry_high"] = round(ema21 * 1.005, 2)  # tight zone
    elif entry_stop:
        result["ideal_entry_low"] = round(entry_stop * 1.003, 2)
        result["ideal_entry_high"] = round(entry_stop * 1.01, 2)
    else:
        result["ideal_entry_low"] = None
        result["ideal_entry_high"] = None

    # ── Key support/resistance summary for commentary ─────────────────────
    supports = []
    resistances = []
    for label, val in [
        ("EMA9", result.get("ema9")),
        ("EMA21", result.get("ema21")),
        ("EMA50", result.get("ema50")),
        ("AVWAP_Low", result.get("avwap_low")),
    ]:
        if val and val < close:
            supports.append({"label": label, "price": val,
                             "distance_pct": round((close - val) / close * 100, 2)})
    for label, val in [
        ("AVWAP_High", result.get("avwap_high")),
        ("Prev_High", result.get("prev_high")),
    ]:
        if val and val > close:
            resistances.append({"label": label, "price": val,
                                "distance_pct": round((val - close) / close * 100, 2)})
    result["supports"] = sorted(supports, key=lambda x: x["distance_pct"])
    result["resistances"] = sorted(resistances, key=lambda x: x["distance_pct"])

    return result


def _compute_avwap_analysis(df: pd.DataFrame, close: float) -> dict:
    """
    Detailed AVWAP analysis following Martin Luk's Chapter 6 methodology.

    Anchored VWAP analysis including:
    - Swing high AVWAP (resistance/supply zone)
    - Swing low AVWAP (support/demand zone)
    - Price position relative to both
    - Trading signals based on AVWAP confluence
    """
    from modules.data_pipeline import (
        get_avwap_from_swing_high, get_avwap_from_swing_low,
    )

    avwap_high = get_avwap_from_swing_high(df)
    avwap_low = get_avwap_from_swing_low(df)

    ah_val = avwap_high.get("avwap_value")
    al_val = avwap_low.get("avwap_value")
    above_high = avwap_high.get("above_avwap", False)
    above_low = avwap_low.get("above_avwap", False)

    # ── Position analysis ─────────────────────────────────────────────────
    position = "UNKNOWN"
    signal = "NEUTRAL"
    signal_zh = "中性"
    detail_zh = ""

    if above_high and above_low:
        position = "ABOVE_BOTH"
        signal = "BULLISH"
        signal_zh = "看好"
        detail_zh = "股價在雙 AVWAP 上方 — 多頭主導，趨勢健康"
    elif above_low and not above_high:
        position = "BETWEEN"
        signal = "NEUTRAL_BULLISH"
        signal_zh = "中性偏好"
        detail_zh = "股價在支撐 AVWAP 上方但阻力 AVWAP 下方 — 等待突破阻力"
    elif not above_low and not above_high:
        position = "BELOW_BOTH"
        signal = "BEARISH"
        signal_zh = "看淡"
        detail_zh = "股價在雙 AVWAP 下方 — 空方主導"
    elif not above_low and above_high:
        position = "MIXED"
        signal = "NEUTRAL"
        signal_zh = "中性"
        detail_zh = "AVWAP 交叉信號矛盾 — 觀望"

    # ── Proximity analysis ────────────────────────────────────────────────
    prox_high = None
    prox_low = None
    if ah_val and ah_val > 0:
        prox_high = round((close - ah_val) / ah_val * 100, 2)
    if al_val and al_val > 0:
        prox_low = round((close - al_val) / al_val * 100, 2)

    # ── Trading signals per Martin Ch6 ────────────────────────────────────
    trade_signals = []
    if al_val and 0 < prox_low <= 1.5:
        trade_signals.append({
            "type": "SUPPORT_TEST",
            "sentiment": "bullish",
            "zh": f"📍 股價接近支撐 AVWAP ${al_val:.2f} (距 {prox_low:.1f}%) — 潛在買入點",
        })
    if ah_val and prox_high is not None and 0 < prox_high <= 1.0:
        trade_signals.append({
            "type": "RESISTANCE_APPROACH",
            "sentiment": "neutral",
            "zh": f"⚡ 股價接近阻力 AVWAP ${ah_val:.2f} (距 {prox_high:.1f}%) — 觀察突破",
        })
    if ah_val and prox_high is not None and prox_high > 2.0:
        trade_signals.append({
            "type": "ABOVE_RESISTANCE",
            "sentiment": "bullish",
            "zh": f"✅ 股價已突破阻力 AVWAP ${ah_val:.2f} — AVWAP 由阻轉撐",
        })
    if al_val and prox_low is not None and prox_low < -1.0:
        trade_signals.append({
            "type": "BELOW_SUPPORT",
            "sentiment": "bearish",
            "zh": f"⛔ 股價跌破支撐 AVWAP ${al_val:.2f} (距 {prox_low:.1f}%) — 支撐失效",
        })

    return {
        "avwap_high_price": round(ah_val, 2) if ah_val else None,
        "avwap_low_price": round(al_val, 2) if al_val else None,
        "above_avwap_high": above_high,
        "above_avwap_low": above_low,
        "position": position,
        "signal": signal,
        "signal_zh": signal_zh,
        "detail_zh": detail_zh,
        "prox_high_pct": prox_high,
        "prox_low_pct": prox_low,
        "trade_signals": trade_signals,
    }


def _build_trade_plan(stars: float, row: dict) -> dict:
    """
    Generate a concrete trade action plan following Martin Luk's
    formula-based position sizing.

    Key formula: Shares = (Account × Risk%) / (Entry − Stop)
    """
    close = row.get("close", 0)
    lod = row.get("lod") or row.get("low") or close
    account = getattr(C, "ACCOUNT_SIZE", 100_000)
    risk_pct = getattr(C, "ML_RISK_PER_TRADE_PCT", 0.50)
    max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
    buffer = getattr(C, "ML_LOD_STOP_BUFFER_PCT", 0.3)

    # Stop = LOD - buffer
    stop_px = round(lod * (1 - buffer / 100.0), 2) if lod and lod > 0 else None
    stop_distance = close - stop_px if stop_px and close > 0 else 0
    stop_pct_actual = (stop_distance / close * 100.0) if close > 0 else 99.0

    # Position sizing via formula
    risk_dollars = account * (risk_pct / 100.0)
    if stop_distance > 0:
        shares = int(risk_dollars / stop_distance)
    else:
        shares = 0

    # Max position cap (25% of account)
    max_pos_pct = getattr(C, "ML_MAX_SINGLE_POSITION_PCT", 25.0)
    max_shares_by_cap = int(account * max_pos_pct / 100.0 / close) if close > 0 else 0
    shares = min(shares, max_shares_by_cap)

    pos_value = shares * close if close > 0 else 0
    pos_pct = (pos_value / account * 100.0) if account > 0 else 0
    total_risk = shares * stop_distance if stop_distance > 0 else 0

    # Martin's partial sell levels
    partial_1_r = getattr(C, "ML_PARTIAL_SELL_1_R", 3.0)
    partial_1_pct = getattr(C, "ML_PARTIAL_SELL_1_PCT", 15.0)
    partial_2_r = getattr(C, "ML_PARTIAL_SELL_2_R", 5.0)
    partial_2_pct = getattr(C, "ML_PARTIAL_SELL_2_PCT", 15.0)
    trail_ema = getattr(C, "ML_TRAIL_EMA", 9)

    target_1 = round(close + stop_distance * partial_1_r, 2) if stop_distance > 0 else None
    target_2 = round(close + stop_distance * partial_2_r, 2) if stop_distance > 0 else None

    return {
        "action": "BUY" if stars >= 3.0 and stop_pct_actual <= max_stop else "PASS",
        "entry": round(close, 2),
        "stop": stop_px,
        "stop_pct": round(stop_pct_actual, 2),
        "shares": shares,
        "position_value": round(pos_value, 0),
        "position_pct": round(pos_pct, 1),
        "risk_dollars": round(total_risk, 2),
        "risk_pct_of_account": round(total_risk / account * 100, 2) if account > 0 else 0,
        "target_1_price": target_1,
        "target_1_label": f"3R 目標 — 出 {partial_1_pct:.0f}%",
        "target_2_price": target_2,
        "target_2_label": f"5R 目標 — 出 {partial_2_pct:.0f}%",
        "trail_label": f"剩餘 70% 用 {trail_ema} EMA 追蹤止損",
        "trail_ema_period": trail_ema,
        "formula": f"倉位 = ({account:,.0f} × {risk_pct}%) / ({close:.2f} − {stop_px}) = {shares} 股",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main public function  
# ─────────────────────────────────────────────────────────────────────────────

def analyze_ml(ticker: str,
               df: pd.DataFrame = None,
               rs_rank: Optional[float] = None,
               print_report: bool = True,
               market_regime: Optional[str] = None) -> dict:
    """
    Full Martin Luk 7-dimension analysis for a single stock.

    Args:
        ticker:       Stock symbol
        df:           Enriched OHLCV DataFrame (fetched if None)
        rs_rank:      Pre-computed RS percentile rank (0-99)
        print_report: Print terminal report

    Returns:
        Comprehensive dict with:
            'ticker', 'stars', 'capped_stars', 'recommendation',
            'recommendation_zh', 'dim_scores' (A-G detail dicts),
            'setup_type', 'trade_plan', 'scan_date'
    """
    from modules.data_pipeline import (
        get_enriched, get_dollar_volume, get_adr,
        get_momentum_returns, get_ema_alignment
    )

    if df is None or df.empty:
        df = get_enriched(ticker, period="1y")

    if df is None or df.empty:
        return {"ticker": ticker, "error": "No price data available",
                "stars": 0.0, "capped_stars": 0.0,
                "recommendation": "PASS", "recommendation_zh": "無法取得數據"}

    close = float(df["Close"].iloc[-1])
    dv = get_dollar_volume(df)
    adr = get_adr(df)
    mom = get_momentum_returns(df)
    ema = get_ema_alignment(df)

    # ── Detect setup type ──────────────────────────────────────────────────
    from modules.ml_setup_detector import detect_setup_type
    setup = detect_setup_type(df, ticker=ticker)

    # ── Score all 7 dimensions ─────────────────────────────────────────────
    dim_a = _score_dim_a(df)
    dim_b = _score_dim_b(df)
    dim_c = _score_dim_c(df)
    dim_d = _score_dim_d(df)
    dim_e = _score_dim_e(df)
    dim_f = _score_dim_f(df, rs_rank=rs_rank)
    dim_g = _score_dim_g(market_regime=market_regime)

    dim_scores = {
        "A": dim_a, "B": dim_b, "C": dim_c, "D": dim_d,
        "E": dim_e, "F": dim_f, "G": dim_g,
    }

    # ── Aggregate star rating ──────────────────────────────────────────────
    rating = compute_star_rating(dim_scores)
    stars = rating["capped_stars"]
    rec = rating["recommendation"]
    rec_zh = rating["recommendation_zh"]

    # ── Trade plan ─────────────────────────────────────────────────────────
    low = float(df["Low"].iloc[-1]) if not df.empty else None
    trade_plan = _build_trade_plan(stars, {
        "close": close,
        "low": low,
        "lod": low,
        "adr": adr,
    })

    result = {
        "ticker":            ticker.upper(),
        "scan_date":         date.today().isoformat(),
        "close":             round(close, 2),
        "adr":               round(adr, 2),
        "dollar_volume_m":   round(dv / 1_000_000, 2),
        "stars":             rating["stars"],
        "capped_stars":      stars,
        "recommendation":    rec,
        "recommendation_zh": rec_zh,
        "veto":              rating.get("veto"),
        "dim_scores":        dim_scores,
        "setup_type":        setup,
        "trade_plan":        trade_plan,
        "ema_alignment":     {k: v for k, v in ema.items() if k != "avwap_series"},
        "momentum": {
            "3m": mom.get("3m"),
            "6m": mom.get("6m"),
        },
        "mom_3m":            mom.get("3m"),
        "mom_6m":            mom.get("6m"),
    }

    # ── Entry Levels — Quantified price levels for Martin's entry notes ─────
    try:
        result["entry_levels"] = _compute_entry_levels(df, close, trade_plan)
    except Exception as exc:
        logger.debug("[ML Analyze] Entry levels failed: %s", exc)
        result["entry_levels"] = None

    # ── AVWAP Analysis — detailed AVWAP confluence analysis ─────────────────
    try:
        result["avwap_analysis"] = _compute_avwap_analysis(df, close)
    except Exception as exc:
        logger.debug("[ML Analyze] AVWAP analysis failed: %s", exc)
        result["avwap_analysis"] = None

    # ── NEW Phase 1: Pullback Quality Scorecard ─────────────────────────────
    try:
        result["pullback_scorecard"] = compute_pullback_scorecard(result)
    except Exception as exc:
        logger.debug("[ML Analyze] Pullback scorecard failed: %s", exc)
        result["pullback_scorecard"] = None

    # ── NEW Phase 1: 9-Step Entry Decision Tree ─────────────────────────────
    if getattr(C, "ML_DTQ_ENABLED", True):
        try:
            result["decision_tree"] = evaluate_entry_decision_tree(result)
        except Exception as exc:
            logger.debug("[ML Analyze] Decision tree failed: %s", exc)
            result["decision_tree"] = None

    # ── NEW Phase 1: Short Research Marker ──────────────────────────────────
    if getattr(C, "ML_SHORT_RESEARCH_ENABLED", True):
        try:
            mkt_data = None
            try:
                from modules.market_env import assess as mkt_assess
                mkt_data = mkt_assess(verbose=False)
            except Exception:
                pass
            result["short_research"] = _evaluate_short_research(df, market_data=mkt_data)
        except Exception as exc:
            logger.debug("[ML Analyze] Short research failed: %s", exc)
            result["short_research"] = None

    if print_report:
        _print_report(result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Pullback Buy Quality Scorecard (Appendix C — Martin Luk 10-point system)
# ─────────────────────────────────────────────────────────────────────────────

def compute_pullback_scorecard(analysis_data: dict) -> dict:
    """
    Evaluate Martin Luk's Appendix C Pullback Buy Quality Scorecard.

    10-point holistic quality assessment:
      (1) EMA direction           — 0-2 pts
      (2) Support confluence      — 0-2 pts
      (3) Base structure          — 0-1 pt
      (4) Weekly confirmation     — 0-1 pt
      (5) Break and retest        — 0-1 pt
      (6) First pullback in base  — 0-1 pt
      (7) AVWAP proximity         — 0-1 pt
      (8) Higher lows             — 0-1 pt

    Scoring: 8-10 = high quality (full size), 6-7 = reduce size, ≤5 = skip.

    Args:
        analysis_data: Result dict from analyze_ml()

    Returns:
        dict with 'score', 'max_score', 'grade', 'items', 'size_multiplier'
    """
    dim_scores = analysis_data.get("dim_scores", {})
    setup_type = analysis_data.get("setup_type", {})

    items = []
    total = 0

    # ── (1) EMA Direction — 0-2 pts ───────────────────────────────────────
    dim_a = dim_scores.get("A", {}).get("detail", {})
    ema_align = dim_a.get("ema_alignment", {})
    if ema_align.get("all_stacked") and ema_align.get("all_rising"):
        score = 2; desc = "9>21>50>150 EMA 完美排列上升 ✓✓"
    elif ema_align.get("all_stacked") or ema_align.get("ema_21_rising"):
        score = 1; desc = "EMA 部分排列/上升 ✓"
    else:
        score = 0; desc = "EMA 方向未明或下降"
    items.append({"item": "EMA方向", "score": score, "max": 2, "desc": desc})
    total += score

    # ── (2) Support Confluence — 0-2 pts ──────────────────────────────────
    dim_c = dim_scores.get("C", {}).get("detail", {})
    confluence_count = dim_c.get("confluence_count", 0) or dim_c.get("confluence", {}).get("count", 0)
    if confluence_count >= 3:
        score = 2; desc = f"{confluence_count} 個支撐層級匯聚 ✓✓ (高概率)"
    elif confluence_count >= 2:
        score = 1; desc = f"{confluence_count} 個支撐層級匯聚 ✓"
    else:
        score = 0; desc = "支撐匯聚不足"
    items.append({"item": "支撐匯聚", "score": score, "max": 2, "desc": desc})
    total += score

    # ── (3) Base structure — 0-1 pt ───────────────────────────────────────
    dim_b = dim_scores.get("B", {}).get("detail", {})
    pb = dim_b.get("pullback", {})
    pb_quality = pb.get("pullback_quality", "unknown")
    if pb_quality == "ideal":
        score = 1; desc = "理想底部結構 (EMA回調) ✓"
    elif pb_quality == "acceptable":
        score = 0; desc = "可接受底部結構"
    else:
        score = 0; desc = "底部結構偏差"
    items.append({"item": "底部結構", "score": score, "max": 1, "desc": desc})
    total += score

    # ── (4) Weekly chart confirmation — 0-1 pt ─────────────────────────────
    weekly_data = dim_a.get("weekly_trend", {})
    weekly_trend = weekly_data.get("weekly_trend", "unknown")
    if weekly_trend == "uptrend":
        score = 1; desc = "週線確認上升趨勢 ✓"
    elif weekly_trend in ("uptrend_weakening", "neutral"):
        score = 0; desc = "週線趨勢不明確"
    else:
        score = 0; desc = "週線趨勢衝突 / 下降"
    items.append({"item": "週線確認", "score": score, "max": 1, "desc": desc})
    total += score

    # ── (5) Break and Retest pattern — 0-1 pt ─────────────────────────────
    has_br_retest = False
    for setup in (setup_type.get("all_setups") or []):
        if isinstance(setup, (list, tuple)) and setup[0] == "BR_RETEST":
            has_br_retest = True
            break
    score = 1 if has_br_retest else 0
    items.append({"item": "Break & Retest", "score": score, "max": 1,
                  "desc": "Break & Retest 形態 ✓" if has_br_retest else "非 BR 形態"})
    total += score

    # ── (6) First pullback in base — 0-1 pt ───────────────────────────────
    # Proxy: setup detected as PB_EMA with high confidence
    primary = setup_type.get("primary_setup", "UNKNOWN")
    conf = setup_type.get("confidence", 0)
    first_pb = primary == "PB_EMA" and conf >= 0.65
    score = 1 if first_pb else 0
    items.append({"item": "底部首次回調", "score": score, "max": 1,
                  "desc": "底部首次回調 (PB_EMA 高信心) ✓" if first_pb else "非底部首次回調"})
    total += score

    # ── (7) AVWAP proximity — 0-1 pt ──────────────────────────────────────
    avwap_low = dim_c.get("avwap_low", {})
    l_pct = avwap_low.get("price_vs_avwap_pct")
    near_avwap = l_pct is not None and 0 <= l_pct <= 3.0
    score = 1 if near_avwap else 0
    items.append({"item": "AVWAP接近度", "score": score, "max": 1,
                  "desc": f"接近支撐 AVWAP ({l_pct:.1f}%) ✓" if near_avwap else "距 AVWAP 較遠"})
    total += score

    # ── (8) Higher lows — 0-1 pt ──────────────────────────────────────────
    hl_data = dim_b.get("higher_lows", {})
    has_hl = hl_data.get("has_higher_lows", False)
    score = 1 if has_hl else 0
    items.append({"item": "遞升低點", "score": score, "max": 1,
                  "desc": f"{hl_data.get('count', 0)} 個遞升低點 ✓" if has_hl else "無遞升低點結構"})
    total += score

    max_score = sum(i["max"] for i in items)
    pct = total / max_score * 100.0 if max_score > 0 else 0.0

    high_thresh = getattr(C, "ML_SCORECARD_HIGH_QUALITY", 8)
    med_thresh  = getattr(C, "ML_SCORECARD_MED_QUALITY", 6)

    if total >= high_thresh:
        grade = "A"; size_mult = 1.0;  decision = "全倉 Full Size"
    elif total >= med_thresh:
        grade = "B"; size_mult = 0.7;  decision = "七成倉 Reduced (0.7×)"
    else:
        grade = "C"; size_mult = 0.0;  decision = "跳過 Skip"

    return {
        "score":          total,
        "max_score":      max_score,
        "pct":            round(pct, 1),
        "grade":          grade,
        "items":          items,
        "size_multiplier": size_mult,
        "decision":       decision,
        "summary_zh":     f"回調品質計分卡: {total}/{max_score} ({grade}級) — {decision}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9-Step Entry Decision Tree (Chapter 9 flowchart)
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_entry_decision_tree(analysis_data: dict) -> dict:
    """
    Evaluate Martin Luk's 9-step entry decision tree (Chapter 9).

    9 questions:
      Q1: Market environment supportive?
      Q2: Stock in uptrend (EMA structure)?
      Q3: Sufficient support confluence?
      Q4: Higher lows present?
      Q5: Is this the first pullback in the base?
      Q6: Break and retest pattern?
      Q7: Flush→V-recovery opening behavior?
      Q8: Stop width acceptable (< 2.5%)?
      Q9: Price NOT already up > 3% from LOD (no chase)?

    Returns:
        dict with 'signal' (GO/CAUTION/NO-GO), 'pass_count',
                  'questions', 'summary_zh'
    """
    dim_scores = analysis_data.get("dim_scores", {})
    setup_type = analysis_data.get("setup_type", {})
    trade_plan = analysis_data.get("trade_plan", {})

    questions = []

    def q(num, label, passed, detail=""):
        questions.append({"q": num, "label": label,
                           "passed": passed, "detail": detail})

    # Q1: Market environment
    dim_g = dim_scores.get("G", {}).get("detail", {})
    regime = dim_g.get("regime", "UNKNOWN")
    q(1, "市場環境支持?",
      regime in ("CONFIRMED_UPTREND", "BULL_CONFIRMED", "UPTREND_UNDER_PRESSURE"),
      f"市場狀態: {regime}")

    # Q2: Stock in uptrend
    dim_a = dim_scores.get("A", {}).get("detail", {})
    ema_align = dim_a.get("ema_alignment", {})
    q(2, "股票處於上升趨勢?",
      ema_align.get("all_stacked") or ema_align.get("ema_21_rising", False),
      "EMA堆疊: " + ("%s" % ema_align.get("all_stacked", "N/A")))

    # Q3: Support confluence ≥ 2
    dim_c = dim_scores.get("C", {}).get("detail", {})
    confluence_count = dim_c.get("confluence_count", 0) or 0
    q(3, "支撐匯聚充足 (≥2)?",
      confluence_count >= getattr(C, "ML_CONFLUENCE_MIN_SETUP", 2),
      f"支撐層級: {confluence_count} 個")

    # Q4: Higher lows
    dim_b = dim_scores.get("B", {}).get("detail", {})
    hl_data = dim_b.get("higher_lows", {})
    q(4, "有遞升低點?",
      hl_data.get("has_higher_lows", False),
      hl_data.get("detail_zh", "未偵測"))

    # Q5: First pullback in base
    primary = setup_type.get("primary_setup", "UNKNOWN")
    conf    = setup_type.get("confidence", 0)
    q(5, "底部首次回調?",
      primary == "PB_EMA" and conf >= 0.55,
      f"形態: {primary} (信心: {conf:.0%})")

    # Q6: Break and Retest
    has_br = any(
        (isinstance(s, (list, tuple)) and s[0] == "BR_RETEST")
        for s in (setup_type.get("all_setups") or [])
    )
    q(6, "Break & Retest 形態?", has_br, "形態清單: " + str([s[0] for s in (setup_type.get("all_setups") or [])]))

    # Q7: Opening behavior (flush→V-recovery) — uses intraday data if available
    intraday_signals = analysis_data.get("intraday_signals", {})
    flush_v = intraday_signals.get("flush_v_recovery", {})
    orh    = intraday_signals.get("orh_breakout", {})
    has_intraday_signal = (
        flush_v.get("detected", False)
        or orh.get("is_above_orh", False)
        or orh.get("signal") == "AT_ORH"
    )
    q(7, "開盤行為: Flush→V反轉 / ORH突破?",
      has_intraday_signal,
      flush_v.get("detail_zh", "無日內信號數據 (盯盤模式可用)"))

    # Q8: Stop width < 2.5%
    stop_pct = dim_scores.get("E", {}).get("detail", {}).get("stop_pct", 99)
    max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
    q(8, f"止損距離可接受 (< {max_stop}%)?",
      stop_pct <= max_stop,
      f"止損距離: {stop_pct:.1f}%")

    # Q9: Not chasing (< 3% above LOD)
    chase_data = dim_scores.get("E", {}).get("detail", {}).get("chase_check", {})
    is_chase   = chase_data.get("is_chase", False)
    pct_above  = chase_data.get("pct_above_lod", 0) or 0
    q(9, "未追價 (距當日低 < 3%)?",
      not is_chase,
      f"距當日低: {pct_above:.1f}%")

    # Signal assessment
    pass_count = sum(1 for item in questions if item["passed"])
    go_min     = getattr(C, "ML_DTQ_GO_MIN_PASS",      7)
    caution_min = getattr(C, "ML_DTQ_CAUTION_MIN_PASS", 5)

    if pass_count >= go_min:
        signal = "GO";      signal_zh = "✅ 入場 — 條件達標"
    elif pass_count >= caution_min:
        signal = "CAUTION"; signal_zh = "⚠️  謹慎 — 部分條件未達"
    else:
        signal = "NO-GO";   signal_zh = "⛔ 不入場 — 條件不足"

    failed = [item["label"] for item in questions if not item["passed"]]

    return {
        "signal":      signal,
        "signal_zh":   signal_zh,
        "pass_count":  pass_count,
        "total":       len(questions),
        "questions":   questions,
        "failed_items": failed,
        "summary_zh": f"9步決策樹: {pass_count}/9 通過 → {signal_zh}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Short Research Marker (Chapter 16 — research only, not trading signal)
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_short_research(df: pd.DataFrame, market_data: dict = None) -> dict:
    """
    Detect Picture Perfect Short Setup characteristics for RESEARCH ONLY.

    Martin Luk (Chapter 16): Does NOT generate trading signals.
    Only flags when a stock exhibits the short setup pattern, to be used
    as a market health indicator.

    Picture Perfect Short pre-conditions:
      - QQQ below 50 EMA (or market in correction/downtrend)
      - IWM lagging QQQ significantly
      - Stock: first break below 50 EMA → rally to declining 9/21 EMA
        → rejection at AVWAP resistance → 5-min breakdown trigger

    Returns:
        dict with 'has_short_signal', 'prerequisites_met', 'setup_detail',
                  'research_note' (never a trade recommendation)
    """
    result = {
        "has_short_signal":       False,
        "prerequisites_met":      False,
        "missing_prerequisites":  [],
        "setup_detail":           {},
        "research_note": (
            "📊 做空研究 (Martin Luk Chapter 16) — 僅供市場健康參考，非交易建議"
        ),
    }

    if not getattr(C, "ML_SHORT_RESEARCH_ENABLED", True):
        return result

    if df is None or df.empty or len(df) < 60:
        return result

    missing = []

    # Prerequisite 1: Market environment check
    mkt_regime = (market_data or {}).get("regime", "UNKNOWN")
    market_ok = mkt_regime in ("MARKET_IN_CORRECTION", "DOWNTREND")
    if not market_ok:
        missing.append("市場需處於修正/下跌趨勢")

    # Prerequisite 2: Check if stock's weekly is weak (9 EMA < 21 EMA)
    # Proxy using daily: if 50-day EMA is declining
    ema50_col = "EMA_50"
    weekly_weak = False
    if ema50_col in df.columns and len(df) >= 20:
        ema50_now   = float(df[ema50_col].iloc[-1])
        ema50_prior = float(df[ema50_col].iloc[-20])
        weekly_weak = ema50_now < ema50_prior
    if not weekly_weak:
        missing.append("股票週線需顯弱勢 (50 EMA 下行)")

    # Setup detection: Picture Perfect Short pattern
    # Step 1: Price was above 50 EMA, then broke below
    ema50_val = float(df["EMA_50"].iloc[-1]) if ema50_col in df.columns else None
    close = float(df["Close"].iloc[-1])
    below_ema50 = ema50_val is not None and close < ema50_val

    # Step 2: After breakdown, did price rally back to declining EMAs (9/21)?
    ema9_val  = float(df["EMA_9"].iloc[-1])  if "EMA_9"  in df.columns else None
    ema21_val = float(df["EMA_21"].iloc[-1]) if "EMA_21" in df.columns else None
    ema9_declining  = (len(df) >= 10 and ema9_val  is not None
                       and float(df["EMA_9"].iloc[-10])  > ema9_val)
    ema21_declining = (len(df) >= 10 and ema21_val is not None
                       and float(df["EMA_21"].iloc[-10]) > ema21_val)

    # Price near declining EMA (potential resistance)
    near_declining_ema = False
    if ema21_val and ema21_declining and close > 0:
        pct_vs_ema21 = abs(close / ema21_val - 1.0) * 100
        near_declining_ema = pct_vs_ema21 <= 3.0

    # IWM lagging check
    iwm_lag_ok = True  # Default to not blocking (can't always get this)
    min_lag = getattr(C, "ML_SHORT_IWM_LAG_MIN", -3.0)
    # Would need IWM data which we can't easily get here without extra API call
    # Flag as informational only

    has_short_signal = (below_ema50 and ema21_declining and near_declining_ema
                        and len(missing) == 0)
    prerequisites_met = len(missing) == 0

    result.update({
        "has_short_signal":       has_short_signal,
        "prerequisites_met":      prerequisites_met,
        "missing_prerequisites":  missing,
        "setup_detail": {
            "below_ema50":         below_ema50,
            "ema50_val":           round(ema50_val, 2) if ema50_val else None,
            "ema21_declining":     ema21_declining,
            "near_declining_ema":  near_declining_ema,
            "weekly_weak":         weekly_weak,
            "market_regime":       mkt_regime,
        },
        "research_note": (
            "📊 [做空研究信號] 此股符合 Martin Luk Picture Perfect Short 模式特徵 — "
            "僅供市場健康參考，本系統不執行做空交易建議"
            if has_short_signal else
            "📊 做空研究: 條件未達 (" + "; ".join(missing) + ")"
            if missing else
            "📊 做空研究: 形態條件不完整"
        ),
    })
    return result


def _print_report(r: dict) -> None:
    """Print terminal report following Martin Luk's framework."""
    ticker = r["ticker"]
    stars = r["capped_stars"]
    rec = r["recommendation"]
    rec_zh = r["recommendation_zh"]
    veto = r.get("veto")
    close = r.get("close", 0)
    adr = r.get("adr", 0)

    star_symbol = "⭐" * int(stars) + ("½" if (stars % 1) else "")

    print(f"\n{_BOLD}{'═'*70}{_RESET}")
    print(f"{_BOLD}  MARTIN LUK PULLBACK ANALYSIS — {ticker}{_RESET}")
    print(f"{'═'*70}")
    print(f"  收盤價 Close:       ${close:.2f}")
    print(f"  ADR (14日):         {adr:.1f}%")
    print(f"  日成交額 $Vol:      ${r.get('dollar_volume_m', 0):.1f}M")
    print()

    setup = r.get("setup_type", {})
    print(f"  {_BOLD}形態類型: {setup.get('primary_setup', 'UNKNOWN')}{_RESET}")
    print(f"  信心度: {setup.get('confidence', 0):.1%}")
    print()

    if veto:
        name = "止損距離過大" if veto == "RISK" else "熊市否決"
        print(f"  {_RED}{_BOLD}⛔ {name} — 不交易{_RESET}")
    else:
        colour = _GREEN if rec in ("BUY", "STRONG BUY") else _YELLOW if rec == "WATCH" else _RED
        print(f"  {_BOLD}星級評分: {star_symbol} ({stars} ⭐){_RESET}")
        print(f"  {colour}{_BOLD}建議: {rec} — {rec_zh}{_RESET}")

    print()
    print(f"  {'─'*60}")
    print(f"  {_BOLD}七維評分{_RESET}:")
    dim_labels = {
        "A": "EMA結構 EMA Structure",
        "B": "回調品質 Pullback Quality",
        "C": "AVWAP匯合 AVWAP Confluence",
        "D": "成交量模式 Volume Pattern",
        "E": "風險回報 Risk/Reward",
        "F": "相對強度 Relative Strength",
        "G": "市場環境 Market Timing",
    }
    for dim_key, label in dim_labels.items():
        ds = r["dim_scores"].get(dim_key, {})
        sc = ds.get("score", 0)
        bar = _GREEN if sc > 0 else _RED if sc < 0 else ""
        print(f"  {dim_key}: {label:<32} {bar}{sc:+.1f}{_RESET}")

    tp = r.get("trade_plan", {})
    if tp.get("action") == "BUY":
        print()
        print(f"  {'─'*60}")
        print(f"  {_BOLD}交易計劃 Trade Plan:{_RESET}")
        print(f"  公式: {tp.get('formula', '—')}")
        print(f"  進場 Entry:    ${tp.get('entry', 0):.2f}")
        print(f"  止損 Stop:     ${tp.get('stop', 0)}")
        print(f"  止損距離:      {tp.get('stop_pct', 0):.1f}%")
        print(f"  倉位 Shares:   {tp.get('shares', 0)}")
        print(f"  倉位金額:      ${tp.get('position_value', 0):,.0f} ({tp.get('position_pct', 0):.1f}%)") 
        print(f"  風險金額:      ${tp.get('risk_dollars', 0):.0f}")
        print(f"  3R 目標:       ${tp.get('target_1_price', '—')}")
        print(f"  5R 目標:       ${tp.get('target_2_price', '—')}")
        print(f"  追蹤止損:      {tp.get('trail_label', '—')}")

    print(f"{'═'*70}\n")
