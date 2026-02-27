"""
modules/position_monitor.py
────────────────────────────
Daily Position Health Monitor  (Minervini Part 6 + 7 + 8)

Implements:
  • Position tracking (entry price, stop, shares)
  • Daily health checklist (14.2 from stockguide.md)
  • Trailing stop update recommendations (method 1 & 2)
  • ATR-based dynamic stop calculation
  • Sell signal detection (defensive + offensive)
  • Account drawdown level monitor (H1-H5)

Storage: data/positions.json
"""

import sys
import json
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_enriched
from modules.rs_ranking import get_rs_rank

logger = logging.getLogger(__name__)

POSITIONS_FILE = ROOT / C.DATA_DIR / "positions.json"

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════════════════

def _load() -> dict:
    """Load positions from JSON only (fastest, most reliable)."""
    import time
    start = time.time()
    
    if POSITIONS_FILE.exists():
        try:
            data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
            elapsed = time.time() - start
            logger.debug(f"[position_monitor] Loaded from JSON in {elapsed*1000:.1f}ms")
            return data
        except Exception as exc:
            logger.warning("[position_monitor] JSON load failed: %s, returning empty", exc)
    
    return {"positions": {}, "closed": [], "account_high": C.ACCOUNT_SIZE}


def _save(data: dict):
    """Save positions: JSON sync (fast) + DuckDB async background thread."""
    if not data:
        return

    import time
    start = time.time()

    # 1. JSON write — synchronous, always first (< 5 ms)
    try:
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        POSITIONS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        elapsed = time.time() - start
        logger.debug(f"[position_monitor] Saved to JSON in {elapsed*1000:.1f}ms")
    except Exception as exc:
        logger.warning("[position_monitor] JSON save failed: %s", exc)

    # 2. DuckDB write — fire-and-forget background thread (non-blocking)
    if C.DB_ENABLED:
        threading.Thread(
            target=_bg_db_save,
            args=(data.copy(),),
            daemon=True,
            name="pos_db_save",
        ).start()


def _bg_db_save(data: dict):
    """Background worker: archive positions to DuckDB (runs in daemon thread)."""
    try:
        from modules import db
        db.pos_save(data)
        logger.debug("[position_monitor] Background DuckDB archive complete")
    except Exception as exc:
        logger.warning("[position_monitor] Background DuckDB archive failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Position management
# ═══════════════════════════════════════════════════════════════════════════════

def add_position(ticker: str, buy_price: float, shares: int,
                 stop_loss: float, target: float = None, note: str = ""):
    """
    Record a new position.
    stop_loss: absolute price (not percentage).
    target:    target price (optional, auto-calculated if None).
    """
    import time
    ticker = ticker.upper().strip()
    start = time.time()
    
    # Fast JSON-only load (no DuckDB)
    data = _load()
    t1 = time.time()

    if ticker in data["positions"]:
        print(f"  {ticker} already in positions — use remove_position first to update")
        return

    if target is None:
        risk      = buy_price - stop_loss
        target    = buy_price + risk * C.IDEAL_RISK_REWARD

    stop_pct  = (buy_price - stop_loss) / buy_price * 100
    rr        = (target - buy_price) / (buy_price - stop_loss) if (buy_price - stop_loss) > 0 else 0
    risk_dol  = shares * (buy_price - stop_loss)
    pnl_pct   = 0.0

    data["positions"][ticker] = {
        "buy_price":   round(buy_price, 2),
        "shares":      shares,
        "stop_loss":   round(stop_loss, 2),
        "stop_pct":    round(stop_pct, 2),
        "target":      round(target, 2),
        "rr":          round(rr, 2),
        "risk_dollar": round(risk_dol, 2),
        "buy_date":    date.today().isoformat(),
        "days_held":   0,
        "max_price":   buy_price,    # track high water mark for trailing stop
        "note":        note,
    }
    _save(data)
    
    elapsed = time.time() - start
    logger.info(f"[add_position] {ticker} completed in {elapsed*1000:.1f}ms")
    
    print(f"  ✓ {ticker}: {shares} shares @ ${buy_price:.2f}  "
          f"Stop: ${stop_loss:.2f} (-{stop_pct:.1f}%)  "
          f"Target: ${target:.2f}  R:R {rr:.1f}:1")


def close_position(ticker: str, exit_price: float, reason: str = ""):
    """Record a position exit."""
    ticker = ticker.upper().strip()
    data   = _load()

    if ticker not in data["positions"]:
        print(f"  {ticker} not in open positions")
        return

    pos      = data["positions"].pop(ticker)
    buy_date = pos.get("buy_date", "")
    buy_price = pos["buy_price"]
    shares   = pos["shares"]
    pnl_pct  = (exit_price - buy_price) / buy_price * 100
    pnl_dol  = (exit_price - buy_price) * shares

    closed_entry = {
        **pos,
        "exit_price": round(exit_price, 2),
        "exit_date":  date.today().isoformat(),
        "pnl_pct":    round(pnl_pct, 2),
        "pnl_dollar": round(pnl_dol, 2),
        "reason":     reason,
    }
    data["closed"].append(closed_entry)
    _save(data)

    colour = _GREEN if pnl_pct >= 0 else _RED
    print(f"  ✓ {ticker} CLOSED @ ${exit_price:.2f}  "
          f"P&L: {colour}{pnl_pct:+.1f}% (${pnl_dol:+,.0f}){_RESET}")


def update_stop(ticker: str, new_stop: float):
    """Manually move stop loss up (stop can only go UP per Minervini rules)."""
    ticker = ticker.upper().strip()
    data   = _load()
    if ticker not in data["positions"]:
        print(f"  {ticker} not found in positions")
        return
    pos = data["positions"][ticker]
    old_stop = pos["stop_loss"]
    if new_stop < old_stop:
        print(f"  {_RED}ERROR: Stop can only move UP. "
              f"Old: ${old_stop:.2f}, Attempted: ${new_stop:.2f}{_RESET}")
        return
    pos["stop_loss"] = round(new_stop, 2)
    pos["stop_pct"]  = round((pos["buy_price"] - new_stop) / pos["buy_price"] * 100, 2)
    _save(data)
    print(f"  ✓ {ticker} stop updated: ${old_stop:.2f} → ${new_stop:.2f}")


# ═══════════════════════════════════════════════════════════════════════════════
# Daily health check
# ═══════════════════════════════════════════════════════════════════════════════

def daily_check(account_size: float = None):
    """
    Run Minervini's daily position health checklist (14.2) for all open positions.
    Also checks account-level drawdown (H1-H5).
    """
    data = _load()
    positions = data.get("positions", {})
    acct = account_size or C.ACCOUNT_SIZE

    if not positions:
        print("  No open positions.")
        return

    print(f"\n{'═'*65}")
    print(f"{_BOLD}  DAILY POSITION HEALTH CHECK  —  {date.today().isoformat()}{_RESET}")
    print(f"{'═'*65}")

    all_results = []
    total_value = 0.0
    total_cost  = 0.0

    for ticker, pos in positions.items():
        result = _check_position(ticker, pos)
        all_results.append(result)
        if result.get("current_price"):
            total_value += result["current_price"] * pos["shares"]
            total_cost  += pos["buy_price"] * pos["shares"]

    # ── Print individual results ──────────────────────────────────────────────
    for r in all_results:
        _print_position_health(r)

    # ── Recommended stop updates ──────────────────────────────────────────────
    updates = [(r["ticker"], r["recommended_stop"])
               for r in all_results
               if r.get("recommended_stop") and
               r["recommended_stop"] > data["positions"][r["ticker"]]["stop_loss"]]
    if updates:
        print(f"\n{_BOLD}  TRAILING STOP UPDATES RECOMMENDED:{_RESET}")
        for ticker, new_stop in updates:
            old_stop = data["positions"][ticker]["stop_loss"]
            print(f"  {ticker}: ${old_stop:.2f} → ${new_stop:.2f}  "
                  f"(run: positions update {ticker} {new_stop:.2f})")

    # ── Account-level P&L and drawdown ───────────────────────────────────────
    portfolio_pnl_pct = (total_value - total_cost) / total_cost * 100 \
                         if total_cost > 0 else 0
    print(f"\n{'─'*65}")
    colour = _GREEN if portfolio_pnl_pct >= 0 else _RED
    print(f"  {_BOLD}Portfolio P&L: {colour}{portfolio_pnl_pct:+.1f}%{_RESET}  "
          f"Positions: {len(positions)}/{C.MAX_OPEN_POSITIONS}")
    _check_drawdown(acct, data)

    # Save updated max_price / days_held
    for ticker, pos in positions.items():
        r = next((x for x in all_results if x["ticker"] == ticker), None)
        if r and r.get("current_price"):
            curr = r["current_price"]
            if curr > pos.get("max_price", 0):
                data["positions"][ticker]["max_price"] = round(curr, 2)
        # Update days held
        try:
            buy_d = date.fromisoformat(pos.get("buy_date", date.today().isoformat()))
            data["positions"][ticker]["days_held"] = (date.today() - buy_d).days
        except Exception:
            pass
    _save(data)

    print(f"{'═'*65}\n")


def _check_position(ticker: str, pos: dict) -> dict:
    """Run full health check for one position."""
    buy_price = pos["buy_price"]
    shares    = pos["shares"]
    stop      = pos["stop_loss"]
    target    = pos["target"]
    max_price = pos.get("max_price", buy_price)
    days_held = pos.get("days_held", 0)

    result = {
        "ticker":       ticker,
        "buy_price":    buy_price,
        "shares":       shares,
        "stop":         stop,
        "target":       target,
        "days_held":    days_held,
        "current_price":None,
        "pnl_pct":      None,
        "sell_signals": [],
        "recommended_stop": None,
        "health":       "UNKNOWN",
    }

    # Fetch current data
    try:
        df   = get_enriched(ticker, period="6mo")
        if df is None or df.empty:
            result["health"] = "NO DATA"
            return result

        last         = df.iloc[-1]
        current      = float(last["Close"])
        pnl_pct      = (current - buy_price) / buy_price * 100
        vol_today    = float(last["Volume"])
        avg_vol      = float(df["Volume"].tail(50).mean())
        rel_vol      = vol_today / avg_vol if avg_vol > 0 else 1.0

        result["current_price"] = round(current, 2)
        result["pnl_pct"]       = round(pnl_pct, 2)
        result["rel_vol"]       = round(rel_vol, 2)

        sma50  = df["Close"].rolling(50).mean().iloc[-1] if len(df) >= 50 else None
        sma20  = df["Close"].rolling(20).mean().iloc[-1] if len(df) >= 20 else None
        sma150 = df["Close"].rolling(150).mean().iloc[-1] if "SMA_150" not in df.columns \
                 else df["SMA_150"].iloc[-1] if len(df) >= 150 else None

        result["sma50"]  = round(float(sma50), 2) if sma50 and not np.isnan(sma50) else None
        result["sma20"]  = round(float(sma20), 2) if sma20 and not np.isnan(sma20) else None

        # ── Health checks (manual checklist 14.2) ────────────────────────────
        sell_signals = []

        # Distance from stop
        pct_from_stop = (current - stop) / current * 100
        result["pct_from_stop"] = round(pct_from_stop, 1)

        # 1. Stop loss triggered?
        if current <= stop:
            sell_signals.append(f"🔴 STOP HIT: Current ${current:.2f} ≤ Stop ${stop:.2f}")

        # 2. Breaking down through SMA50?
        if sma50 and current < float(sma50) * 0.99:
            sell_signals.append(f"⚠️  Price below SMA50 (${float(sma50):.2f})")

        # 3. High relative volume on down day?
        day_change = (current - float(last.get("Open", current))) / current * 100
        if rel_vol >= 2.0 and day_change < -1.0:
            sell_signals.append(
                f"⚠️  Distribution signal: down {day_change:.1f}% on {rel_vol:.1f}x vol")

        # 4. Climax top: price extended >25% above SMA50?
        if sma50 and float(sma50) > 0:
            pct_above_sma50 = (current - float(sma50)) / float(sma50) * 100
            result["pct_above_sma50"] = round(pct_above_sma50, 1)
            if pct_above_sma50 > 25:
                sell_signals.append(
                    f"⚠️  Over-extended: {pct_above_sma50:.1f}% above SMA50 "
                    f"(climax top risk)")

        # 5. New accounting: never let a winner become loser
        if pnl_pct >= 5.0 and current <= buy_price:
            sell_signals.append("⚠️  Winner turned to loss — consider exiting")

        # 6. Time stop checks
        if days_held >= 35 and pnl_pct < 5.0:
            sell_signals.append(
                f"⏰ Time stop: {days_held}d held, only {pnl_pct:+.1f}% gain")
        elif days_held >= 21 and pnl_pct < 0:
            sell_signals.append(
                f"⏰ Time stop: {days_held}d held, still losing {pnl_pct:.1f}%")

        result["sell_signals"] = sell_signals

        # ── Trailing stop recommendation ──────────────────────────────────────
        rec_stop = _calculate_trailing_stop(
            buy_price, current, max_price, stop, pnl_pct, df
        )
        result["recommended_stop"] = rec_stop

        # ── Overall health ────────────────────────────────────────────────────
        if any("STOP HIT" in s for s in sell_signals):
            result["health"] = "EXIT"
        elif len(sell_signals) >= 2:
            result["health"] = "DANGER"
        elif len(sell_signals) == 1 and "⚠️" in sell_signals[0]:
            result["health"] = "CAUTION"
        elif pnl_pct > 0:
            result["health"] = "OK"
        else:
            result["health"] = "WATCH"

    except Exception as exc:
        logger.warning(f"Health check error for {ticker}: {exc}")
        result["health"] = "ERROR"
        result["error"]  = str(exc)

    return result


def _calculate_trailing_stop(buy_price, current, max_price, current_stop,
                              pnl_pct, df) -> Optional[float]:
    """
    Calculate recommended trailing stop using Minervini's profit-pullback method (7.3).
    Only recommends if the new stop is HIGHER than the current stop.
    """
    if current <= buy_price:
        return None   # Not in profit — maintain original stop

    # Method 1: Profit-pullback / Minervini table
    max_pullback_pct = 0.0
    for (min_profit, max_pullback) in sorted(C.TRAILING_STOP_TABLE):
        if pnl_pct >= min_profit:
            max_pullback_pct = max_pullback

    if max_pullback_pct <= 0:
        # Move to break even once 5%+ profit
        if pnl_pct >= 5.0:
            new_stop = buy_price
        else:
            return None
    else:
        new_stop = max_price * (1 - max_pullback_pct / 100)

    # Method 2: SMA-based (use SMA20 as trailing stop for profits)
    if "SMA_20" in df.columns:
        sma20 = df["SMA_20"].dropna()
        if not sma20.empty:
            sma20_stop = float(sma20.iloc[-1]) * 0.99   # 1% cushion
            new_stop   = max(new_stop, sma20_stop)

    # Must be higher than current stop
    new_stop = round(new_stop, 2)
    return new_stop if new_stop > current_stop else None


def _check_drawdown(account_size: float, data: dict):
    """Check account-level drawdown and print action matrix."""
    positions = data.get("positions", {})
    account_high = data.get("account_high", account_size)

    # Estimate current account value
    current_equity = account_size
    for ticker, pos in positions.items():
        # This is an approximation — we'd need current prices for exact calc
        pass

    drawdown_pct = (account_high - current_equity) / account_high * 100 \
                   if account_high > 0 else 0

    colour = _GREEN
    action = "Normal trading"
    for threshold, act in C.DRAWDOWN_LEVELS:
        if drawdown_pct >= threshold:
            colour = _RED if threshold >= 7 else _YELLOW
            action = act.replace("_", " ").title()

    print(f"\n  {_BOLD}Account Drawdown:{_RESET} {colour}{drawdown_pct:.1f}%{_RESET}  "
          f"→  {action}")


def _print_position_health(r: dict):
    """Print health check result for one position."""
    ticker = r["ticker"]
    health = r.get("health", "UNKNOWN")
    pnl    = r.get("pnl_pct", 0) or 0
    curr   = r.get("current_price", "N/A")
    days   = r.get("days_held", 0)
    stop   = r.get("stop", 0)
    stop_dist = r.get("pct_from_stop", 0) or 0

    h_colour = (_GREEN if health == "OK" else
                _YELLOW if health in ("CAUTION", "WATCH") else
                _RED)
    p_colour = _GREEN if pnl >= 0 else _RED

    print(f"\n  {_BOLD}{ticker}{_RESET}  [{h_colour}{health}{_RESET}]")
    print(f"  Current: ${curr}  "
          f"P&L: {p_colour}{pnl:+.1f}%{_RESET}  "
          f"Days: {days}  "
          f"Stop: ${stop:.2f} ({stop_dist:+.1f}% cushion)")

    if r.get("recommended_stop"):
        print(f"  {_GREEN}→ Trailing stop recommendation: ${r['recommended_stop']:.2f}{_RESET}")

    for sig in r.get("sell_signals", []):
        print(f"  {sig}")


def list_positions():
    """Display all open positions."""
    data      = _load()
    positions = data.get("positions", {})
    if not positions:
        print("\n  No open positions.\n")
        return
    print(f"\n{'═'*65}")
    print(f"{_BOLD}  OPEN POSITIONS{_RESET}")
    print(f"{'─'*65}")
    print(f"  {'Ticker':<6}  {'Entry':>8}  {'Stop':>8}  "
          f"{'Target':>8}  {'R:R':>5}  {'Shares':>7}  "
          f"{'Risk $':>8}  {'Date':<12}  Note")
    print(f"  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*5}  "
          f"{'─'*7}  {'─'*8}  {'─'*12}  ────")
    for ticker, pos in sorted(positions.items()):
        print(f"  {ticker:<6}  ${pos['buy_price']:>7.2f}  "
              f"${pos['stop_loss']:>7.2f}  "
              f"${pos['target']:>7.2f}  "
              f"{pos['rr']:>5.1f}  "
              f"{pos['shares']:>7,}  "
              f"${pos['risk_dollar']:>7,.0f}  "
              f"{pos['buy_date']:<12}  "
              f"{pos.get('note', '')[:20]}")
    print(f"{'═'*65}\n")
