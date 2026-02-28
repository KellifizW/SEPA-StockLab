"""
modules/qm_analyzer.py  —  Qullamaggie 6-Dimension Star Rating Engine
═══════════════════════════════════════════════════════════════════════
Implements the complete star-rating system from Section 6 of
QullamaggieStockguide.md for deep single-stock analysis.

Six rating dimensions (Section 6.1):
  A. Momentum Quality     — sector leader? relative strength? institutional footprint?
  B. ADR Level            — primary speed gate; has INDEPENDENT VETO POWER
  C. Consolidation Qual.  — tightness, higher lows, clean range, time
  D. MA Alignment         — surfing which MA? all rising? 20SMA catching up?
  E. Stock Type           — institutional (natural flow) vs retail-pump
  F. Market Timing        — bull/bear/correction; sector tailwind

Star rating → position sizing (Section 6.2):
  5+★ → 20-25%   5★ → 15-25%   4-4.5★ → 10-15%   3-3.5★ ≤10%   <3★ → PASS

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
# Dimension A — Momentum Quality
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_a(df: pd.DataFrame, rs_rank: Optional[float] = None) -> dict:
    """
    Score Dimension A: Momentum Quality (0 to +2 star adjustment).

    QM rules:
      • Sector leader (top momentum in its sector)       → +1 to +2
      • Relative strength (holds better during drawdown) → proxy via RS rank
      • Institutional footprint (natural flow: Higher Lows + consistent buying)
      • Multi-timeframe momentum confirmation            → +0.5 to +1
      • Retail-pump with no natural flow                 → -1
    """
    from modules.data_pipeline import get_momentum_returns

    mom = get_momentum_returns(df)
    m1 = mom.get("1m") or 0
    m3 = mom.get("3m") or 0
    m6 = mom.get("6m") or 0

    adj = 0.0
    detail = {}

    # Multi-timeframe confirmation: count how many windows pass
    passes = sum([
        m1 >= getattr(C, "QM_MOMENTUM_1M_MIN_PCT", 25.0),
        m3 >= getattr(C, "QM_MOMENTUM_3M_MIN_PCT", 50.0),
        m6 >= getattr(C, "QM_MOMENTUM_6M_MIN_PCT", 150.0),
    ])
    detail["momentum_windows_passed"] = passes
    detail["m1"] = round(m1, 1)
    detail["m3"] = round(m3, 1)
    detail["m6"] = round(m6, 1)

    if passes >= 3:
        adj += 1.0   # All three windows → very strong momentum leadership
    elif passes == 2:
        adj += 0.5   # Two windows → solid
    elif passes == 1:
        adj += 0.0   # Only one → minimal
    else:
        adj -= 0.5   # None → below minimum threshold

    # RS rank bonus (if available — from rs_ranking.py or scan result)
    if rs_rank is not None:
        if rs_rank >= 90:
            adj += 0.5
        elif rs_rank >= 80:
            adj += 0.25
        elif rs_rank < 60:
            adj -= 0.25
        detail["rs_rank"] = rs_rank

    # Extreme 6M momentum (≥300% = potential HTF / parabolic)
    if m6 >= 300:
        adj += 0.5
    elif m6 >= 150:
        adj += 0.25

    detail["adjustment"] = round(adj, 2)
    detail["tooltip"] = (
        "動量品質 (Momentum Quality)\n"
        "通過的時間框架越多、RSI排名越高 → 越強\n"
        f"1M={m1:.0f}% 3M={m3:.0f}% 6M={m6:.0f}%"
    )
    return {"score": round(adj, 2), "detail": detail}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension B — ADR Level (has INDEPENDENT VETO POWER)
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_b(df: pd.DataFrame) -> dict:
    """
    Score Dimension B: ADR Level.

    QM rules:
      ≥15%    → +1 (explosive)
      8-15%   → ±0 (ideal baseline)
      5-8%    → -0.5 (marginal — still tradeable)
      3-5%    → VETO (-2 or pass)
      <3%     → HARD VETO (do not trade regardless)
    """
    from modules.data_pipeline import get_adr

    adr  = get_adr(df)
    adj  = 0.0
    veto = False
    detail = {"adr": round(adr, 2)}

    if adr < 3.0:
        veto  = True
        adj   = -2.0
        detail["veto_reason"] = "ADR < 3% — 絕對硬否決，不交易 (hard veto)"
    elif adr < getattr(C, "QM_MIN_ADR_PCT", 5.0):
        veto  = True
        adj   = -2.0
        detail["veto_reason"] = f"ADR {adr:.1f}% < 5% — QM獨立否決權 (ADR veto)"
    elif adr < getattr(C, "QM_ADR_PENALTY_MARGINAL", 8.0):
        adj   = -0.5
        detail["note"] = f"ADR {adr:.1f}% 達到最低門檻 5-8%，略低於理想值"
    elif adr >= getattr(C, "QM_ADR_BONUS_HIGH", 15.0):
        adj   = +1.0
        detail["note"] = f"ADR {adr:.1f}% ≥ 15% — 極度爆發潛力"
    else:
        adj   = 0.0
        detail["note"] = f"ADR {adr:.1f}% 在 8-15% 理想範圍"

    detail["adjustment"] = round(adj, 2)
    detail["is_veto"]    = veto
    detail["tooltip"]    = (
        "ADR 水平 (Average Daily Range)\n"
        "ADR = 過去14天每天 (最高/最低 - 1) 的平均值\n"
        "<5% 觸發ADR獨立否決權 — 無論其他評分多好都不交易\n"
        f"當前ADR: {adr:.1f}% (門檻: 5%最低 / 8%理想 / 15%極佳)"
    )
    return {"score": round(adj, 2), "detail": detail, "is_veto": veto}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension C — Consolidation Quality
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_c(df: pd.DataFrame) -> dict:
    """
    Score Dimension C: Consolidation Quality.

    QM rules (by importance):
      1. Higher Lows (most important)
      2. Tightness (K-bars contracting)
      3. Clean range (clear support + resistance)
      4. Consolidation duration (3-60 days ideal)
    """
    from modules.data_pipeline import get_higher_lows, get_consolidation_tightness

    hl       = get_higher_lows(df)
    tight    = get_consolidation_tightness(df)
    has_hl   = hl.get("has_higher_lows", False)
    n_lows   = hl.get("num_lows", 0)
    is_tight = tight.get("is_tight", False)
    t_ratio  = tight.get("tightness_ratio", 1.0) or 1.0
    rtrend   = tight.get("range_trend", "stable")

    adj = 0.0
    detail = {
        "has_higher_lows":  has_hl,
        "num_lows":         n_lows,
        "is_tight":         is_tight,
        "tightness_ratio":  round(t_ratio, 3),
        "range_trend":      rtrend,
    }

    # Higher Lows scoring
    if has_hl and n_lows >= 3:
        adj += 0.8
    elif has_hl:
        adj += 0.5
    else:
        adj -= 0.5

    # Tightness scoring
    if is_tight and t_ratio < 0.3:
        adj += 1.0   # Extremely tight — very strong quality signal
    elif is_tight:
        adj += 0.5   # Tight
    elif t_ratio < 0.7:
        adj += 0.2   # Moderate tightness
    elif rtrend == "expanding":
        adj -= 0.5   # Range expanding = bad setup

    # Range trend bonus
    if rtrend == "contracting":
        adj += 0.3   # Actively contracting = building

    # Volume dry-up during consolidation
    recent5_vol = float(df["Volume"].tail(5).mean()) if len(df) >= 5 else 0
    avg20_vol   = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else recent5_vol
    if avg20_vol > 0 and recent5_vol < avg20_vol * 0.65:
        adj += 0.2
        detail["vol_dryup"] = True
    else:
        detail["vol_dryup"] = False

    # Cap
    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    detail["tooltip"] = (
        "整理品質 (Consolidation Quality)\n"
        "Higher Lows = 機構資金累積的物理證據（最重要）\n"
        "收緊 = 供需趨近平衡 → 突破時爆發力更強\n"
        "成交量萎縮 = 無人急著賣，供給稀缺"
    )
    return {"score": round(adj, 2), "detail": detail}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension D — MA Alignment
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_d(df: pd.DataFrame) -> dict:
    """
    Score Dimension D: Moving Average Alignment.

    QM rules:
      Surfing 20SMA + all MAs rising     → +1 (golden setup)
      Surfing 10SMA                      → +0.5 (aggressive, very strong)
      Surfing 50SMA, 20SMA rising        → ±0 (slower but valid)
      Undercut & Reclaim 20SMA           → +0.5 bonus
      Price too far above 20SMA          → -1 to -2
      MAs pointing down / tangled        → HARD filter (eliminated in Stage 3)
    """
    from modules.data_pipeline import get_ma_alignment

    ma       = get_ma_alignment(df)
    surf     = ma.get("surfing_ma", 0)
    all_up   = ma.get("all_ma_rising", False)
    s20_up   = ma.get("sma_20_rising", False)
    s10_up   = ma.get("sma_10_rising", False)
    pct_20   = ma.get("price_vs_sma_20")   # % above/below 20SMA
    pct_50   = ma.get("price_vs_sma_50")

    adj    = 0.0
    detail = {
        "surfing_ma":       surf,
        "all_ma_rising":    all_up,
        "sma_20_rising":    s20_up,
        "pct_vs_sma_20":    pct_20,
        "pct_vs_sma_50":    pct_50,
    }

    # Primary surfing bonus
    if surf == 20 and all_up:
        adj += 1.0   # Perfect golden line setup
    elif surf == 20:
        adj += 0.5
    elif surf == 10:
        adj += 0.5   # 10SMA = aggressive bull phase
    elif surf == 50 and s20_up:
        adj += 0.0   # Slow but acceptable
    elif surf == 50:
        adj -= 0.25  # 50SMA without upward 20SMA trend
    else:
        adj -= 0.5   # Not surfing any MA

    # Distance penalty: too far above 20SMA = higher failure rate
    if pct_20 is not None:
        tolerance = getattr(C, "QM_SURFING_TOLERANCE_PCT", 3.0)
        if pct_20 > 20:
            adj -= 1.5   # Very extended — common breakout failure reason
        elif pct_20 > 12:
            adj -= 1.0
        elif pct_20 > 7:
            adj -= 0.5
        elif pct_20 < -tolerance and not s20_up:
            adj -= 0.5   # Below AND falling 20SMA

    # Undercut-and-Reclaim bonus (sophisticated pattern)
    if pct_20 is not None and s20_up and -tolerance <= pct_20 <= 1.0:
        adj += 0.5   # Just reclaimed 20SMA — ideal position

    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    detail["tooltip"] = (
        "均線對齊 (MA Alignment)\n"
        "20SMA是最重要的均線—Qullamaggie: '神奇的黃線'\n"
        "衝浪20SMA+均線全部向上 = 最佳設置\n"
        "股價距離20SMA超過10-15% → 失敗率上升，應等待20SMA追上來"
    )
    return {"score": round(adj, 2), "detail": detail}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension E — Stock Type
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_e(df: pd.DataFrame, dollar_volume: Optional[float] = None) -> dict:
    """
    Score Dimension E: Stock Type (Institutional vs Retail-Pump).

    QM: institutional stocks have natural flow (fund buying on each dip → HLs).
    Retail-pump stocks need external catalysts to move.

    Proxies for institutional type:
      • Higher Lows without external catalysts → funds accumulating
      • High dollar volume ($10M+ daily) → institutional footprint
      • Stock does NOT require news/PR to move (detectable only manually;
        we use liquidity as proxy)

    Retail-pump proxies:
      • Very low dollar volume (< $5M)
      • Micro-cap / nano-cap characteristics
    """
    from modules.data_pipeline import get_dollar_volume, get_higher_lows

    dv    = dollar_volume if dollar_volume is not None else get_dollar_volume(df)
    hl    = get_higher_lows(df)
    has_hl = hl.get("has_higher_lows", False)

    adj    = 0.0
    detail = {"dollar_volume_m": round(dv / 1_000_000, 2)}

    # Dollar volume as institutional proxy
    if dv >= 10_000_000:
        adj   += 0.5
        detail["type"] = "institutional"
        detail["note"] = f"日成交額 ${dv/1e6:.1f}M ≥ $10M → 機構型"
    elif dv >= 5_000_000:
        adj   += 0.0
        detail["type"] = "mid"
        detail["note"] = f"日成交額 ${dv/1e6:.1f}M ($5M-$10M) → 中等流動性"
    elif dv >= 2_000_000:
        adj   -= 0.5
        detail["type"] = "retail"
        detail["note"] = f"日成交額 ${dv/1e6:.1f}M < $5M → 散戶型，信心降低"
    else:
        adj   -= 1.0
        detail["type"] = "micro"
        detail["note"] = f"日成交額 ${dv/1e6:.1f}M < $2M → 流動性不足"

    # Natural flow bonus: consistent HLs suggest institutional demand
    if has_hl and dv >= 5_000_000:
        adj += 0.5
        detail["natural_flow"] = True
    else:
        detail["natural_flow"] = False

    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    detail["tooltip"] = (
        "股票類型 (Stock Type)\n"
        "機構型 = 每次回調都有基金進場接手 → Higher Lows = 機構累積的腳印\n"
        "散戶型 = 需要外力（PR/新聞/推薦）才會動 → 突破可靠性較低\n"
        f"日成交額 ${dv/1e6:.1f}M (建議 ≥$5M，理想 ≥$10M)"
    )
    return {"score": round(adj, 2), "detail": detail}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension F — Market Timing
# ─────────────────────────────────────────────────────────────────────────────

def _score_dim_f() -> dict:
    """
    Score Dimension F: Market Timing / Environment.

    QM rules:
      Bull market recovery after dip  → +1 to +2 (best time to trade)
      Stable confirmed bull market    → ±0
      Sideways / choppy market        → -0.5 to -1
      Bear market / downtrend         → -2 or BLOCK entirely

    We pull the current market regime from market_env.py.
    """
    adj    = 0.0
    detail = {}
    veto   = False

    try:
        from modules.market_env import assess as mkt_assess
        mkt = mkt_assess(verbose=False)
        regime = mkt.get("regime", "UNKNOWN")
        detail["regime"] = regime

        regime_map = {
            "CONFIRMED_UPTREND":      (+0.5, "確認多頭市場 → 標準操作"),
            "UPTREND_UNDER_PRESSURE": ( 0.0, "多頭受壓 → 正常倉位，謹慎"),
            "MARKET_IN_CORRECTION":   (-1.0, "市場調整中 → 縮小倉位"),
            "DOWNTREND":              (-2.0, "空頭市場 → 封鎖突破操作"),
            "BULL_CONFIRMED":         (+0.5, "確認多頭"),
            "BULL_UNCONFIRMED":       (+0.0, "多頭未確認"),
        }
        if regime in regime_map:
            adj, note = regime_map[regime]
            detail["note"] = note
        else:
            detail["note"] = f"市場狀態: {regime}"

        if regime == "DOWNTREND" and getattr(C, "QM_BLOCK_IN_BEAR", True):
            veto = True
            detail["veto_reason"] = "熊市封鎖 — QM突破操作已停止"

    except Exception as exc:
        logger.warning("[QM DimF] Could not fetch market env: %s", exc)
        detail["note"] = "無法取得市場數據，假設中性"
        detail["regime"] = "UNKNOWN"

    adj = max(-2.0, min(adj, 2.0))
    detail["adjustment"] = round(adj, 2)
    detail["tooltip"] = (
        "時機環境 (Market Timing)\n"
        "牛市回調後的恢復期 = 最佳突破時機（+1~+2）\n"
        "確認牛市 = 標準操作（±0）\n"
        "市場調整 = 縮小倉位（-1）\n"
        "熊市 = 封鎖所有突破操作（-2、否決）"
    )
    return {"score": round(adj, 2), "detail": detail, "is_veto": veto}


# ─────────────────────────────────────────────────────────────────────────────
# Star rating aggregation + trade plan
# ─────────────────────────────────────────────────────────────────────────────

def compute_star_rating(
    dim_scores: dict[str, dict],
    base: float = None,
) -> dict:
    """
    Aggregate 6-dimension scores into a final star rating.

    Returns:
        dict with 'stars', 'capped_stars', 'recommendation', 'position_pct'
    """
    base = base if base is not None else getattr(C, "QM_STAR_BASE", 3.0)
    weights = {
        "A": getattr(C, "QM_STAR_DIM_A_WEIGHT", 0.25),
        "B": getattr(C, "QM_STAR_DIM_B_WEIGHT", 0.20),
        "C": getattr(C, "QM_STAR_DIM_C_WEIGHT", 0.25),
        "D": getattr(C, "QM_STAR_DIM_D_WEIGHT", 0.15),
        "E": getattr(C, "QM_STAR_DIM_E_WEIGHT", 0.10),
        "F": getattr(C, "QM_STAR_DIM_F_WEIGHT", 0.05),
    }

    # Check vetoes first
    adr_veto    = dim_scores.get("B", {}).get("is_veto", False)
    market_veto = dim_scores.get("F", {}).get("is_veto", False)

    if adr_veto:
        return {
            "stars": 0.0, "capped_stars": 0.0,
            "recommendation": "PASS",
            "recommendation_zh": "放棄 — ADR 否決",
            "position_pct_min": 0.0, "position_pct_max": 0.0,
            "veto": "ADR",
        }
    if market_veto:
        return {
            "stars": 0.0, "capped_stars": 0.0,
            "recommendation": "PASS",
            "recommendation_zh": "放棄 — 熊市否決",
            "position_pct_min": 0.0, "position_pct_max": 0.0,
            "veto": "MARKET",
        }

    # Weighted sum of dimension adjustments
    total_adj = 0.0
    for dim_key, w in weights.items():
        score = dim_scores.get(dim_key, {}).get("score", 0.0)
        total_adj += score * (w / sum(weights.values()))  # normalise weights to 1.0

    # Scale total_adj: each dimension ranges ~[-2, +2]; scale to ~[-2.5, +2.5 stars]
    raw_stars = base + total_adj * 2.5
    capped    = round(max(0.0, min(raw_stars, 5.5)) * 2) / 2  # round to nearest 0.5

    # Position sizing lookup
    sizing = getattr(C, "QM_POSITION_SIZING", {
        "5+": (20.0, 25.0), "5": (15.0, 25.0),
        "4": (10.0, 15.0),  "3": (5.0, 10.0), "0": (0.0, 0.0),
    })
    if capped >= 5.5:
        lo, hi = sizing.get("5+", (20, 25))
        rec    = "STRONG BUY"
        rec_zh = "強力買入"
    elif capped >= 5.0:
        lo, hi = sizing.get("5", (15, 25))
        rec    = "BUY"
        rec_zh = "買入"
    elif capped >= 4.0:
        lo, hi = sizing.get("4", (10, 15))
        rec    = "BUY"
        rec_zh = "買入"
    elif capped >= 3.0:
        lo, hi = sizing.get("3", (5, 10))
        rec    = "WATCH"
        rec_zh = "觀察，小倉位"
    else:
        lo, hi = sizing.get("0", (0, 0))
        rec    = "PASS"
        rec_zh = "放棄"

    return {
        "stars":             round(raw_stars, 2),
        "capped_stars":      capped,
        "recommendation":    rec,
        "recommendation_zh": rec_zh,
        "position_pct_min":  lo,
        "position_pct_max":  hi,
        "veto": None,
    }


def _build_trade_plan(stars: float, row: dict) -> dict:
    """
    Generate a concrete trade action plan based on star rating and current data.
    """
    adr       = row.get("adr", 0)
    close     = row.get("close", 0)
    sma_10    = row.get("sma_10")
    sma_20    = row.get("sma_20")
    account   = getattr(C, "ACCOUNT_SIZE", 100_000)
    
    # LOD (low of day) is preferred entry/stop, fallback to 10SMA
    lod         = row.get("lod")
    if not lod or lod <= 0:
        lod = sma_10 or close
    lod_stop    = round(lod * (1 - getattr(C, "QM_STOP_PCT", 0.07)), 2) if lod else None

    # ── Position allocation % based on star rating ────────────────────────
    sizing = getattr(C, "QM_POSITION_SIZING", {
        "5+": (20.0, 25.0), "5": (15.0, 25.0),
        "4": (10.0, 15.0),  "3": (5.0, 10.0), "0": (0.0, 0.0),
    })
    if stars >= 5.5:
        allocation_pct_lo, allocation_pct_hi = sizing.get("5+", (20, 25))
    elif stars >= 5.0:
        allocation_pct_lo, allocation_pct_hi = sizing.get("5", (15, 25))
    elif stars >= 4.0:
        allocation_pct_lo, allocation_pct_hi = sizing.get("4", (10, 15))
    elif stars >= 3.0:
        allocation_pct_lo, allocation_pct_hi = sizing.get("3", (5, 10))
    else:
        allocation_pct_lo, allocation_pct_hi = sizing.get("0", (0, 0))

    # Position value in USD
    pos_value_lo      = account * (allocation_pct_lo / 100.0)
    pos_value_hi      = account * (allocation_pct_hi / 100.0)
    pos_value_mid     = (pos_value_lo + pos_value_hi) / 2.0
    
    # Calculate share count (use mid-point for primary calculation)
    shares = int(pos_value_mid / close) if close > 0 else 1
    max_shares = int(account * 0.25 / close) if close > 0 else 1  # Never exceed 25% of account
    shares = min(shares, max(1, max_shares))
    
    # Actual position value after share rounding
    pos_value = shares * close
    
    # Risk per share (from entry to day1 stop)
    risk_per_share = round(close - lod_stop, 2) if lod_stop else 0.0
    total_risk_usd = shares * risk_per_share
    risk_pct_of_account = (total_risk_usd / account * 100) if account > 0 else 0.0
    stop_pct = round(risk_pct_of_account, 2)

    # Day 2-3 stops and profit taking
    profit_pct_1   = getattr(C, "QM_PROFIT_TAKE_1ST_GAIN", 10.0)
    profit_target  = round(close * (1 + profit_pct_1 / 100), 2) if close else None
    take_pct       = 25.0 if stars >= 5.0 else 33.0 if stars >= 4.0 else 50.0
    
    # Day 2 stop = cost (break-even = entry price)
    day2_stop_px   = round(close, 2) if close else None
    
    # Day 3+ stop = 10MA trail (actual price) or None if not available
    day3_stop_px   = round(sma_10, 2) if sma_10 else None
    
    ma_period      = getattr(C, 'QM_TRAIL_MA_PERIOD', 10)

    return {
        "action":               "BUY" if stars >= 3.0 else "PASS",
        "position_lo_pct":      allocation_pct_lo,
        "position_hi_pct":      allocation_pct_hi,
        "suggested_shares":     shares,
        "suggested_value_usd":  round(pos_value, 0),
        "suggested_risk_usd":   round(total_risk_usd, 2),
        "day1_stop":            lod_stop,
        "day1_stop_pct_risk":   stop_pct,
        "day2_stop":            day2_stop_px,
        "day2_stop_label":      "成本價 Break-even",
        "day3plus_stop":        day3_stop_px,
        "day3plus_stop_label":  f"{ma_period}MA 追蹤 Trail",
        "sma_10_trail":         day3_stop_px,
        "profit_take_day":      f"Day 3-5 或獲利達{profit_pct_1:.0f}%",
        "profit_take_qty":      f"先出 {take_pct:.0f}% 持倉",
        "profit_target_px":     profit_target,
        "remainder_management": f"剩餘持倉用{ma_period}MA管理，跟随趋势",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def analyze_qm(ticker: str,
               df: pd.DataFrame = None,
               rs_rank: Optional[float] = None,
               print_report: bool = True) -> dict:
    """
    Full Qullamaggie 6-dimension analysis for a single stock.

    Args:
        ticker:       Stock symbol
        df:           Enriched OHLCV DataFrame (fetched if None)
        rs_rank:      Pre-computed RS percentile rank (0-99)
        print_report: Print terminal report

    Returns:
        Comprehensive dict with:
            'ticker', 'stars', 'capped_stars', 'recommendation',
            'recommendation_zh', 'dim_scores' (A-F detail dicts),
            'setup_type', 'trade_plan', 'scan_date'
    """
    from modules.data_pipeline import get_enriched, get_dollar_volume

    if df is None or df.empty:
        df = get_enriched(ticker, period="1y")

    if df.empty:
        return {"ticker": ticker, "error": "No price data available",
                "stars": 0.0, "capped_stars": 0.0,
                "recommendation": "PASS", "recommendation_zh": "無法取得數據"}

    close = float(df["Close"].iloc[-1]) if not df.empty else 0.0
    dv    = get_dollar_volume(df)

    # ── Detect setup type ──────────────────────────────────────────────────
    from modules.qm_setup_detector import detect_setup_type
    setup = detect_setup_type(df, ticker=ticker)

    # ── Score all 6 dimensions ─────────────────────────────────────────────
    dim_a = _score_dim_a(df, rs_rank=rs_rank)
    dim_b = _score_dim_b(df)
    dim_c = _score_dim_c(df)
    dim_d = _score_dim_d(df)
    dim_e = _score_dim_e(df, dollar_volume=dv)
    dim_f = _score_dim_f()

    dim_scores = {"A": dim_a, "B": dim_b, "C": dim_c,
                  "D": dim_d, "E": dim_e, "F": dim_f}

    # ── Aggregate star rating ──────────────────────────────────────────────
    rating  = compute_star_rating(dim_scores)
    stars   = rating["capped_stars"]
    rec     = rating["recommendation"]
    rec_zh  = rating["recommendation_zh"]

    # MA and momentum quick refs
    from modules.data_pipeline import get_ma_alignment, get_momentum_returns, get_adr
    ma  = get_ma_alignment(df)
    mom = get_momentum_returns(df)
    adr = get_adr(df)

    row_for_plan = {
        "close": close,
        "adr":   adr,
        "sma_10": ma.get("sma_10"),
        "sma_20": ma.get("sma_20"),
        "low": float(df["Low"].iloc[-1]) if not df.empty else None,
    }
    trade_plan = _build_trade_plan(stars, row_for_plan)

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
        "position_pct_min":  rating["position_pct_min"],
        "position_pct_max":  rating["position_pct_max"],
        "veto":              rating.get("veto"),
        "dim_scores":        dim_scores,
        "setup_type":        setup,
        "trade_plan":        trade_plan,
        "ma":                {k: v for k, v in ma.items()},
        "momentum":          mom,
        # Flatten momentum fields for template convenience
        "mom_1m":            mom.get("1m"),
        "mom_3m":            mom.get("3m"),
        "mom_6m":            mom.get("6m"),
    }

    if print_report:
        _print_report(result)

    return result


def _print_report(r: dict) -> None:
    """Print terminal report following Qullamaggie's framework."""
    ticker = r["ticker"]
    stars  = r["capped_stars"]
    rec    = r["recommendation"]
    rec_zh = r["recommendation_zh"]
    veto   = r.get("veto")
    adr    = r.get("adr", 0)
    close  = r.get("close", 0)

    star_symbol = "⭐" * int(stars) + ("½" if (stars % 1) else "")

    print(f"\n{_BOLD}{'═'*70}{_RESET}")
    print(f"{_BOLD}  QULLAMAGGIE ANALYSIS — {ticker}{_RESET}")
    print(f"{'═'*70}")
    print(f"  收盤價 Close:       ${close:.2f}")
    print(f"  ADR (14日):         {adr:.1f}%", end="")
    if veto == "ADR":
        print(f"  {_RED}← ADR否決{_RESET}")
    else:
        print()
    print(f"  日成交額 $Vol:      ${r.get('dollar_volume_m', 0):.1f}M")
    print()

    print(f"  {_BOLD}形態類型: {r['setup_type']['primary_type']}{_RESET}")
    print(f"  {r['setup_type']['description_zh']}")
    print()

    if veto:
        name = "ADR 否決（速度不足）" if veto == "ADR" else "熊市否決"
        print(f"  {_RED}{_BOLD}⛔ {name} — 不交易{_RESET}")
    else:
        colour = _GREEN if rec in ("BUY", "STRONG BUY") else _YELLOW if rec == "WATCH" else _RED
        print(f"  {_BOLD}星級評分: {star_symbol} ({stars} ⭐){_RESET}")
        print(f"  {colour}{_BOLD}建議: {rec} — {rec_zh}{_RESET}")
        pmin = r.get("position_pct_min", 0)
        pmax = r.get("position_pct_max", 0)
        print(f"  推薦倉位: {pmin:.0f}% – {pmax:.0f}%")

    print()
    print(f"  {'─'*60}")
    print(f"  {_BOLD}六維評分{_RESET}:")
    dim_labels = {
        "A": "動量品質 Momentum",
        "B": "ADR 水平",
        "C": "整理品質 Consolidation",
        "D": "均線對齊 MA Alignment",
        "E": "股票類型 Stock Type",
        "F": "時機環境 Market Timing",
    }
    for dim_key, label in dim_labels.items():
        ds = r["dim_scores"].get(dim_key, {})
        sc = ds.get("score", 0)
        bar = _GREEN if sc > 0 else _RED if sc < 0 else ""
        print(f"  {dim_key}: {label:<32} {bar}{sc:+.1f}{_RESET}")

    tp = r["trade_plan"]
    if tp.get("action") != "PASS":
        print()
        print(f"  {'─'*60}")
        print(f"  {_BOLD}交易計劃 Trade Plan:{_RESET}")
        print(f"  Day 1 止損 Stop:   {tp.get('day1_stop', '—')}  "
              f"(風險 {tp.get('day1_stop_pct_risk', '—')}%)")
        print(f"  Day 2 止損:        {tp.get('day2_stop', '—')}")
        print(f"  Day 3+ 追蹤止損:   {tp.get('day3plus_stop', '—')}")
        print(f"  獲利了結:          {tp.get('profit_take_day', '—')} — "
              f"{tp.get('profit_take_qty', '—')}")

    print(f"{'═'*70}\n")
