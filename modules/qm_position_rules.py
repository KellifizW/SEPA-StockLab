"""
modules/qm_position_rules.py  —  Qullamaggie 3-Phase Stop & Profit Management
═══════════════════════════════════════════════════════════════════════════════
Implements the position management rules from Sections 8, 9, and 10 of
QullamaggieStockguide.md.

3-Phase Stop System:
  Phase 1 (Day 1):    Stop = day's Low-of-Day (LOD) minus a small buffer
  Phase 2 (Day 2):    Move stop to break-even (entry price)
  Phase 3 (Day 3+):   Trail using 10SMA as a soft stop (close-based)

2-Step Profit Taking:
  Step 1 (Day 3-5):   Sell 25-50% depending on star rating
  Step 2 (Day 5+):    Trail remainder on 10SMA; sell all when price closes below

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
# Phase 1: Day 1 initial stop (LOD-based)
# ─────────────────────────────────────────────────────────────────────────────

def get_day1_stop(entry_price: float,
                  day_low: float,
                  buffer_pct: float = None) -> dict:
    """
    Calculate the Day 1 initial stop loss for a Qullamaggie breakout entry.
    Day 1 stop = today's Low-of-Day (LOD) minus a small buffer.

    Args:
        entry_price: The price at which the trade was entered
        day_low:     The intraday low on the entry day (LOD)
        buffer_pct:  Buffer below LOD (default: QM_DAY1_STOP_BELOW_LOD_PCT = 0.5%)

    Returns:
        dict with 'stop_price', 'risk_pct', 'risk_amount_per_share'
    """
    if buffer_pct is None:
        buffer_pct = getattr(C, "QM_DAY1_STOP_BELOW_LOD_PCT", 0.5)

    stop_price = day_low * (1.0 - buffer_pct / 100.0)
    risk_pct   = (entry_price - stop_price) / entry_price * 100.0 if entry_price > 0 else 0
    risk_ps    = entry_price - stop_price

    return {
        "stop_price":            round(stop_price, 2),
        "risk_pct":              round(risk_pct, 2),
        "risk_per_share":        round(risk_ps, 2),
        "phase":                 1,
        "description_zh":        f"Day 1 止損：當日最低 ${day_low:.2f} 下方 {buffer_pct}%",
        "description_en":        f"Day 1 stop: LOD ${day_low:.2f} minus {buffer_pct}% buffer",
        "tooltip":               (
            "Day 1 止損 — 最壞情況止損\n"
            "= 進場當天的最低點（LOD）下方一點緩衝\n"
            "若股價跌穿此位 → 全部賣出，不猶豫\n"
            "'You should always be using a stop loss, especially\n"
            " as a beginner and especially with these volatile stocks.'"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Day 2 break-even stop
# ─────────────────────────────────────────────────────────────────────────────

def get_day2_stop(entry_price: float,
                  current_price: float) -> dict:
    """
    Day 2 stop: move to break-even (entry price) if still profitable.

    QM rule: "If you wish — move stop to break-even on Day 2.
              From this point, worst case = scratch trade ('free trade')."

    Args:
        entry_price:   Original entry price
        current_price: Current market price

    Returns:
        dict with 'stop_price', 'is_profitable', 'action'
    """
    is_profitable = current_price > entry_price
    action = "MOVE_TO_BREAKEVEN" if is_profitable else "HOLD_DAY1_STOP"

    return {
        "stop_price":     round(entry_price, 2),
        "is_profitable":  is_profitable,
        "action":         action,
        "phase":          2,
        "description_zh": (
            f"Day 2 止損：{'移至成本價' if is_profitable else '維持Day1止損'} "
            f"(成本 ${entry_price:.2f})"
        ),
        "description_en": (
            f"Day 2 stop: {'move to break-even' if is_profitable else 'maintain Day 1 stop'}"
        ),
        "tooltip": (
            "Day 2 止損 — Break-Even 保護\n"
            "若價格仍在成本以上 → 將止損移至您的買入價\n"
            "從此最壞情況 = 打平（'Free Trade'）\n"
            "注意：偶爾止損太緊可能被正常波動洗出\n"
            "初學者建議執行，有經驗者可酌情保留略低的止損"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Day 3+ trailing 10SMA soft stop
# ─────────────────────────────────────────────────────────────────────────────

def get_day3_trail_stop(df: pd.DataFrame,
                        entry_price: float,
                        current_stop: float) -> dict:
    """
    Day 3+ trailing stop using the 10SMA as a soft stop.
    "Soft stop" = triggered on CLOSE price, not intraday.

    Rules:
      → Continue holding while Close > 10SMA
      → SELL all remaining position when Close ≤ 10SMA
      → Hard stop fallback: 10SMA minus small buffer (for when you can't monitor)

    Args:
        df:            OHLCV DataFrame (from get_historical)
        entry_price:   Original trade entry price
        current_stop:  Current stop level (should be ≥ entry_price at this phase)

    Returns:
        dict with 'soft_stop', 'hard_stop', 'trail_signal', 'unrealised_pct'
    """
    trail_period = getattr(C, "QM_TRAIL_MA_PERIOD", 10)
    buffer_pct   = getattr(C, "QM_DAY1_STOP_BELOW_LOD_PCT", 0.5)

    if df.empty or len(df) < trail_period:
        return {
            "soft_stop": current_stop,
            "hard_stop": current_stop,
            "trail_signal": "HOLD",
            "sma_10": None,
            "phase": 3,
        }

    # Compute 10SMA
    sma10_col = f"SMA_{trail_period}"
    if sma10_col in df.columns:
        sma10 = float(df[sma10_col].iloc[-1])
    else:
        sma10 = float(df["Close"].rolling(trail_period).mean().iloc[-1])

    close = float(df["Close"].iloc[-1])
    hard_stop  = round(sma10 * (1.0 - buffer_pct / 100.0), 2)

    # Trail signal
    below_sma10 = close <= sma10
    trail_signal = "SELL_ALL" if below_sma10 else "HOLD"

    unrealised_pct = (close / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0

    # The effective stop is the higher of: current_stop or break-even
    effective_stop = max(current_stop, entry_price)

    return {
        "soft_stop":       round(sma10, 2),
        "hard_stop":       hard_stop,
        "effective_stop":  round(effective_stop, 2),
        "trail_signal":    trail_signal,
        "close_vs_sma10":  round(close - sma10, 2),
        "close_below_sma": below_sma10,
        "sma_10":          round(sma10, 2),
        "unrealised_pct":  round(unrealised_pct, 2),
        "phase":           3,
        "description_zh":  (
            f"Day 3+ 追蹤止損 (10SMA soft stop):\n"
            f"  軟止損：收盤跌破 10SMA ({sma10:.2f}) → 全出\n"
            f"  硬止損（無法盯盤）：${hard_stop:.2f}"
        ),
        "description_en": (
            f"Day 3+ trailing: Close below 10SMA ({sma10:.2f}) = SELL ALL | "
            f"Hard stop: ${hard_stop:.2f}"
        ),
        "tooltip": (
            "Day 3+ 追蹤止損 — 10SMA Soft Stop\n"
            "Soft Stop 看收盤價，不是盤中價\n"
            "盤中暫時跌穿10SMA又收回 → 不操作\n"
            "收盤低於10SMA → 賣出全部剩餘持倉\n"
            "無法盯盤時：可設硬止損在10SMA下方（可能被波動誤觸）\n"
            "10SMA隨股價上漲而上移 → 保護越來越多的利潤"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Detect current phase
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
            return 3  # Unknown → assume Day 3+
    else:
        entry_dt = entry_date

    today = date.today()
    # Approximate trading days (rough: 5/7 of calendar days)
    cal_days = (today - entry_dt).days
    trading_days = max(0, int(cal_days * 5 / 7))

    if trading_days == 0:
        return 1
    elif trading_days == 1:
        return 2
    else:
        return 3


# ─────────────────────────────────────────────────────────────────────────────
# Profit-taking rules
# ─────────────────────────────────────────────────────────────────────────────

def get_profit_action(entry_price: float,
                      current_price: float,
                      entry_date: str | date,
                      shares: int,
                      star_rating: float = 4.0) -> dict:
    """
    Determine what profit-taking action to take based on QM 2-step system.

    2-Step Profit Taking (Section 9):
      Step 1 (Day 3-5): Sell 25-50% depending on star rating
      Step 2 (Day 5+):  Trail remainder on 10SMA until trend ends

    Args:
        entry_price:  Original entry price
        current_price: Current market price
        entry_date:   Trade entry date
        shares:       Total shares held
        star_rating:  Setup star rating (affects how much to sell in Step 1)

    Returns:
        dict with 'action', 'shares_to_sell', 'reason_zh', 'reason_en'
    """
    phase     = get_current_phase(entry_date)
    gain_pct  = (current_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0

    # Determine Step 1 sell % by star rating
    if star_rating >= 5.0:
        step1_sell_pct = getattr(C, "QM_PROFIT_TAKE_5STAR_1ST", 25.0)   # 5★ only sell 25%
        step1_gain_trig = getattr(C, "QM_PROFIT_TAKE_5STAR_GAIN", 20.0)
    elif star_rating >= 4.0:
        step1_sell_pct = 33.0   # 4★ → sell ~33%
        step1_gain_trig = getattr(C, "QM_PROFIT_TAKE_1ST_GAIN", 10.0)
    else:
        step1_sell_pct = 50.0   # 3★ or less → aggressive 50%
        step1_gain_trig = getattr(C, "QM_PROFIT_TAKE_1ST_GAIN", 10.0)

    take_day_min = getattr(C, "QM_PROFIT_TAKE_DAY_MIN", 3)
    take_day_max = getattr(C, "QM_PROFIT_TAKE_DAY_MAX", 5)

    # Determine action
    action   = "HOLD"
    reason   = ""
    shares_out = 0

    # Phase 1-2: too early for profit taking
    if phase < take_day_min:
        action  = "HOLD"
        reason  = f"Day {phase} — 等待 Day {take_day_min}-{take_day_max} 再開始獲利"

    # Step 1: Day 3-5 or gain threshold reached
    elif phase <= take_day_max or gain_pct >= step1_gain_trig:
        if gain_pct > 0:
            action     = "TAKE_PARTIAL_PROFIT"
            shares_out = max(1, int(shares * step1_sell_pct / 100))
            reason     = (
                f"{'Day ' + str(phase) if phase <= take_day_max else '獲利達'}"
                f"{gain_pct:.1f}%({step1_gain_trig:.0f}%觸發) → "
                f"出售 {step1_sell_pct:.0f}% 持倉 ({shares_out} 股)"
            )
        else:
            action = "HOLD"
            reason = "Day 3-5 但仍未獲利 — 繼續觀察，等10SMA止損管理"

    # Step 2: Beyond Day 5 — trail on 10SMA (handled externally through day3 stop)
    else:
        action  = "TRAIL_10SMA"
        reason  = "Step 2：剩餘持倉跟隨 10SMA 追蹤止損，直到收盤跌穿10SMA"

    return {
        "action":          action,
        "phase":           phase,
        "gain_pct":        round(gain_pct, 2),
        "shares_to_sell":  shares_out,
        "shares_remaining": shares - shares_out,
        "step1_sell_pct":  step1_sell_pct,
        "reason_zh":       reason,
        "reason_en":       reason,
        "tooltip": (
            "獲利了結規則 (Profit Taking Rules)\n"
            "Step 1: Day 3-5 先出 25-50% 持倉（鎖定利潤）\n"
            "  5★ → 只出25%（更有耐心）\n"
            "  4★ → 出33%（標準）\n"
            "  3★ → 出50%（積極了結）\n"
            "Step 2: 剩餘持倉用10SMA追蹤 → 跌穿全出\n"
            "已出倉部分 = 免費的（止損在成本以上）\n"
            "讓剩餘部分跑完整個趨勢"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Position health check (unified stop + profit assessment)
# ─────────────────────────────────────────────────────────────────────────────

def check_qm_position(
    ticker: str,
    entry_price: float,
    current_stop: float,
    entry_date: str | date,
    shares: int,
    star_rating: float = 4.0,
    df: pd.DataFrame = None,
) -> dict:
    """
    Unified QM position health check.
    Evaluates current price vs all 3 stop phases and recommends action.

    Args:
        ticker:        Stock ticker
        entry_price:   Trade entry price
        current_stop:  Current stop level (updated over time)
        entry_date:    Date of trade entry
        shares:        Current shares held
        star_rating:   Original setup star rating
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
    phase = get_current_phase(entry_date)
    gain_pct = (close / entry_price - 1.0) * 100.0 if entry_price > 0 else 0

    # ── Compute stops by phase ─────────────────────────────────────────────
    day1_info  = get_day1_stop(entry_price, float(df["Low"].iloc[-1]))
    day2_info  = get_day2_stop(entry_price, close)
    day3_info  = get_day3_trail_stop(df, entry_price, current_stop)
    profit     = get_profit_action(entry_price, close, entry_date, shares, star_rating)

    # ── Resolve primary action ─────────────────────────────────────────────
    primary_action = "HOLD"
    stop_triggered = False
    stop_level     = current_stop

    if phase == 1:
        stop_level = day1_info["stop_price"]
        if close <= stop_level:
            primary_action = "STOP_HIT"
            stop_triggered = True
    elif phase == 2:
        # Update stop to at least break-even
        stop_level = max(current_stop, entry_price)
        if close <= entry_price:
            primary_action = "STOP_HIT"
            stop_triggered = True
        elif day2_info["action"] == "MOVE_TO_BREAKEVEN":
            primary_action = "UPDATE_STOP"
    else:
        # Day 3+: check 10SMA soft stop
        stop_level = day3_info.get("effective_stop", current_stop)
        if day3_info.get("trail_signal") == "SELL_ALL":
            primary_action = "SELL_ALL"
            stop_triggered = True
        elif profit.get("action") in ("TAKE_PARTIAL_PROFIT",):
            primary_action = "TAKE_PARTIAL_PROFIT"

    # ── Build signals list ─────────────────────────────────────────────────
    signals = []
    if stop_triggered:
        signals.append({
            "type": "STOP",
            "severity": "critical",
            "msg_zh": f"止損觸發 (Phase {phase}) — 建議立即出場",
            "msg_en": f"Stop triggered (Phase {phase}) — exit immediately",
        })
    if gain_pct >= 10 and not stop_triggered:
        signals.append({
            "type": "PROFIT",
            "severity": "info",
            "msg_zh": f"獲利 {gain_pct:.1f}% — 考慮鎖定部分利潤",
            "msg_en": f"Up {gain_pct:.1f}% — consider locking partial profits",
        })

    return {
        "ticker":           ticker,
        "current_phase":    phase,
        "close":            round(close, 2),
        "entry_price":      round(entry_price, 2),
        "gain_pct":         round(gain_pct, 2),
        "primary_action":   primary_action,
        "stop_triggered":   stop_triggered,
        "recommended_stop": round(stop_level, 2),
        "day1_stop":        day1_info,
        "day2_stop":        day2_info,
        "day3_trail":       day3_info,
        "profit_action":    profit,
        "signals":          signals,
        "scan_date":        date.today().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Calculate optimal position size from star rating + account
# ─────────────────────────────────────────────────────────────────────────────

def calc_qm_position_size(
    star_rating: float,
    entry_price: float,
    stop_price:  float,
    account_size: float = None,
) -> dict:
    """
    Calculate QM position size based on star rating and risk parameters.

    Two methods:
      1. Star-rating based % of account (primary)
      2. Fixed risk per trade cross-check (from trader_config.py)

    Args:
        star_rating:  Setup star rating (0 to 5.5)
        entry_price:  Planned entry price
        stop_price:   Planned initial stop price (LOD-based)
        account_size: Total account equity (default: C.ACCOUNT_SIZE)

    Returns:
        dict with 'shares', 'position_value', 'risk_dollar', 'risk_pct_acct'
    """
    if account_size is None:
        account_size = getattr(C, "ACCOUNT_SIZE", 100_000)

    sizing = getattr(C, "QM_POSITION_SIZING", {
        "5+": (20.0, 25.0), "5": (15.0, 25.0),
        "4":  (10.0, 15.0), "3": (5.0,  10.0), "0": (0.0, 0.0),
    })

    # Determine % allocation
    if star_rating >= 5.5:
        lo, hi = sizing.get("5+", (20, 25))
    elif star_rating >= 5.0:
        lo, hi = sizing.get("5",  (15, 25))
    elif star_rating >= 4.0:
        lo, hi = sizing.get("4",  (10, 15))
    elif star_rating >= 3.0:
        lo, hi = sizing.get("3",  (5,  10))
    else:
        return {
            "shares": 0, "position_value": 0, "risk_dollar": 0,
            "risk_pct_acct": 0, "action": "PASS",
            "reason": f"Star={star_rating:.1f} < 3.0 — 不交易",
        }

    # Mid-point allocation
    alloc_pct  = (lo + hi) / 2
    pos_value  = account_size * alloc_pct / 100

    # Risk calculation
    risk_ps    = entry_price - stop_price if stop_price and entry_price else 0
    risk_pct   = risk_ps / entry_price * 100 if entry_price > 0 else 0

    if entry_price > 0:
        shares = int(pos_value / entry_price)
        # Cross-check with max risk per trade
        max_risk_pct = getattr(C, "MAX_RISK_PER_TRADE_PCT", 1.5)
        max_risk_dol = account_size * max_risk_pct / 100
        if risk_ps > 0:
            max_shares_by_risk = int(max_risk_dol / risk_ps)
            # Use the more conservative estimate
            shares = min(shares, max_shares_by_risk)
        actual_pos_value = shares * entry_price
    else:
        shares = 0
        actual_pos_value = 0

    risk_dollar = shares * risk_ps if shares > 0 else 0

    return {
        "shares":                 shares,
        "position_value":         round(actual_pos_value, 2),
        "position_pct_min":       lo,
        "position_pct_max":       hi,
        "risk_dollar":            round(risk_dollar, 2),
        "risk_pct_acct":          round(risk_dollar / account_size * 100, 2) if account_size > 0 else 0,
        "stop_pct_from_entry":    round(risk_pct, 2),
        "star_rating":            star_rating,
        "action":                 "BUY",
    }
