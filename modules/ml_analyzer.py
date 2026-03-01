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

    detail["pullback"] = pb
    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    return {"score": round(adj, 2), "detail": detail, "is_veto": False}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension C — AVWAP Confluence
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_c(df: pd.DataFrame) -> dict:
    """
    C: AVWAP Confluence scoring.
    Martin Luk's core indicator: AVWAP from swing high (overhead supply)
    and swing low (dynamic support).

    Bullish: Price above both AVWAPs; Reclaiming supply AVWAP.

    Adjustment range: [-1.5, +1.5]
    """
    from modules.data_pipeline import get_avwap_from_swing_high, get_avwap_from_swing_low

    avwap_h = get_avwap_from_swing_high(df)
    avwap_l = get_avwap_from_swing_low(df)

    adj = 0.0
    detail = {}

    above_supply = avwap_h.get("above_avwap", False)
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

    # Proximity to AVWAP (near = potential bounce point)
    h_pct = avwap_h.get("price_vs_avwap_pct")
    l_pct = avwap_l.get("price_vs_avwap_pct")
    if l_pct is not None and 0 <= l_pct <= 2.0:
        adj += 0.3
        detail["support_proximity"] = f"接近支撐 AVWAP ({l_pct:.1f}%) — 潛在反彈點"

    detail["avwap_high"] = {k: v for k, v in avwap_h.items() if k != "avwap_series"}
    detail["avwap_low"] = {k: v for k, v in avwap_l.items() if k != "avwap_series"}

    adj = max(-1.5, min(adj, 1.5))
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

def _score_dim_g() -> dict:
    """
    G: Market Environment scoring.
    Martin reduces activity in corrections, blocks in downtrend.

    Adjustment range: [-2.0, +1.0] with VETO for DOWNTREND.
    """
    adj = 0.0
    detail = {}
    veto = False

    try:
        from modules.market_env import assess as mkt_assess
        mkt = mkt_assess(verbose=False)
        regime = mkt.get("regime", "UNKNOWN")
        detail["regime"] = regime

        if regime in ("CONFIRMED_UPTREND", "BULL_CONFIRMED"):
            adj += 0.8
            detail["market"] = "確認上升趨勢 ✓"
        elif regime == "UPTREND_UNDER_PRESSURE":
            adj += 0.2
            detail["market"] = "上升趨勢受壓"
        elif regime == "MARKET_IN_CORRECTION":
            adj -= 0.8
            detail["market"] = "市場修正中 — 減少倉位"
        elif regime == "DOWNTREND":
            adj -= 2.0
            veto = True
            detail["market"] = "⛔ 下降趨勢 — 停止交易"
        else:
            detail["market"] = f"市場狀態: {regime}"
    except Exception as exc:
        logger.debug("[ML DimG] market_env unavailable: %s", exc)
        detail["market"] = "市場環境數據不可用"

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

    # Check vetoes
    risk_veto = dim_scores.get("E", {}).get("is_veto", False)
    mkt_veto = dim_scores.get("G", {}).get("is_veto", False)

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
               print_report: bool = True) -> dict:
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
    dim_g = _score_dim_g()

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

    if print_report:
        _print_report(result)

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
