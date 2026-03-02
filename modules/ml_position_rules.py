"""
modules/ml_position_rules.py  —  Martin Luk 3-Phase Stop & Partial Sell System
═══════════════════════════════════════════════════════════════════════════════
Implements Martin Luk's position management system:

3-Phase Stop System:
  Phase 1 (Day 1):    Stop = LOD minus 0.3% buffer (tighter than QM)
  Phase 2 (Day 2):    Move stop to breakeven (cost price)
  Phase 3 (Day 3+):   Trail using 9 EMA (close-based)

Partial Sell (R-multiple targets):
  Step 1: At 3R → sell 15%
  Step 2: At 5R → sell another 15%
  Step 3: Remaining 70% trails on 9 EMA until close below

Position Sizing (formula-based):
  Shares = (Account × 0.50%) / (Entry − Stop)
  Max single position: 25% of account
  Max stop: 2.5% (hard veto — do not enter if stop exceeds)

Consecutive Loss Protection:
  3 consecutive losses → halve position size for next 2 trades
  5 consecutive losses → stop trading for 1 week

All pure-logic functions — no API calls.  Uses data_pipeline for price data.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Day 1 initial stop (LOD-based, tighter buffer)
# ─────────────────────────────────────────────────────────────────────────────

def get_day1_stop(entry_price: float,
                  day_low: float,
                  buffer_pct: float = None,
                  atr: float = None) -> dict:
    """
    Calculate Day 1 initial stop for Martin Luk pullback entry.
    Stop = LOD - 0.3% buffer (default, tighter than QM's 0.5%).

    Martin Luk keeps stops very tight since entries are on pullback
    to known support (EMA / AVWAP).

    Args:
        entry_price: Entry price
        day_low:     Intraday low on entry day (LOD)
        buffer_pct:  Buffer below LOD (default: ML_LOD_STOP_BUFFER_PCT = 0.3%)
        atr:         Average True Range (for validation)

    Returns:
        dict with 'stop_price', 'risk_pct', 'risk_per_share', 'is_valid'
    """
    if buffer_pct is None:
        buffer_pct = getattr(C, "ML_LOD_STOP_BUFFER_PCT", 0.3)

    max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)

    stop_price = round(day_low * (1.0 - buffer_pct / 100.0), 2)
    risk_pct = (entry_price - stop_price) / entry_price * 100.0 if entry_price > 0 else 99.0
    risk_ps = entry_price - stop_price

    is_valid = risk_pct <= max_stop

    # ATR check
    atr_check = {}
    if atr is not None and atr > 0:
        stop_dist = entry_price - stop_price
        atr_ratio = stop_dist / atr
        atr_check = {
            "atr": round(atr, 4),
            "stop_vs_atr_ratio": round(atr_ratio, 2),
            "is_within_atr": atr_ratio <= 1.0,
        }

    return {
        "stop_price": stop_price,
        "risk_pct": round(risk_pct, 2),
        "risk_per_share": round(risk_ps, 2),
        "is_valid": is_valid,
        "phase": 1,
        "atr_check": atr_check,
        "description_zh": f"Day 1 止損：LOD ${day_low:.2f} 下方 {buffer_pct}%",
        "description_en": f"Day 1 stop: LOD ${day_low:.2f} minus {buffer_pct}% buffer",
        "tooltip": (
            "Martin Luk Day 1 止損\n"
            "= 進場日最低價 (LOD) 下方一小點緩衝\n"
            "Martin 用較緊止損 (0.3%) 因為進場點在已知支撐\n"
            "止損上限: 2.5%，超過不進場"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Day 2 breakeven stop
# ─────────────────────────────────────────────────────────────────────────────

def get_day2_stop(entry_price: float, current_price: float) -> dict:
    """
    Day 2: Move stop to breakeven (entry price).

    Martin Luk: "If the stock doesn't go in your direction by Day 2,
    you want your stop at breakeven. Either the trade works fast or
    you get out flat."

    Args:
        entry_price:   Original entry price
        current_price: Current market price

    Returns:
        dict with action, stop_price, gain_pct
    """
    gain_pct = (current_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0

    if current_price >= entry_price:
        action = "MOVE_TO_BREAKEVEN"
        msg_zh = f"Day 2：止損移至成本價 ${entry_price:.2f} (已漲 {gain_pct:.1f}%)"
    else:
        # If stock is below entry, keep Day 1 stop but flag concern
        action = "HOLD_DAY1_STOP"
        msg_zh = f"Day 2：股價低於成本 ({gain_pct:.1f}%) — 維持 Day 1 止損"

    return {
        "action": action,
        "stop_price": round(entry_price, 2),
        "phase": 2,
        "gain_pct": round(gain_pct, 2),
        "description_zh": msg_zh,
        "description_en": f"Day 2: {'Move to breakeven' if action == 'MOVE_TO_BREAKEVEN' else 'Keep Day1 stop'} (gain {gain_pct:.1f}%)",
        "tooltip": (
            "Martin Luk Day 2 規則\n"
            "止損移至成本價 — 讓這筆交易變成零風險\n"
            "如果股價未上漲表示這個 setup 可能無效\n"
            "要快速脫離風險"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Day 3+ 9 EMA trailing stop
# ─────────────────────────────────────────────────────────────────────────────

def get_day3_trail_stop(df: pd.DataFrame,
                         entry_price: float,
                         current_stop: float,
                         r_multiple: float = 0.0) -> dict:
    """
    Day 3+: Adaptive EMA trailing — upgrades from 9→21→50 EMA as R grows.

    Martin Luk explicitly teaches upgrading the trail EMA to give winners
    more room once they prove themselves:
      - Default / < ML_EXIT_TRAIL_21_EMA_R  → trail 9 EMA  (tight)
      - ≥ ML_EXIT_TRAIL_21_EMA_R (5R)       → trail 21 EMA (medium)
      - ≥ ML_EXIT_TRAIL_50_EMA_R (10R)      → trail 50 EMA (wide, let winners run)

    HK timezone note: use compute_hk_timezone_stop() for live sessions.

    Args:
        df:            OHLCV DataFrame with EMA_9/EMA_21/EMA_50 columns
        entry_price:   Original entry price
        current_stop:  Existing stop (never lower)
        r_multiple:    Current floating R gain (used for EMA tier selection)

    Returns:
        dict with trail_stop, trail_signal, ema_value, trail_ema_period
    """
    # ── Select EMA tier based on R gain ──────────────────────────────────
    r_for_21 = getattr(C, "ML_EXIT_TRAIL_21_EMA_R", 5.0)
    r_for_50 = getattr(C, "ML_EXIT_TRAIL_50_EMA_R", 10.0)

    if r_multiple >= r_for_50:
        trail_ema_period = 50
        tier_label = "50 EMA (大贏追蹤)"
    elif r_multiple >= r_for_21:
        trail_ema_period = 21
        tier_label = "21 EMA (中期追蹤)"
    else:
        trail_ema_period = getattr(C, "ML_TRAIL_EMA", 9)
        tier_label = "9 EMA (標準追蹤)"

    ema_col = f"EMA_{trail_ema_period}"

    result = {
        "phase": 3,
        "trail_stop": current_stop,
        "trail_signal": "HOLD",
        "ema_value": None,
        "trail_ema_period": trail_ema_period,
        "tier_label_zh": tier_label,
        "description_zh": f"Day 3+ {tier_label} 追蹤止損",
    }

    if df.empty or len(df) < 5:
        result["description_zh"] = "數據不足"
        return result

    close = float(df["Close"].iloc[-1])

    # Find EMA value (compute if column absent)
    if ema_col in df.columns:
        ema_val = float(df[ema_col].iloc[-1])
    else:
        ema_val = float(df["Close"].ewm(span=trail_ema_period, adjust=False).mean().iloc[-1])

    result["ema_value"] = round(ema_val, 2)

    # HK timezone stop: add buffer to avoid HK off-hours whipsaw
    if getattr(C, "ML_HK_TIMEZONE_MODE", True):
        hk_stop = compute_hk_timezone_stop(ema_val, entry_price)
        trail_level = hk_stop["stop_price"]
    else:
        trail_level = ema_val

    # Trail stop = max(current_stop, ema-based level) — never lower the stop
    new_trail = max(current_stop, trail_level)
    result["trail_stop"] = round(new_trail, 2)

    # Signal: sell if close below EMA
    if close < ema_val:
        result["trail_signal"] = "SELL_ALL"
        result["description_zh"] = (
            f"⚠ 收盤 ${close:.2f} < {tier_label} ${ema_val:.2f} — 出場全部剩餘持倉"
        )
    else:
        pct_above = (close / ema_val - 1.0) * 100.0
        result["trail_signal"] = "HOLD"
        result["description_zh"] = (
            f"收盤 ${close:.2f} 高於 {tier_label} ${ema_val:.2f} (+{pct_above:.1f}%) — 繼續持有"
        )

    result["tooltip"] = (
        f"Martin Luk 自適應 EMA 追蹤止損\n"
        f"< {r_for_21:.0f}R → 9 EMA (緊)\n"
        f"≥ {r_for_21:.0f}R → 21 EMA (中)\n"
        f"≥ {r_for_50:.0f}R → 50 EMA (寬，讓贏家跑)\n"
        f"當前: {tier_label} (浮盈 {r_multiple:.1f}R)\n"
        "收盤跌穿選定 EMA → 出場剩餘全部持倉"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# HK Timezone Stop Helper
# ─────────────────────────────────────────────────────────────────────────────

def compute_hk_timezone_stop(ema_value: float,
                              entry_price: float,
                              buffer_pct: float = None) -> dict:
    """
    Compute a HK-timezone-aware stop level by adding a small buffer below the EMA.

    Martin Luk is based in Hong Kong. US market is open in HK overnight
    (9:30pm – 4:00am HKT). Having a stop exactly at the EMA risks being
    stopped out by minor pre-market noise. The buffer gives a small cushion.

    Args:
        ema_value:   Current EMA price level (9/21/50 depending on tier)
        entry_price: Original entry price (for R-based buffer scaling)
        buffer_pct:  Override buffer percentage below EMA (default from config)

    Returns:
        dict with stop_price, buffer_applied_pct, note_zh
    """
    if buffer_pct is None:
        buffer_pct = getattr(C, "ML_HK_EMA_STOP_BUFFER_PCT", 0.5)

    stop_price = ema_value * (1.0 - buffer_pct / 100.0)

    # Sanity: stop must always be below entry
    if entry_price > 0 and stop_price >= entry_price:
        stop_price = entry_price * 0.99  # Fallback: 1% below entry

    return {
        "stop_price": round(stop_price, 2),
        "ema_value": round(ema_value, 2),
        "buffer_applied_pct": buffer_pct,
        "note_zh": (
            f"HK 時區止損 = EMA ${ema_value:.2f} × (1 - {buffer_pct:.1f}%) "
            f"= ${stop_price:.2f}\n"
            "(美股在 HK 深夜開市，EMA 緩衝防止隔夜雜訊觸發)"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Determine current phase
# ─────────────────────────────────────────────────────────────────────────────

def get_current_phase(entry_date: str | date) -> int:
    """
    Determine which stop phase applies based on trading days since entry.
    Returns: 1 (Day 1), 2 (Day 2), or 3 (Day 3+)
    """
    if isinstance(entry_date, str):
        try:
            entry_dt = date.fromisoformat(entry_date)
        except ValueError:
            return 3
    else:
        entry_dt = entry_date

    today = date.today()
    cal_days = (today - entry_dt).days
    trading_days = max(0, int(cal_days * 5 / 7))

    if trading_days == 0:
        return 1
    elif trading_days == 1:
        return 2
    else:
        return 3


# ─────────────────────────────────────────────────────────────────────────────
# Partial Sell (R-multiple targets)
# ─────────────────────────────────────────────────────────────────────────────

def get_profit_action(entry_price: float,
                      current_price: float,
                      stop_price: float,
                      shares: int,
                      vol_ratio: float = 0.0,
                      is_parabolic: bool = False) -> dict:
    """
    Martin Luk R-multiple partial sell system with contextual exit overrides.

    Standard schedule:
      - At 3R: sell 15% of shares
      - At 5R: sell another 15% of shares
      - Remaining 70%: trail on adaptive EMA

    Contextual overrides (Chapter 14 — Selling into Strength):
      - Extreme volume surge (≥ ML_EXIT_EXTREME_VOL_MULT): SELL_ALL_EXTREME_VOL
        → Climactic action signal; never hold through a volume spike reversal
      - Parabolic flag: SELL_ALL_PARABOLIC
        → Price gone parabolic per Appendix E checklist; distribute immediately
      - 10R+ confirmed trend: switch to EMA trail ONLY (no more scheduled partials)
        → Let the winner run; let EMA trail manage the exit

    Args:
        entry_price:   Original entry price
        current_price: Current market price
        stop_price:    Initial stop price (for R calculation)
        shares:        Current shares held
        vol_ratio:     Today's volume / 20-day avg volume (for climax detection)
        is_parabolic:  Flag from compute_parabolic_checklist() Appendix E output

    Returns:
        dict with action, R level, shares_to_sell, exit_type
    """
    r1 = entry_price - stop_price if stop_price and entry_price > stop_price else 0
    r_mult = (current_price - entry_price) / r1 if r1 > 0 else 0
    gain_pct = (current_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0

    partial_1_r  = getattr(C, "ML_PARTIAL_SELL_1_R",  3.0)
    partial_1_pct = getattr(C, "ML_PARTIAL_SELL_1_PCT", 15.0)
    partial_2_r  = getattr(C, "ML_PARTIAL_SELL_2_R",  5.0)
    partial_2_pct = getattr(C, "ML_PARTIAL_SELL_2_PCT", 15.0)
    extreme_vol_mult = getattr(C, "ML_EXIT_EXTREME_VOL_MULT", 3.0)
    trail_only_r = getattr(C, "ML_EXIT_TRAIL_50_EMA_R", 10.0)

    action = "HOLD"
    shares_out = 0
    reason_zh = ""
    target_label = ""
    exit_type = "SCHEDULED"

    # ── Priority 1: Parabolic — full exit immediately ──────────────────────
    if is_parabolic and getattr(C, "ML_PARABOLIC_FULL_EXIT", True):
        action = "SELL_ALL_PARABOLIC"
        shares_out = shares
        exit_type = "PARABOLIC"
        reason_zh = (
            f"⚠ 拋物線啟動 ({r_mult:.1f}R, +{gain_pct:.1f}%) — "
            "附錄E核對清單觸發，立即出場全部"
        )
        target_label = "PARABOLIC"

    # ── Priority 2: Climactic volume — full exit on volume spike ──────────
    elif vol_ratio >= extreme_vol_mult:
        action = "SELL_ALL_EXTREME_VOL"
        shares_out = shares
        exit_type = "EXTREME_VOL"
        reason_zh = (
            f"⚠ 極端量 {vol_ratio:.1f}x 均量 (閾值 {extreme_vol_mult:.1f}x, {r_mult:.1f}R) — "
            "高潮量能出現，分批出場全部"
        )
        target_label = "EXTREME_VOL"

    # ── Priority 3: 10R+ confirmed trend — trail ONLY, no more partials ────
    elif r_mult >= trail_only_r:
        action = "TRAIL_ONLY"
        shares_out = 0
        exit_type = "TRAIL_ONLY"
        reason_zh = (
            f"大贏家 {r_mult:.1f}R (+{gain_pct:.1f}%) ≥ {trail_only_r:.0f}R — "
            "切換 50 EMA 追蹤，不再主動了結，讓贏家繼續跑"
        )
        target_label = f"≥{trail_only_r:.0f}R TRAIL"

    # ── Standard partial sell schedule ────────────────────────────────────
    elif r_mult >= partial_2_r:
        action = "TAKE_PARTIAL_5R"
        shares_out = max(1, int(shares * partial_2_pct / 100.0))
        reason_zh = (
            f"達到 {partial_2_r:.0f}R ({r_mult:.1f}R, +{gain_pct:.1f}%) — "
            f"出 {partial_2_pct:.0f}% ({shares_out} 股)"
        )
        target_label = f"{partial_2_r:.0f}R"
    elif r_mult >= partial_1_r:
        action = "TAKE_PARTIAL_3R"
        shares_out = max(1, int(shares * partial_1_pct / 100.0))
        reason_zh = (
            f"達到 {partial_1_r:.0f}R ({r_mult:.1f}R, +{gain_pct:.1f}%) — "
            f"出 {partial_1_pct:.0f}% ({shares_out} 股)"
        )
        target_label = f"{partial_1_r:.0f}R"
    elif gain_pct > 0:
        action = "HOLD"
        reason_zh = f"獲利 +{gain_pct:.1f}% ({r_mult:.1f}R) — 未達 {partial_1_r:.0f}R 目標，繼續持有"
    else:
        action = "HOLD"
        reason_zh = f"浮虧 {gain_pct:.1f}% — 維持止損管理"

    return {
        "action": action,
        "r_multiple": round(r_mult, 2),
        "gain_pct": round(gain_pct, 2),
        "shares_to_sell": shares_out,
        "shares_remaining": shares - shares_out,
        "target_label": target_label,
        "exit_type": exit_type,
        "reason_zh": reason_zh,
        "tooltip": (
            "Martin Luk 部分了結規則 (情境式退場)\n"
            f"拋物線 → 立即出場 100%\n"
            f"極端成交量 (≥{extreme_vol_mult:.1f}x) → 出場 100%\n"
            f"≥{trail_only_r:.0f}R → 純追蹤，不再了結\n"
            f"標準: {partial_1_r:.0f}R → -{partial_1_pct:.0f}%, "
            f"{partial_2_r:.0f}R → -{partial_2_pct:.0f}%, 其餘 EMA 追蹤"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formula-based position sizing
# ─────────────────────────────────────────────────────────────────────────────

def calc_ml_position_size(entry_price: float,
                          stop_price: float,
                          account_size: float = None,
                          risk_pct: float = None,
                          consecutive_losses: int = 0) -> dict:
    """
    Martin Luk formula-based position sizing:
      Shares = (Account × Risk%) / (Entry − Stop)

    With consecutive loss protection:
      3 losses → halve size
      5 losses → stop trading

    Args:
        entry_price:        Planned entry price
        stop_price:         Planned stop price (Day 1 LOD-based)
        account_size:       Account equity (default: C.ACCOUNT_SIZE)
        risk_pct:           Risk % per trade (default: ML_RISK_PER_TRADE_PCT)
        consecutive_losses: Current consecutive loss streak

    Returns:
        dict with 'shares', 'position_value', 'risk_dollars', 'formula'
    """
    if account_size is None:
        account_size = getattr(C, "ACCOUNT_SIZE", 100_000)
    if risk_pct is None:
        risk_pct = getattr(C, "ML_RISK_PER_TRADE_PCT", 0.50)

    max_stop_pct = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
    max_pos_pct = getattr(C, "ML_MAX_SINGLE_POSITION_PCT", 25.0)
    loss_halve = getattr(C, "ML_CONSECUTIVE_LOSS_HALVE", 3)
    loss_stop = getattr(C, "ML_CONSECUTIVE_LOSS_STOP", 5)

    # Gate: stop trading on streak
    if consecutive_losses >= loss_stop:
        return {
            "shares": 0, "position_value": 0, "risk_dollars": 0,
            "action": "STOP_TRADING",
            "reason_zh": f"連續虧損 {consecutive_losses} 次 ≥ {loss_stop} → 暫停交易一週",
            "formula": "N/A — 暫停交易",
        }

    # Risk adjustment for consecutive losses
    effective_risk = risk_pct
    if consecutive_losses >= loss_halve:
        effective_risk = risk_pct / 2.0
        logger.info("[ML Size] 連續虧損 %d → 風險減半至 %.2f%%", consecutive_losses, effective_risk)

    # Stop distance
    stop_distance = entry_price - stop_price if entry_price > stop_price else 0
    stop_pct = (stop_distance / entry_price * 100.0) if entry_price > 0 else 99.0

    if stop_pct > max_stop_pct:
        return {
            "shares": 0, "position_value": 0, "risk_dollars": 0,
            "action": "PASS",
            "reason_zh": f"止損距離 {stop_pct:.1f}% > {max_stop_pct}% 上限 — 放棄",
            "formula": f"止損 {stop_pct:.1f}% 超出上限",
        }

    if stop_distance <= 0:
        return {
            "shares": 0, "position_value": 0, "risk_dollars": 0,
            "action": "PASS",
            "reason_zh": "止損距離 ≤ 0 — 無效",
            "formula": "N/A — 止損距離無效",
        }

    # Core formula: Shares = (Account × Risk%) / Stop Distance
    risk_dollars = account_size * (effective_risk / 100.0)
    shares = int(risk_dollars / stop_distance)

    # Cap to max position %
    max_shares_by_cap = int(account_size * max_pos_pct / 100.0 / entry_price) if entry_price > 0 else 0
    shares = min(shares, max_shares_by_cap)

    pos_value = shares * entry_price
    actual_risk = shares * stop_distance

    return {
        "shares": shares,
        "position_value": round(pos_value, 2),
        "position_pct": round(pos_value / account_size * 100.0, 1) if account_size > 0 else 0,
        "risk_dollars": round(actual_risk, 2),
        "risk_pct_account": round(actual_risk / account_size * 100.0, 2) if account_size > 0 else 0,
        "stop_pct": round(stop_pct, 2),
        "effective_risk_pct": round(effective_risk, 2),
        "consecutive_losses": consecutive_losses,
        "action": "BUY",
        "formula": (
            f"倉位 = ({account_size:,.0f} × {effective_risk:.2f}%) / "
            f"({entry_price:.2f} − {stop_price:.2f}) = {shares} 股"
        ),
        "reason_zh": (
            f"{shares} 股 × ${entry_price:.2f} = ${pos_value:,.0f} "
            f"(佔帳戶 {pos_value / account_size * 100:.1f}%); "
            f"風險 ${actual_risk:.0f} ({actual_risk / account_size * 100:.2f}%)"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Unified position health check
# ─────────────────────────────────────────────────────────────────────────────

def check_ml_position(
    ticker: str,
    entry_price: float,
    current_stop: float,
    entry_date: str | date,
    shares: int,
    initial_stop: float = None,
    star_rating: float = 3.0,
    df: pd.DataFrame = None,
) -> dict:
    """
    Unified Martin Luk position health check.
    Evaluates current price vs all 3 phases and R-multiple targets.

    Args:
        ticker:        Stock ticker
        entry_price:   Trade entry price
        current_stop:  Current stop level (updated over time)
        entry_date:    Date of trade entry
        shares:        Current shares held
        initial_stop:  Original Day 1 stop (for R calc)
        star_rating:   Setup star rating (not used for sell sizing in ML)
        df:            OHLCV DataFrame (fetched if None)

    Returns:
        Comprehensive health dict with recommendation and stop update info
    """
    from modules.data_pipeline import get_historical, get_technicals

    if df is None or df.empty:
        df = get_historical(ticker, period="6mo")
        if not df.empty:
            df = get_technicals(df)

    if df.empty:
        return {"ticker": ticker, "error": "No price data", "action": "HOLD"}

    close = float(df["Close"].iloc[-1])
    low = float(df["Low"].iloc[-1])
    phase = get_current_phase(entry_date)
    gain_pct = (close / entry_price - 1.0) * 100.0 if entry_price > 0 else 0

    # Determine initial stop for R calc
    stop_for_r = initial_stop if initial_stop else current_stop

    # ── Compute stops by phase ─────────────────────────────────────────────
    day1_info = get_day1_stop(entry_price, low)
    day2_info = get_day2_stop(entry_price, close)
    day3_info = get_day3_trail_stop(df, entry_price, current_stop)
    profit = get_profit_action(entry_price, close, stop_for_r, shares)

    # ── Resolve primary action ─────────────────────────────────────────────
    primary_action = "HOLD"
    stop_triggered = False
    stop_level = current_stop

    if phase == 1:
        stop_level = day1_info["stop_price"]
        if close <= stop_level:
            primary_action = "STOP_HIT"
            stop_triggered = True
    elif phase == 2:
        stop_level = max(current_stop, entry_price)
        if close <= entry_price:
            primary_action = "STOP_HIT"
            stop_triggered = True
        elif day2_info["action"] == "MOVE_TO_BREAKEVEN":
            primary_action = "UPDATE_STOP"
    else:
        # Day 3+: 9 EMA trail
        stop_level = day3_info.get("trail_stop", current_stop)
        if day3_info.get("trail_signal") == "SELL_ALL":
            primary_action = "SELL_ALL"
            stop_triggered = True
        elif profit.get("action") in ("TAKE_PARTIAL_3R", "TAKE_PARTIAL_5R"):
            primary_action = profit["action"]

    # ── Build signals ──────────────────────────────────────────────────────
    signals = []

    if stop_triggered:
        signals.append({
            "type": "STOP",
            "severity": "critical",
            "msg_zh": f"止損觸發 (Phase {phase}) — 建議立即出場",
            "msg_en": f"Stop triggered (Phase {phase}) — exit immediately",
        })

    if profit.get("action") in ("TAKE_PARTIAL_3R", "TAKE_PARTIAL_5R"):
        signals.append({
            "type": "PROFIT_TARGET",
            "severity": "info",
            "msg_zh": profit["reason_zh"],
            "msg_en": f"R-target hit ({profit['r_multiple']:.1f}R)",
        })

    if gain_pct >= 20 and not stop_triggered:
        signals.append({
            "type": "BIG_WINNER",
            "severity": "info",
            "msg_zh": f"大贏家 +{gain_pct:.1f}% — 保護利潤，緊跟 9 EMA",
            "msg_en": f"Big winner +{gain_pct:.1f}% — protect profits",
        })

    # ── EMA alignment check ───────────────────────────────────────────────
    trail_ema = getattr(C, "ML_TRAIL_EMA", 9)
    ema_col = f"EMA_{trail_ema}"
    if ema_col in df.columns:
        ema_val = float(df[ema_col].iloc[-1])
        pct_above_ema = (close / ema_val - 1.0) * 100.0 if ema_val > 0 else 0
        if pct_above_ema > 8:
            signals.append({
                "type": "EXTENDED",
                "severity": "warning",
                "msg_zh": f"高於 {trail_ema} EMA {pct_above_ema:.1f}% — 可能需要整理",
                "msg_en": f"{pct_above_ema:.1f}% above {trail_ema} EMA — may consolidate",
            })

    return {
        "ticker": ticker,
        "current_phase": phase,
        "close": round(close, 2),
        "entry_price": round(entry_price, 2),
        "gain_pct": round(gain_pct, 2),
        "r_multiple": profit.get("r_multiple", 0),
        "primary_action": primary_action,
        "stop_triggered": stop_triggered,
        "recommended_stop": round(stop_level, 2),
        "day1_stop": day1_info,
        "day2_stop": day2_info,
        "day3_trail": day3_info,
        "profit_action": profit,
        "signals": signals,
        "scan_date": date.today().isoformat(),
    }
