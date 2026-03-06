"""
modules/position_controller.py
───────────────────────────────
3-Pool Position Control Engine  (ML倉 / QM倉 / 自由倉)

Implements:
  • Pool allocation / release bookkeeping
  • Per-pool + total risk budget (heat) tracking
  • Position count enforcement
  • Drawdown gate & loss-streak throttle
  • Comprehensive DuckDB logging via db.append_pool_log / db.append_exit_log

All pool-level decisions are logged so every allocation, rejection, and release
can be audited through the UI or DuckDB queries.
"""

import sys
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Pool configuration helpers
# ═══════════════════════════════════════════════════════════════════════════════

_POOL_CFG = {
    "ML":   {"cap_pct": C.PC_POOL_ML_PCT,   "max_pos": C.PC_ML_MAX_POSITIONS,   "max_heat": C.PC_ML_MAX_HEAT_PCT},
    "QM":   {"cap_pct": C.PC_POOL_QM_PCT,   "max_pos": C.PC_QM_MAX_POSITIONS,   "max_heat": C.PC_QM_MAX_HEAT_PCT},
    "FREE": {"cap_pct": C.PC_POOL_FREE_PCT, "max_pos": C.PC_FREE_MAX_POSITIONS, "max_heat": C.PC_FREE_MAX_HEAT_PCT},
}


def _pool_cfg(pool: str) -> dict:
    return _POOL_CFG.get(pool.upper(), _POOL_CFG["FREE"])


def get_pool_for_strategy(strategy: str) -> str:
    """Map strategy name to pool. Non-ML/QM → FREE."""
    s = strategy.upper().strip()
    if s == "ML":
        return "ML"
    if s == "QM":
        return "QM"
    return "FREE"


# ═══════════════════════════════════════════════════════════════════════════════
# Pool status snapshot
# ═══════════════════════════════════════════════════════════════════════════════

def get_pool_status(account_size: Optional[float] = None) -> dict:
    """
    Build a complete snapshot of all pools.

    Returns:
        {
          "account_size": float,
          "total_positions": int,
          "total_used_pct": float,
          "total_heat_pct": float,
          "pools": {
            "ML":   { "positions": int, "max_positions": int, "used_value": float,
                      "cap_pct": float, "used_pct": float, "heat_pct": float,
                      "max_heat": float, "tickers": [...] },
            "QM":   { ... },
            "FREE": { ... },
          },
          "drawdown_pct": float,
          "loss_streak": int,
          "size_multiplier": float,   # 1.0 normal, 0.5 reduced, 0.0 stopped
        }
    """
    from modules.position_monitor import get_positions_by_pool, _load

    data = _load()
    if account_size is None:
        account_size = float(data.get("account_high", C.ACCOUNT_SIZE) or C.ACCOUNT_SIZE)
    if account_size <= 0:
        account_size = float(C.ACCOUNT_SIZE)

    grouped = get_positions_by_pool()

    pools = {}
    total_used = 0.0
    total_heat = 0.0
    total_positions = 0

    for pool_name in C.PC_POOL_NAMES:
        cfg = _pool_cfg(pool_name)
        positions = grouped.get(pool_name, {})
        count = len(positions)
        total_positions += count

        used_value = sum(
            p.get("buy_price", 0) * p.get("shares", 0) for p in positions.values()
        )
        heat_value = sum(
            (p.get("buy_price", 0) - p.get("stop_loss", p.get("buy_price", 0))) * p.get("shares", 0)
            for p in positions.values()
        )

        used_pct = (used_value / account_size * 100) if account_size > 0 else 0
        heat_pct = (heat_value / account_size * 100) if account_size > 0 else 0
        total_used += used_value
        total_heat += heat_value

        pools[pool_name] = {
            "positions":     count,
            "max_positions": cfg["max_pos"],
            "used_value":    round(used_value, 2),
            "cap_pct":       cfg["cap_pct"],
            "used_pct":      round(used_pct, 2),
            "heat_pct":      round(heat_pct, 2),
            "max_heat":      cfg["max_heat"],
            "tickers":       list(positions.keys()),
        }

    total_used_pct = (total_used / account_size * 100) if account_size > 0 else 0
    total_heat_pct = (total_heat / account_size * 100) if account_size > 0 else 0

    dd_pct = _calc_drawdown_pct(data, account_size)
    streak = _calc_loss_streak(data)
    mult = _size_multiplier(dd_pct, streak)

    return {
        "account_size":     round(account_size, 2),
        "total_positions":  total_positions,
        "total_used_pct":   round(total_used_pct, 2),
        "total_heat_pct":   round(total_heat_pct, 2),
        "pools":            pools,
        "drawdown_pct":     round(dd_pct, 2),
        "loss_streak":      streak,
        "size_multiplier":  mult,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Allocation gate  —  can we open a new position in this pool?
# ═══════════════════════════════════════════════════════════════════════════════

def can_allocate(pool: str, entry_price: float, shares: int,
                 stop_price: float, account_size: Optional[float] = None) -> dict:
    """
    Check whether a proposed position can be allocated to *pool*.

    Returns:
        {"allowed": bool, "reason": str, "details": dict}
    """
    pool = pool.upper().strip()
    status = get_pool_status(account_size)
    acct = status["account_size"]
    ps   = status["pools"].get(pool, status["pools"]["FREE"])

    position_value = entry_price * shares
    risk_value     = (entry_price - stop_price) * shares
    new_used_pct   = ps["used_pct"] + (position_value / acct * 100 if acct > 0 else 0)
    new_heat_pct   = ps["heat_pct"] + (risk_value / acct * 100 if acct > 0 else 0)
    new_total_heat = status["total_heat_pct"] + (risk_value / acct * 100 if acct > 0 else 0)

    details = {
        "pool":              pool,
        "pool_positions":    ps["positions"],
        "pool_max_pos":      ps["max_positions"],
        "total_positions":   status["total_positions"],
        "total_max_pos":     C.PC_TOTAL_MAX_POSITIONS,
        "pool_used_pct":     round(new_used_pct, 2),
        "pool_cap_pct":      ps["cap_pct"],
        "pool_heat_pct":     round(new_heat_pct, 2),
        "pool_max_heat":     ps["max_heat"],
        "total_heat_pct":    round(new_total_heat, 2),
        "total_max_heat":    C.PC_TOTAL_MAX_HEAT_PCT,
        "drawdown_pct":      status["drawdown_pct"],
        "loss_streak":       status["loss_streak"],
        "size_multiplier":   status["size_multiplier"],
    }

    # ── Drawdown hard stop ────────────────────────────────────────────────
    if status["drawdown_pct"] >= C.PC_DRAWDOWN_STOP_PCT:
        return {"allowed": False, "reason": f"帳戶回撤 {status['drawdown_pct']:.1f}% >= {C.PC_DRAWDOWN_STOP_PCT}% → 全停", "details": details}

    # ── Loss streak hard stop ─────────────────────────────────────────────
    if status["loss_streak"] >= C.PC_LOSS_STREAK_STOP:
        return {"allowed": False, "reason": f"連續虧損 {status['loss_streak']} >= {C.PC_LOSS_STREAK_STOP} → 全停", "details": details}

    # ── Total position count ──────────────────────────────────────────────
    if status["total_positions"] >= C.PC_TOTAL_MAX_POSITIONS:
        return {"allowed": False, "reason": f"全帳戶持倉 {status['total_positions']} >= {C.PC_TOTAL_MAX_POSITIONS} 上限", "details": details}

    # ── Pool position count ───────────────────────────────────────────────
    if ps["positions"] >= ps["max_positions"]:
        return {"allowed": False, "reason": f"{pool}倉持倉 {ps['positions']} >= {ps['max_positions']} 上限", "details": details}

    # ── Pool capital cap ──────────────────────────────────────────────────
    if new_used_pct > ps["cap_pct"]:
        return {"allowed": False, "reason": f"{pool}倉 佔用 {new_used_pct:.1f}% > {ps['cap_pct']}% 資金上限", "details": details}

    # ── Pool heat cap ─────────────────────────────────────────────────────
    if new_heat_pct > ps["max_heat"]:
        return {"allowed": False, "reason": f"{pool}倉 風險 {new_heat_pct:.1f}% > {ps['max_heat']}% 上限", "details": details}

    # ── Total heat cap ────────────────────────────────────────────────────
    if new_total_heat > C.PC_TOTAL_MAX_HEAT_PCT:
        return {"allowed": False, "reason": f"全帳戶風險 {new_total_heat:.1f}% > {C.PC_TOTAL_MAX_HEAT_PCT}% 上限", "details": details}

    return {"allowed": True, "reason": "OK", "details": details}


# ═══════════════════════════════════════════════════════════════════════════════
# Allocate / Release  — bookkeeping + DuckDB logging
# ═══════════════════════════════════════════════════════════════════════════════

def allocate_to_pool(ticker: str, pool: str, shares: int,
                     entry_price: float, stop_price: float,
                     account_size: Optional[float] = None,
                     note: str = "") -> dict:
    """
    Record allocation of *ticker* to *pool* in the pool log.
    Call AFTER the buy order is confirmed and add_position() has stored the position.

    Returns the pool status snapshot after allocation.
    """
    pool = pool.upper().strip()
    status = get_pool_status(account_size)
    ps = status["pools"].get(pool, status["pools"]["FREE"])

    position_value = entry_price * shares
    risk_value = (entry_price - stop_price) * shares

    log_note = note or f"BUY {shares}@${entry_price:.2f} stop=${stop_price:.2f}"
    logger.info("[PoolCtrl] ALLOCATE %s → %s: %d shares, value=$%.0f, risk=$%.0f | %s",
                ticker, pool, shares, position_value, risk_value, log_note)

    _log_pool_event(
        ticker=ticker, pool=pool, action="ALLOCATE",
        shares=shares, entry_price=entry_price, stop_price=stop_price,
        status=status, note=log_note,
    )

    return status


def release_from_pool(ticker: str, pool: str, shares: int,
                      exit_price: float, entry_price: float,
                      stop_price: float, reason: str = "",
                      strategy: str = "", exit_type: str = "FULL",
                      shares_remaining: int = 0, dry_run: bool = False,
                      account_size: Optional[float] = None) -> dict:
    """
    Record release of *ticker* from *pool* + exit log.
    Call AFTER close_position() has updated the position data.

    Returns the pool status snapshot after release.
    """
    pool = pool.upper().strip()
    status = get_pool_status(account_size)

    pnl_pct = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
    pnl_dollars = (exit_price - entry_price) * shares
    risk_per_share = entry_price - stop_price if stop_price > 0 else entry_price * 0.08
    r_multiple = (exit_price - entry_price) / risk_per_share if risk_per_share > 0 else 0

    ps = status["pools"].get(pool, status["pools"]["FREE"])

    logger.info("[PoolCtrl] RELEASE %s ← %s: %d shares @ $%.2f  P&L: %.1f%% ($%.0f) %.1fR | %s",
                ticker, pool, shares, exit_price, pnl_pct, pnl_dollars, r_multiple, reason)

    # ── Pool log (release event) ──────────────────────────────────────────
    _log_pool_event(
        ticker=ticker, pool=pool, action="RELEASE",
        shares=shares, entry_price=entry_price, stop_price=stop_price,
        status=status, note=f"{exit_type}: {reason}",
    )

    # ── Exit log (detailed trade outcome) ─────────────────────────────────
    try:
        from modules import db
        db.append_exit_log({
            "ticker": ticker, "pool": pool, "strategy": strategy or pool,
            "exit_type": exit_type, "shares_sold": shares,
            "shares_remaining": shares_remaining,
            "exit_price": exit_price, "entry_price": entry_price,
            "stop_price": stop_price, "r_multiple": round(r_multiple, 2),
            "pnl_pct": round(pnl_pct, 2), "pnl_dollars": round(pnl_dollars, 2),
            "pool_used_after": ps.get("used_pct", 0),
            "pool_heat_after": ps.get("heat_pct", 0),
            "total_heat_after": status.get("total_heat_pct", 0),
            "reason": reason, "dry_run": dry_run,
        })
    except Exception as exc:
        logger.warning("[PoolCtrl] append_exit_log failed: %s", exc)

    return status


# ═══════════════════════════════════════════════════════════════════════════════
# Position size adjustment  —  drawdown / loss-streak throttle
# ═══════════════════════════════════════════════════════════════════════════════

def adjusted_position_size(base_shares: int, account_size: Optional[float] = None) -> dict:
    """
    Apply drawdown / loss-streak multiplier to *base_shares*.

    Returns:
        {"shares": int, "multiplier": float, "reason": str}
    """
    status = get_pool_status(account_size)
    mult = status["size_multiplier"]

    if mult <= 0.0:
        return {"shares": 0, "multiplier": 0.0, "reason": "自動交易已暫停 (回撤/連虧)"}

    adjusted = max(1, int(base_shares * mult))

    if mult < 1.0:
        reason = f"減半: DD={status['drawdown_pct']:.1f}% streak={status['loss_streak']}"
    else:
        reason = "正常"

    return {"shares": adjusted, "multiplier": mult, "reason": reason}


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_drawdown_pct(data: dict, account_size: float) -> float:
    """Account drawdown from high-water mark."""
    hwm = data.get("account_high", account_size)
    if hwm <= 0:
        return 0.0
    return max(0.0, (hwm - account_size) / hwm * 100)


def _calc_loss_streak(data: dict) -> int:
    """Count consecutive recent losses from closed positions (newest first)."""
    closed = data.get("closed", [])
    streak = 0
    for pos in reversed(closed):
        if pos.get("pnl_pct", 0) < 0:
            streak += 1
        else:
            break
    return streak


def _size_multiplier(dd_pct: float, loss_streak: int) -> float:
    """Determine position size multiplier based on drawdown & loss streak."""
    if dd_pct >= C.PC_DRAWDOWN_STOP_PCT or loss_streak >= C.PC_LOSS_STREAK_STOP:
        return 0.0
    if dd_pct >= C.PC_DRAWDOWN_REDUCE_PCT or loss_streak >= C.PC_LOSS_STREAK_HALVE:
        return 0.5
    return 1.0


def _log_pool_event(ticker: str, pool: str, action: str,
                    shares: int, entry_price: float, stop_price: float,
                    status: dict, note: str = ""):
    """Write a row to the position_pool_log table."""
    try:
        from modules import db
        ps = status["pools"].get(pool, {})
        db.append_pool_log({
            "ticker": ticker, "pool": pool, "action": action,
            "shares": shares, "entry_price": entry_price, "stop_price": stop_price,
            "position_value": round(entry_price * shares, 2),
            "risk_dollars": round((entry_price - stop_price) * shares, 2),
            "pool_used_pct": ps.get("used_pct", 0),
            "pool_heat_pct": ps.get("heat_pct", 0),
            "total_used_pct": status.get("total_used_pct", 0),
            "total_heat_pct": status.get("total_heat_pct", 0),
            "pool_positions": ps.get("positions", 0),
            "account_size": status.get("account_size", 0),
            "note": note,
        })
    except Exception as exc:
        logger.warning("[PoolCtrl] append_pool_log failed: %s", exc)
