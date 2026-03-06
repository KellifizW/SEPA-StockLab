"""
modules/exit_engine.py  —  Automated Exit / Trailing Stop Execution Engine
═══════════════════════════════════════════════════════════════════════════════
Orchestrates the ML and QM position-rule modules to check every open position
and execute sell orders (full or partial) when exit signals fire.

Exit signal sources (per strategy):
  ML:  check_ml_position()  → STOP_HIT | SELL_ALL | TAKE_PARTIAL_3R/5R
  QM:  check_qm_position()  → STOP_HIT | SELL_ALL | SELL_IMMEDIATELY | TAKE_PARTIAL_PROFIT
  ALL: Time stop (no gain after N days), Climax top detection

Flow per check cycle:
  1. Load all open positions from position_monitor
  2. For each position:
     a. Dispatch to ML or QM health-check based on pool tag
     b. Determine primary_action + signals
     c. If action warrants sell → place SELL order via ibkr_client
     d. Update position (partial / full close)
     e. Release pool allocation via position_controller
     f. Log everything to DuckDB exit_log + pool_log
  3. Update module-level status for UI polling

All actions logged with detailed reasons for post-trade auditing.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)


# ── Module-level state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_exit_status: dict = {
    "last_check_at":     None,
    "positions_checked":  0,
    "exits_this_cycle":   0,
    "total_exits_today":  0,
    "results":            [],   # Last cycle results per position
    "error":              None,
}


def get_exit_status() -> dict:
    """Return a snapshot of the exit engine's last check results."""
    with _lock:
        return dict(_exit_status)


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point — called from auto_trader polling loop
# ═══════════════════════════════════════════════════════════════════════════════

def check_all_positions(dry_run: bool = True) -> list[dict]:
    """
    Check every open position and execute exits where signals fire.

    Args:
        dry_run: If True, log only — don't place actual sell orders.

    Returns:
        List of per-position result dicts with actions taken.
    """
    from modules.position_monitor import _load

    data = _load()
    positions = data.get("positions", {})

    if not positions:
        with _lock:
            _exit_status["last_check_at"] = datetime.now().isoformat()
            _exit_status["positions_checked"] = 0
            _exit_status["exits_this_cycle"] = 0
            _exit_status["results"] = []
            _exit_status["error"] = None
        return []

    results = []
    exits_this_cycle = 0
    max_sells = getattr(C, "EXIT_MAX_SELLS_PER_CYCLE", 3)

    for ticker, pos in list(positions.items()):
        if exits_this_cycle >= max_sells:
            logger.info("[ExitEngine] Hit max sells per cycle (%d), deferring rest", max_sells)
            break

        try:
            result = _check_single_position(ticker, pos, dry_run)
            results.append(result)

            if result.get("action_taken") and result["action_taken"] != "HOLD":
                exits_this_cycle += 1
        except Exception as exc:
            logger.warning("[ExitEngine] Error checking %s: %s", ticker, exc)
            results.append({
                "ticker": ticker,
                "pool": pos.get("pool", "FREE"),
                "action_taken": "ERROR",
                "reason": str(exc),
            })

    with _lock:
        _exit_status["last_check_at"] = datetime.now().isoformat()
        _exit_status["positions_checked"] = len(positions)
        _exit_status["exits_this_cycle"] = exits_this_cycle
        _exit_status["total_exits_today"] = (
            _exit_status.get("total_exits_today", 0) + exits_this_cycle
        )
        _exit_status["results"] = results
        _exit_status["error"] = None

    if exits_this_cycle > 0:
        logger.info("[ExitEngine] Cycle done: %d positions checked, %d exits executed",
                    len(positions), exits_this_cycle)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Per-position check dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

def _check_single_position(ticker: str, pos: dict, dry_run: bool) -> dict:
    """
    Run strategy-specific health check for one position and execute exit if needed.

    Returns a result dict:
        {ticker, pool, strategy, phase, close, gain_pct, r_multiple,
         primary_action, signals, action_taken, shares_sold, reason, ...}
    """
    pool = pos.get("pool", "FREE")
    entry_price = pos.get("buy_price", 0)
    shares = pos.get("shares", 0)
    current_stop = pos.get("stop_loss", 0)
    entry_date = pos.get("buy_date", date.today().isoformat())
    note = pos.get("note", "")
    star_rating = _extract_star_rating(note)

    # ── Dispatch to strategy-specific check ───────────────────────────────
    if pool == "ML":
        health = _check_ml(ticker, entry_price, current_stop, entry_date, shares,
                           star_rating=star_rating)
    elif pool == "QM":
        health = _check_qm(ticker, entry_price, current_stop, entry_date, shares,
                           star_rating=star_rating)
    else:
        # FREE pool → use Minervini-style check from position_monitor
        health = _check_free(ticker, pos)

    if health.get("error"):
        return {
            "ticker": ticker, "pool": pool, "action_taken": "HOLD",
            "reason": f"Data error: {health['error']}",
            "signals": [],
        }

    primary_action = health.get("primary_action", "HOLD")
    signals = health.get("signals", [])
    close = health.get("close", 0)
    gain_pct = health.get("gain_pct", 0)
    r_multiple = health.get("r_multiple", 0)
    recommended_stop = health.get("recommended_stop", current_stop)

    # ── Time stop check (all strategies) ──────────────────────────────────
    time_stop = _check_time_stop(entry_date, gain_pct)
    if time_stop and primary_action == "HOLD":
        primary_action = "TIME_STOP"
        signals.append(time_stop)

    # ── Climax top check (all strategies) ─────────────────────────────────
    climax = _check_climax(health)
    if climax and primary_action in ("HOLD", "UPDATE_STOP"):
        primary_action = "CLIMAX_EXIT"
        signals.append(climax)

    # ── Decide shares to sell ─────────────────────────────────────────────
    shares_to_sell = 0
    exit_type = "NONE"
    reason = ""

    if primary_action in ("STOP_HIT", "SELL_ALL", "SELL_IMMEDIATELY",
                          "TIME_STOP", "CLIMAX_EXIT"):
        shares_to_sell = shares
        exit_type = "FULL"
        reason = _build_exit_reason(primary_action, signals, health)

    elif primary_action in ("TAKE_PARTIAL_3R", "TAKE_PARTIAL_5R", "TAKE_PARTIAL_PROFIT"):
        profit_info = health.get("profit_action", {})
        shares_to_sell = profit_info.get("shares_to_sell", 0)
        if shares_to_sell > 0 and shares_to_sell < shares:
            exit_type = "PARTIAL"
            reason = _build_exit_reason(primary_action, signals, health)
        elif shares_to_sell >= shares:
            exit_type = "FULL"
            shares_to_sell = shares
            reason = _build_exit_reason(primary_action, signals, health)
        else:
            shares_to_sell = 0

    # ── Update stop if recommendation is higher ──────────────────────────
    if (primary_action in ("HOLD", "UPDATE_STOP") and
            recommended_stop > current_stop and recommended_stop > 0):
        _update_position_stop(ticker, recommended_stop)
        logger.info("[ExitEngine] %s [%s] stop updated %.2f → %.2f",
                    ticker, pool, current_stop, recommended_stop)

    # ── Execute exit ──────────────────────────────────────────────────────
    action_taken = "HOLD"
    if shares_to_sell > 0:
        action_taken = _execute_exit(
            ticker=ticker,
            pool=pool,
            shares_to_sell=shares_to_sell,
            total_shares=shares,
            exit_type=exit_type,
            exit_price=close,
            entry_price=entry_price,
            stop_price=current_stop,
            reason=reason,
            strategy=pool,
            dry_run=dry_run,
        )

    return {
        "ticker":           ticker,
        "pool":             pool,
        "phase":            health.get("current_phase", 0),
        "close":            round(close, 2),
        "gain_pct":         round(gain_pct, 2),
        "r_multiple":       round(r_multiple, 2),
        "primary_action":   primary_action,
        "recommended_stop": round(recommended_stop, 2),
        "signals":          signals,
        "action_taken":     action_taken,
        "shares_sold":      shares_to_sell if action_taken != "HOLD" else 0,
        "exit_type":        exit_type if action_taken != "HOLD" else "NONE",
        "reason":           reason,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy-specific health checks
# ═══════════════════════════════════════════════════════════════════════════════

def _check_ml(ticker, entry_price, current_stop, entry_date, shares,
              star_rating=3.0):
    """Run Martin Luk position health check."""
    from modules.ml_position_rules import check_ml_position
    return check_ml_position(
        ticker=ticker,
        entry_price=entry_price,
        current_stop=current_stop,
        entry_date=entry_date,
        shares=shares,
        star_rating=star_rating,
    )


def _check_qm(ticker, entry_price, current_stop, entry_date, shares,
              star_rating=4.0):
    """Run Qullamaggie position health check."""
    from modules.qm_position_rules import check_qm_position
    return check_qm_position(
        ticker=ticker,
        entry_price=entry_price,
        current_stop=current_stop,
        entry_date=entry_date,
        shares=shares,
        star_rating=star_rating,
    )


def _check_free(ticker: str, pos: dict) -> dict:
    """
    Fallback health check for FREE pool positions (Minervini-style).
    Uses position_monitor's existing _check_position logic.
    """
    from modules.position_monitor import _check_position
    result = _check_position(ticker, pos)

    # Map position_monitor's format → unified format
    primary_action = "HOLD"
    signals = []

    health = result.get("health", "OK")
    sell_signals = result.get("sell_signals", [])

    if health == "EXIT":
        primary_action = "SELL_ALL"
    elif health == "DANGER":
        # Check for stop hit
        current_price = result.get("current_price", 0)
        stop_loss = pos.get("stop_loss", 0)
        if current_price > 0 and stop_loss > 0 and current_price <= stop_loss:
            primary_action = "STOP_HIT"

    for sig in sell_signals:
        signals.append({
            "type": sig.get("type", "UNKNOWN"),
            "severity": "critical" if "stop" in sig.get("msg", "").lower() else "warning",
            "msg_zh": sig.get("msg", sig.get("msg_zh", "")),
            "msg_en": sig.get("msg_en", sig.get("msg", "")),
        })

    return {
        "ticker": ticker,
        "current_phase": 3,
        "close": result.get("current_price", 0),
        "entry_price": pos.get("buy_price", 0),
        "gain_pct": result.get("pnl_pct", 0),
        "r_multiple": 0,
        "primary_action": primary_action,
        "stop_triggered": primary_action in ("STOP_HIT", "SELL_ALL"),
        "recommended_stop": result.get("new_stop", pos.get("stop_loss", 0)),
        "signals": signals,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-strategy exit checks
# ═══════════════════════════════════════════════════════════════════════════════

def _check_time_stop(entry_date_str: str, gain_pct: float) -> Optional[dict]:
    """
    Time stop: if position has not moved favourably after N days → exit signal.
    Returns a signal dict or None.
    """
    try:
        entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    days_held = (date.today() - entry_dt).days
    min_days = getattr(C, "EXIT_TIME_STOP_DAYS", 5)
    min_gain = getattr(C, "EXIT_TIME_STOP_MIN_GAIN_PCT", 1.0)

    if days_held >= min_days and gain_pct < min_gain:
        return {
            "type": "TIME_STOP",
            "severity": "critical",
            "msg_zh": f"時間止損：持有 {days_held} 天，僅漲 {gain_pct:.1f}% (< {min_gain}%)",
            "msg_en": f"Time stop: held {days_held}d, only +{gain_pct:.1f}% (< {min_gain}%)",
        }
    return None


def _check_climax(health: dict) -> Optional[dict]:
    """
    Detect climax top / parabolic move from health check data.
    Returns a signal dict or None.
    """
    # Check if the ML/QM health check already flagged parabolic/extreme vol
    profit_action = health.get("profit_action", {})
    action = profit_action.get("action", "")

    if action in ("SELL_ALL_PARABOLIC", "SELL_ALL_EXTREME_VOL"):
        return {
            "type": "CLIMAX_TOP",
            "severity": "critical",
            "msg_zh": profit_action.get("reason_zh", "高潮頂 / 拋物線加速 — 全部出場"),
            "msg_en": f"Climax exit: {action}",
        }

    # Check QM-specific extended/broken chart signals
    extended = health.get("extended", {})
    if extended.get("status") == "EXTREME":
        return {
            "type": "CLIMAX_EXTENDED",
            "severity": "critical",
            "msg_zh": extended.get("warning_zh", "極度超買 — Qullamaggie 建議立即出場"),
            "msg_en": f"Extreme extension: {extended.get('pct_above_sma', 0):.0f}% above 10SMA",
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Exit execution
# ═══════════════════════════════════════════════════════════════════════════════

def _execute_exit(ticker: str, pool: str, shares_to_sell: int, total_shares: int,
                  exit_type: str, exit_price: float, entry_price: float,
                  stop_price: float, reason: str, strategy: str,
                  dry_run: bool) -> str:
    """
    Execute or log a sell order, then update position and pool records.

    Returns the action taken: "FULL_EXIT" | "PARTIAL_EXIT" | "DRY_RUN" | "HOLD"
    """
    is_full = exit_type == "FULL" or shares_to_sell >= total_shares
    shares_remaining = 0 if is_full else (total_shares - shares_to_sell)
    action_label = "FULL_EXIT" if is_full else "PARTIAL_EXIT"

    logger.info("[ExitEngine] %s %s [%s]: %d/%d shares @ $%.2f | %s",
                action_label, ticker, pool, shares_to_sell, total_shares,
                exit_price, reason)

    # ── Place sell order (or dry-run) ─────────────────────────────────────
    if dry_run:
        logger.info("[ExitEngine] DRY-RUN: Would sell %d shares of %s @ $%.2f (%s)",
                    shares_to_sell, ticker, exit_price, exit_type)
        action_label = "DRY_RUN"
    else:
        order_result = _place_sell_order(ticker, shares_to_sell, exit_price)
        if not order_result.get("success"):
            logger.warning("[ExitEngine] Sell order FAILED for %s: %s",
                           ticker, order_result.get("message", "unknown"))
            return "HOLD"

    # ── Update position (close/partial) ───────────────────────────────────
    try:
        from modules.position_monitor import close_position
        close_position(
            ticker=ticker,
            exit_price=exit_price,
            reason=reason,
            shares_to_close=shares_to_sell if not is_full else None,
        )
    except Exception as exc:
        logger.warning("[ExitEngine] close_position failed for %s: %s", ticker, exc)

    # ── Release pool allocation ───────────────────────────────────────────
    try:
        from modules.position_controller import release_from_pool
        release_from_pool(
            ticker=ticker,
            pool=pool,
            shares=shares_to_sell,
            exit_price=exit_price,
            entry_price=entry_price,
            stop_price=stop_price,
            reason=reason,
            strategy=strategy,
            exit_type=exit_type,
            shares_remaining=shares_remaining,
            dry_run=dry_run,
        )
    except Exception as exc:
        logger.warning("[ExitEngine] release_from_pool failed for %s: %s", ticker, exc)

    return action_label


def _place_sell_order(ticker: str, shares: int, reference_price: float) -> dict:
    """Place a SELL order via IBKR."""
    try:
        from modules.ibkr_client import place_order
        order_type = getattr(C, "EXIT_ORDER_TYPE", "MKT")
        return place_order(
            ticker=ticker,
            action="SELL",
            qty=shares,
            order_type=order_type,
        )
    except Exception as exc:
        logger.error("[ExitEngine] IBKR sell order exception for %s: %s", ticker, exc)
        return {"success": False, "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _update_position_stop(ticker: str, new_stop: float):
    """Update the stop loss for an open position."""
    try:
        from modules.position_monitor import update_stop
        update_stop(ticker, new_stop)
    except Exception as exc:
        logger.warning("[ExitEngine] update_stop failed for %s: %s", ticker, exc)


def _extract_star_rating(note: str) -> float:
    """Extract star rating from position note (e.g. 'Auto-QM 4.5★')."""
    import re
    match = re.search(r'(\d+\.?\d*)\s*★', note)
    if match:
        return float(match.group(1))
    return 3.0  # default


def _build_exit_reason(action: str, signals: list, health: dict) -> str:
    """Build a human-readable exit reason string for logging."""
    parts = [action]

    # Add top signal messages
    for sig in signals[:3]:
        msg = sig.get("msg_zh", sig.get("msg_en", ""))
        if msg:
            parts.append(msg)

    # Add R-multiple context
    r_mult = health.get("r_multiple", 0)
    if r_mult != 0:
        parts.append(f"R={r_mult:.1f}")

    return " | ".join(parts)


def reset_daily_counters():
    """Reset daily counters (called at midnight or by auto_trader)."""
    with _lock:
        _exit_status["total_exits_today"] = 0
