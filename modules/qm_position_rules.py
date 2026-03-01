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
                  buffer_pct: float = None,
                  atr: float = None) -> dict:
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

    # ── Supplement 1 + 31: ATR stop-distance validation ───────────────────
    # Rule: "The stop shouldn't be higher than the average true range."
    # Ideal: stop distance ≈ 0.5 × ATR ("usually around half of ATR")
    atr_check = {}
    if atr is not None and atr > 0:
        stop_dist = entry_price - stop_price
        atr_ratio = stop_dist / atr
        is_within = atr_ratio <= getattr(C, "QM_ATR_STOP_MAX_MULT", 1.0)
        is_ideal  = atr_ratio <= getattr(C, "QM_ATR_STOP_IDEAL_MULT", 0.5)
        atr_check = {
            "atr":                round(atr, 4),
            "stop_distance":      round(stop_dist, 4),
            "stop_vs_atr_ratio":  round(atr_ratio, 2),
            "is_within_atr":      is_within,
            "is_ideal_stop":      is_ideal,
            "warning_zh":         (
                "" if is_ideal
                else ("止損距離超過ATR上限 — 進場太晚" if not is_within
                      else "止損距離超過0.5×ATR — 略高於理想值")
            ),
        }

    result = {
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
    if atr_check:
        result["atr_check"] = atr_check
    return result


# ─────────────────────────────────────────────────────────────────────────────
# ATR Entry Gate  (Supplement 1 + 31)
# ─────────────────────────────────────────────────────────────────────────────

def check_atr_entry_gate(current_price: float,
                         day_open: float,
                         day_low: float,
                         atr: float) -> dict:
    """
    Check whether the current intraday price still allows a valid ATR entry.

    Qullamaggie supplement rules (S1 + S31):
      "I usually don't buy the stock if it's up more on the day than its ATR."
      "Preferably you want to get in when the stock is only up a third or half
       or two-thirds of its average true range."
      "Low of the day is 172. ATR is 16. So your entry was no higher than 188
       should have been your entry, because the stop shouldn't be higher than ATR."

    Key outputs:
      max_entry_price  = day_low + ATR  (absolute ceiling — LOD + 1 ATR)
      ideal_entry_max  = day_open + ATR × 0.67  (gain up to 2/3 ATR)
      early_entry_max  = day_open + ATR × 0.33  (gain ≤ 1/3 ATR = very good)

    Args:
        current_price: current market price (or intended entry price)
        day_open:      today's open price
        day_low:       today's intraday low (LOD)
        atr:           14-day ATR in dollars (from get_atr())

    Returns:
        dict with 'status', 'max_entry_price', 'ideal_entry_max',
        'gain_vs_atr_ratio', 'warning_zh', 'warning_en'
    """
    if atr <= 0 or day_open <= 0:
        return {
            "status":          "UNKNOWN",
            "max_entry_price": None,
            "ideal_entry_max": None,
            "early_entry_max": None,
            "intraday_gain":   None,
            "gain_vs_atr_ratio": None,
            "warning_zh":      "無法計算 ATR 進場閘門（數據不足）",
            "warning_en":      "Cannot compute ATR entry gate (insufficient data)",
        }

    max_entry_mult  = getattr(C, "QM_ATR_ENTRY_MAX_MULT", 1.0)
    ideal_max_mult  = getattr(C, "QM_ATR_ENTRY_IDEAL_MAX_MULT", 0.67)
    early_mult      = getattr(C, "QM_ATR_ENTRY_EARLY_MULT", 0.33)

    intraday_gain   = current_price - day_open
    gain_ratio      = intraday_gain / atr if atr > 0 else 0.0

    # LOD-based ceiling: entry price must allow stop distance ≤ ATR
    max_entry_lod   = round(day_low + atr * max_entry_mult, 2)
    ideal_entry_max = round(day_open + atr * ideal_max_mult, 2)
    early_entry_max = round(day_open + atr * early_mult, 2)

    # Use the more restrictive of the two ceilings
    effective_max = min(max_entry_lod, day_open + atr * max_entry_mult)

    if current_price > max_entry_lod:
        status = "TOO_LATE"
        warning_zh = (f"進場太晚！股價 ${current_price:.2f} 已超過ATR上限 ${max_entry_lod:.2f} "
                      f"(日低 ${day_low:.2f} + ATR ${atr:.2f}) — 止損距離超限")
        warning_en = (f"Price ${current_price:.2f} exceeds ATR ceiling ${max_entry_lod:.2f} "
                      f"(LOD ${day_low:.2f} + ATR ${atr:.2f}) — stop > ATR, skip")
    elif gain_ratio > ideal_max_mult:
        status = "ACCEPTABLE"
        warning_zh = (f"進場偏晚 — 日內漲幅 {gain_ratio:.1%} × ATR（理想 ≤ {ideal_max_mult:.0%}）"
                      f"\n最高可接受進場價 ${max_entry_lod:.2f}")
        warning_en = (f"Entry late — intraday gain is {gain_ratio:.1%} of ATR "
                      f"(ideal ≤ {ideal_max_mult:.0%}) | max acceptable: ${max_entry_lod:.2f}")
    elif gain_ratio > early_mult:
        status = "IDEAL"
        warning_zh = f"理想進場 — 日內漲幅 {gain_ratio:.1%} × ATR（在 1/3–2/3 ATR 黃金區間）"
        warning_en = f"Ideal entry — intraday gain is {gain_ratio:.1%} of ATR (1/3-2/3 ATR window)"
    else:
        status = "EARLY"
        warning_zh = f"極佳進場 — 日內漲幅僅 {gain_ratio:.1%} × ATR（≤ 1/3 ATR，非常早）"
        warning_en = f"Early entry — intraday gain is only {gain_ratio:.1%} of ATR (very good)"

    return {
        "status":            status,
        "max_entry_price":   max_entry_lod,
        "ideal_entry_max":   ideal_entry_max,
        "early_entry_max":   early_entry_max,
        "intraday_gain":     round(intraday_gain, 4),
        "gain_vs_atr_ratio": round(gain_ratio, 3),
        "atr":               round(atr, 4),
        "day_low":           round(day_low, 2),
        "day_open":          round(day_open, 2),
        "warning_zh":        warning_zh,
        "warning_en":        warning_en,
        "tooltip":           (
            "ATR 進場閘門 (Supplement 1 + 31)\n"
            f"ATR = ${atr:.2f} (14日日內振幅均值)\n"
            f"最高進場上限 = 日低 + ATR = ${max_entry_lod:.2f}\n"
            f"理想進場上限 = 開盤 + 2/3 ATR = ${ideal_entry_max:.2f}\n"
            "超過ATR上限 → 止損距離超過ATR → 不進場\n"
            "\"I usually don't buy the stock if it's up more on"
            " the day than its average true range.\""
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Extended Stock Detection  (Supplement 4 + 33)
# ─────────────────────────────────────────────────────────────────────────────

def check_extended_stock(df: pd.DataFrame, sma_period: int = 10) -> dict:
    """
    Detect if a stock is over-extended from its moving average.

    Qullamaggie supplement rules (S4 + S33):
      "When this thing was like 60% above the 10-day moving average,
       I didn't even want to trail it on the 10 day. I just sold it."
      Selling when extremely extended = protecting unrealised profits before
      they evaporate.  'Extended stocks do get more extended, but the risk
      of giving back too much is high.'

    Statuses:
      NORMAL   — price within 40% of sma_period MA
      EXTENDED — price >40% above → consider trimming (don't blindly trail)
      EXTREME  — price >60% above → Qullamaggie would likely sell outright

    Args:
        df:          OHLCV DataFrame (with SMA columns if available)
        sma_period:  MA to compare against (default 10 for QM system)

    Returns:
        dict with 'status', 'pct_above_sma', 'action', 'warning_zh'
    """
    extended_pct = getattr(C, "QM_EXTENDED_SMA10_PCT", 40.0)
    extreme_pct  = getattr(C, "QM_EXTENDED_SMA10_EXTREME", 60.0)

    if df.empty:
        return {"status": "UNKNOWN", "pct_above_sma": None,
                "action": "HOLD_TRAIL", "warning_zh": ""}

    close = float(df["Close"].iloc[-1])
    col   = f"SMA_{sma_period}"
    if col in df.columns:
        sma_val = float(df[col].iloc[-1])
    else:
        if len(df) >= sma_period:
            sma_val = float(df["Close"].rolling(sma_period).mean().iloc[-1])
        else:
            return {"status": "UNKNOWN", "pct_above_sma": None,
                    "action": "HOLD_TRAIL", "warning_zh": ""}

    if sma_val <= 0:
        return {"status": "UNKNOWN", "pct_above_sma": None,
                "action": "HOLD_TRAIL", "warning_zh": ""}

    pct_above = (close / sma_val - 1.0) * 100.0

    if pct_above >= extreme_pct:
        status    = "EXTREME"
        action    = "SELL_IMMEDIATELY"
        warning   = (f"⚠️ 極度延伸：股價高於{sma_period}SMA達 {pct_above:.0f}%（>{extreme_pct:.0f}%）"
                     f"\nQullamaggie原話：'我直接賣出，不等10SMA止損' — 考慮立即賣出")
    elif pct_above >= extended_pct:
        status    = "EXTENDED"
        action    = "CONSIDER_REDUCING"
        warning   = (f"⚠️ 過度延伸：股價高於{sma_period}SMA達 {pct_above:.0f}%（>{extended_pct:.0f}%）"
                     f"\n若繼續持有，回調空間大 — 考慮提前減倉，不等10SMA信號")
    else:
        status    = "NORMAL"
        action    = "HOLD_TRAIL"
        warning   = ""

    return {
        "status":        status,
        "pct_above_sma": round(pct_above, 1),
        "sma_value":     round(sma_val, 2),
        "close":         round(close, 2),
        "action":        action,
        "warning_zh":    warning,
        "tooltip":       (
            f"延伸狀態 (Extended Check — Supplement 4+33)\n"
            f"股價距 {sma_period}SMA: {pct_above:+.1f}%\n"
            f">40% = 過度延伸，考慮減倉；>60% = 極端，Qullamaggie直接賣出\n"
            "'Extended stocks do get more extended, but giving back too much"
            " is a real risk when this far from the MA.'"
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
# Green-to-Red Stop Detection  (Supplement 12)
# ─────────────────────────────────────────────────────────────────────────────

def get_gap_down_stop(df: pd.DataFrame) -> dict:
    """
    Supplement 12 — Green-to-Red (G2R) Stop: detect when a stock that gapped
    UP on open then reverses to go RED (below prior close).

    Qullamaggie: "If a stock opens above prior close (gap up) and then goes RED
    — sell immediately.  A green-to-red is almost always a sign of distribution.
    Smart money used the gap-up open to unload their position."

    Args:
        df: OHLCV DataFrame (must have at least 2 bars)

    Returns:
        dict with:
            'is_gap_up'           : bool  — today opened above prior close
            'is_green_to_red'     : bool  — opened gap-up but now below prior close
            'gap_up_pct'          : float — % of the opening gap
            'suggested_stop_type' : str   — 'GREEN_TO_RED'|'NORMAL_LOD'|'N/A'
            'warning_zh'          : str   — Chinese warning message
    """
    empty = {
        "is_gap_up": False, "is_green_to_red": False,
        "gap_up_pct": 0.0, "suggested_stop_type": "N/A", "warning_zh": "數據不足",
    }
    if df.empty or len(df) < 2:
        return empty

    if not getattr(C, "QM_GREEN_TO_RED_STOP", True):
        return {**empty, "warning_zh": "G2R止損已禁用"}

    prev_close  = float(df["Close"].iloc[-2])
    today_open  = float(df["Open"].iloc[-1])
    today_close = float(df["Close"].iloc[-1])

    is_gap_up       = today_open > prev_close
    is_green_to_red = is_gap_up and today_close < prev_close  # Closed below prior close
    gap_pct         = (today_open / prev_close - 1.0) * 100.0 if is_gap_up else 0.0

    if is_green_to_red:
        stop_type = "GREEN_TO_RED"
        warning   = (
            f"⚠️ 高開低走 (Green-to-Red)！今天跳空高開 +{gap_pct:.1f}%，"
            f"但現在跌破昨收 ${prev_close:.2f} — 建議立即出場 (S12)"
        )
    elif is_gap_up:
        stop_type = "NORMAL_LOD"
        warning   = f"跳空高開 +{gap_pct:.1f}%，監控是否高開低走 (S12警示)"
    else:
        stop_type = "N/A"
        warning   = "今天非跳空開盤 (S12 G2R無效)"

    return {
        "is_gap_up":           is_gap_up,
        "is_green_to_red":     is_green_to_red,
        "gap_up_pct":          round(gap_pct, 2),
        "suggested_stop_type": stop_type,
        "warning_zh":          warning,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Broken Chart Detection  (Supplement 13)
# ─────────────────────────────────────────────────────────────────────────────

def check_broken_chart(df: pd.DataFrame) -> dict:
    """
    Supplement 13 — Broken Chart Detection: identify when a chart structure is
    definitively broken and the stock should be exited immediately.

    Qullamaggie: "When a stock breaks below the 50-day on heavy volume AND
    the 10-day and 20-day are also below it — the chart is broken.  Higher lows
    pattern is broken.  Don't hope for a recovery — exit."

    A broken chart requires ALL of:
      1. Price < SMA10
      2. Price < SMA20
      3. Price < SMA50
      4. SMA10 is declining over last 5 bars
      5. SMA20 is declining over last 5 bars
      6. No higher lows pattern (from get_higher_lows)

    Args:
        df: OHLCV DataFrame (at least 60 bars recommended)

    Returns:
        dict with:
            'is_broken'   : bool — chart is definitively broken
            'reason'      : str  — description of what broke
            'criteria_met': int  — how many of the 6 criteria are met (0-6)
            'warning_zh'  : str  — Chinese warning message
    """
    from modules.data_pipeline import get_higher_lows

    empty = {
        "is_broken": False, "reason": "數據不足",
        "criteria_met": 0, "warning_zh": "無法評估",
    }
    if df.empty or len(df) < 55:
        return empty

    last_close = float(df["Close"].iloc[-1])
    criteria   = []
    reasons    = []

    # Compute SMAs
    sma10_series = df["Close"].rolling(10).mean().dropna()
    sma20_series = df["Close"].rolling(20).mean().dropna()
    sma50_series = df["Close"].rolling(50).mean().dropna()

    sma10 = float(sma10_series.iloc[-1]) if not sma10_series.empty else None
    sma20 = float(sma20_series.iloc[-1]) if not sma20_series.empty else None
    sma50 = float(sma50_series.iloc[-1]) if not sma50_series.empty else None

    # Criterion 1-3: Price below each MA
    if sma10 and last_close < sma10:
        criteria.append(True)
        reasons.append("價格低於10SMA")
    else:
        criteria.append(False)

    if sma20 and last_close < sma20:
        criteria.append(True)
        reasons.append("價格低於20SMA")
    else:
        criteria.append(False)

    if sma50 and last_close < sma50:
        criteria.append(True)
        reasons.append("價格低於50SMA")
    else:
        criteria.append(False)

    # Criterion 4-5: MA10 and MA20 declining (last 5 bars)
    if len(sma10_series) >= 6:
        declining10 = float(sma10_series.iloc[-1]) < float(sma10_series.iloc[-6])
        criteria.append(declining10)
        if declining10:
            reasons.append("10SMA仍在下彎")
    else:
        criteria.append(False)

    if len(sma20_series) >= 6:
        declining20 = float(sma20_series.iloc[-1]) < float(sma20_series.iloc[-6])
        criteria.append(declining20)
        if declining20:
            reasons.append("20SMA仍在下彎")
    else:
        criteria.append(False)

    # Criterion 6: Higher lows broken
    from modules.data_pipeline import get_higher_lows
    hl_info = get_higher_lows(df)
    no_higher_lows = not hl_info.get("has_higher_lows", False)
    criteria.append(no_higher_lows)
    if no_higher_lows:
        reasons.append("高低點結構已破壞")

    met_count = sum(criteria)
    is_broken = met_count >= 5  # Need at least 5 of 6 to declare broken

    if is_broken:
        reason_str = "、".join(reasons)
        warning_zh = (
            f"🚨 圖表結構已破壞 ({met_count}/6 條件)：{reason_str} "
            f"— 建議立即出場，不要等待反彈 (S13)"
        )
    elif met_count >= 3:
        warning_zh = f"⚠️ 圖表結構快破壞 ({met_count}/6 條件)：{', '.join(reasons)} (S13監察)"
    else:
        warning_zh = f"圖表結構完整 ({met_count}/6 破壞條件)"

    return {
        "is_broken":    is_broken,
        "criteria_met": met_count,
        "reason":       "、".join(reasons) if reasons else "無明顯問題",
        "warning_zh":   warning_zh,
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
    from modules.data_pipeline import get_atr as _get_atr
    atr_val   = _get_atr(df)
    day1_info  = get_day1_stop(entry_price, float(df["Low"].iloc[-1]), atr=atr_val)
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

    # ── Supplement 4 + 33: Extended stock check ────────────────────────────
    extended = check_extended_stock(df)
    if extended.get("status") in ("EXTENDED", "EXTREME"):
        severity = "critical" if extended["status"] == "EXTREME" else "warning"
        signals.append({
            "type":     "EXTENDED",
            "severity": severity,
            "msg_zh":   extended["warning_zh"],
            "msg_en":   f"Stock {extended['status']} ({extended.get('pct_above_sma',0):.0f}% above 10SMA) — {extended['action']}",
        })
        # If extreme and we haven't already stopped, override primary action
        if extended["status"] == "EXTREME" and not stop_triggered:
            primary_action = "SELL_IMMEDIATELY"

    # ── Supplement 13: Broken Chart Detection ──────────────────────────────
    broken = check_broken_chart(df)
    if broken.get("is_broken"):
        signals.append({
            "type":     "BROKEN_CHART",
            "severity": "critical",
            "msg_zh":   broken["warning_zh"],
            "msg_en":   f"Broken chart ({broken['criteria_met']}/6 criteria): {broken['reason']}",
        })
        if not stop_triggered:
            primary_action = "SELL_IMMEDIATELY"
    elif broken.get("criteria_met", 0) >= 3:
        signals.append({
            "type":     "BROKEN_CHART_WARNING",
            "severity": "warning",
            "msg_zh":   broken["warning_zh"],
            "msg_en":   f"Chart weakening ({broken['criteria_met']}/6 criteria)",
        })

    # ── Supplement 12: Green-to-Red (G2R) Stop ─────────────────────────────
    g2r = get_gap_down_stop(df)
    if g2r.get("is_green_to_red"):
        signals.append({
            "type":     "GREEN_TO_RED",
            "severity": "critical",
            "msg_zh":   g2r["warning_zh"],
            "msg_en":   f"Green-to-Red! Gap up {g2r['gap_up_pct']:.1f}% then reversed — sell immediately",
        })
        if not stop_triggered:
            primary_action = "SELL_IMMEDIATELY"
    elif g2r.get("is_gap_up"):
        signals.append({
            "type":     "GAP_UP_WATCH",
            "severity": "info",
            "msg_zh":   g2r["warning_zh"],
            "msg_en":   f"Gap up {g2r['gap_up_pct']:.1f}% — monitor for G2R reversal",
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
        "extended":         extended,
        "broken_chart":     broken,
        "green_to_red":     g2r,
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
