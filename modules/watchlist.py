"""
modules/watchlist.py
──────────────────────
Strategy-based Watchlist Management.

Primary buckets:
    • SEPA
    • QM
    • ML

Storage: data/watchlist.json  (auto-created on first use)
"""

import sys
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_enriched, get_snapshot
from modules.rs_ranking import get_rs_rank, RS_NOT_RANKED
from modules.screener import validate_trend_template, score_sepa_pillars, _get_atr
from modules.vcp_detector import detect_vcp

logger = logging.getLogger(__name__)

WATCHLIST_FILE = ROOT / C.DATA_DIR / "watchlist.json"

STRATEGY_KEYS = ("SEPA", "QM", "ML")
STRATEGY_LABELS = {
    "SEPA": "SEPA Watchlist",
    "QM": "Qullamaggie Watchlist",
    "ML": "Martin Luk Watchlist",
}

_LEGACY_GRADE_TO_STRATEGY = {"A": "SEPA", "B": "SEPA", "C": "SEPA"}

# ANSI colours
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════════════════

def _load() -> dict:
    """Load watchlist from JSON (source of truth; DuckDB is write-only analytics store)."""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            data = _normalize_watchlist(data)
            logger.debug("[watchlist] Loaded from JSON")
            return data
        except Exception as exc:
            logger.warning("[watchlist] JSON load failed: %s", exc)
    return _empty_watchlist()


def _normalize_market(market: Optional[str]) -> str:
    """Normalize market code to US/HK with safe fallback."""
    default_market = str(getattr(C, "MARKET_DEFAULT", "US")).upper().strip()
    m = (market or default_market or "US").upper().strip()
    return m if m in ("US", "HK") else "US"


def _save(data: dict):
    """Save watchlist: JSON sync (fast) + DuckDB async background thread."""
    if not data:
        return

    data = _normalize_watchlist(data)

    # 1. JSON write — synchronous, always first (source of truth)
    try:
        WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("[watchlist] Saved to JSON")
    except Exception as exc:
        logger.warning("[watchlist] JSON save failed: %s", exc)

    # 2. DuckDB write — fire-and-forget background thread (non-blocking)
    if C.DB_ENABLED:
        threading.Thread(
            target=_bg_wl_db_save,
            args=(data.copy(),),
            daemon=True,
            name="wl_db_save",
        ).start()


def _bg_wl_db_save(data: dict):
    """Background worker: sync watchlist to DuckDB watchlist_store."""
    try:
        from modules import db
        db.wl_save(data)
        logger.debug("[watchlist] Background DuckDB sync complete")
    except Exception as exc:
        logger.warning("[watchlist] Background DuckDB sync failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Core operations
# ═══════════════════════════════════════════════════════════════════════════════

def add(ticker: str, grade: Optional[str] = None, note: str = "", market: str = "US"):
    """
    Add a ticker to the watchlist.
    `grade` is kept for backward compatibility and mapped to strategy buckets.
    """
    ticker = ticker.upper().strip()
    wl = _load()
    strategy = _normalize_strategy(grade)
    market = _normalize_market(market)

    # Check if already in watchlist
    for s in STRATEGY_KEYS:
        if ticker in wl[s]:
            print(f"  {ticker} already in {s} watchlist — updating...")
            break

    print(f"\nAdding {ticker} to watchlist...")
    # Quick analysis to determine grade
    try:
        df      = get_enriched(ticker, period="2y")
        rs      = get_rs_rank(ticker)
        snap    = get_snapshot(ticker)
        tt      = validate_trend_template(ticker, df=df, rs_rank=rs)
        vcp     = detect_vcp(df) if df is not None and not df.empty else {}

        if strategy is None:
            strategy = _auto_strategy(tt, vcp, rs)

        entry = {
            "added_date":   datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "rs_rank":      round(rs, 1) if rs != RS_NOT_RANKED else None,
            "tt_score":     tt.get("score", 0),
            "tt_passes":    tt.get("passes", False),
            "vcp_grade":    vcp.get("grade", "D"),
            "vcp_score":    vcp.get("vcp_score", 0),
            "t_count":      vcp.get("t_count", 0),
            "pivot":        vcp.get("pivot_price"),
            "price":        round(df.iloc[-1]["Close"], 2) if df is not None and not df.empty else None,
            "sector":       snap.get("Sector", ""),
            "note":         note,
            "strategy":     strategy,
            "market":       market,
        }
        # Remove from other buckets first
        for s in STRATEGY_KEYS:
            wl[s].pop(ticker, None)
        wl[strategy][ticker] = entry
        _save(wl)

        # — DuckDB 異動日誌 ————————————————────
        if getattr(C, "DB_ENABLED", True):
            try:
                from modules.db import log_watchlist_action
                score = tt.get("score", 0)
                try:
                    score = float(score)
                except (ValueError, TypeError):
                    score = None
                log_watchlist_action(ticker, "ADD", grade=strategy,
                                     sepa_score=score, note=note)
            except Exception:
                pass

            s_colour = _GREEN if strategy == "SEPA" else _YELLOW if strategy == "QM" else _CYAN
            print(f"  ✓ {ticker} added to {s_colour}{strategy}{_RESET}: {STRATEGY_LABELS[strategy]}")
        print(f"    TT: {tt.get('score', 0)}/10  RS: {rs:.0f}  "
              f"VCP: {vcp.get('grade', 'D')} (score {vcp.get('vcp_score', 0)})")
    except Exception as exc:
        logger.warning(f"Error adding {ticker}: {exc}")
        # Add without analysis data
        entry = {
            "added_date":   datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "note":         note,
            "error":        str(exc),
            "market":       market,
        }
        strategy = strategy or "SEPA"
        entry["strategy"] = strategy
        wl[strategy][ticker] = entry
        _save(wl)
        
        # — DuckDB 異動日誌 ————————————————————
        if getattr(C, "DB_ENABLED", True):
            try:
                from modules.db import log_watchlist_action
                log_watchlist_action(ticker, "ADD", grade=strategy,
                                     sepa_score=None, note=note)
            except Exception:
                pass

            print(f"  Added {ticker} to {strategy} (no analysis data: {exc})")


def remove(ticker: str, market: Optional[str] = None):
    """Remove a ticker from any watchlist grade."""
    ticker = ticker.upper().strip()
    target_market = _normalize_market(market) if market else None
    wl = _load()
    removed = False
    removed_grade = None
    for s in STRATEGY_KEYS:
        if ticker in wl[s]:
            entry_market = _normalize_market((wl[s][ticker] or {}).get("market"))
            if target_market and entry_market != target_market:
                continue
            del wl[s][ticker]
            removed = True
            removed_grade = s
            print(f"  ✓ Removed {ticker} from {s} watchlist")
    if not removed:
        print(f"  {ticker} not found in watchlist")
    else:
        _save(wl)
        
        # — DuckDB 異動日誌 ————————————————————
        if getattr(C, "DB_ENABLED", True):
            try:
                from modules.db import log_watchlist_action
                log_watchlist_action(ticker, "REMOVE", grade=removed_grade)
            except Exception:
                pass


def promote(ticker: str, market: Optional[str] = None):
    """Backward-compatible wrapper: move ticker to next strategy bucket."""
    ticker = ticker.upper().strip()
    target_market = _normalize_market(market) if market else None
    wl = _load()
    order = ["SEPA", "QM", "ML"]
    for idx, s in enumerate(order):
        if ticker in wl[s]:
            entry_market = _normalize_market((wl[s][ticker] or {}).get("market"))
            if target_market and entry_market != target_market:
                continue
            if idx == len(order) - 1:
                print(f"  {ticker} already at {s}")
                return
            move_to_strategy(ticker, order[idx + 1], market=target_market)
            return
    print(f"  {ticker} not in watchlist — use 'add' first")


def demote(ticker: str, market: Optional[str] = None):
    """Backward-compatible wrapper: move ticker to previous strategy bucket."""
    ticker = ticker.upper().strip()
    target_market = _normalize_market(market) if market else None
    wl = _load()
    order = ["SEPA", "QM", "ML"]
    for idx, s in enumerate(order):
        if ticker in wl[s]:
            entry_market = _normalize_market((wl[s][ticker] or {}).get("market"))
            if target_market and entry_market != target_market:
                continue
            if idx == 0:
                print(f"  {ticker} already at {s}")
                return
            move_to_strategy(ticker, order[idx - 1], market=target_market)
            return
    print(f"  {ticker} not in watchlist — use 'add' first")


def move_to_strategy(ticker: str, strategy: str, market: Optional[str] = None):
    """Move ticker to target strategy bucket (SEPA/QM/ML)."""
    ticker = ticker.upper().strip()
    strategy = _normalize_strategy(strategy) or "SEPA"
    target_market = _normalize_market(market) if market else None
    wl = _load()

    for s in STRATEGY_KEYS:
        if ticker in wl[s]:
            entry = wl[s].pop(ticker)
            entry_market = _normalize_market((entry or {}).get("market"))
            if target_market and entry_market != target_market:
                wl[s][ticker] = entry
                continue
            entry["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            entry["strategy"] = strategy
            wl[strategy][ticker] = entry
            _save(wl)
            print(f"  ✓ {ticker} moved: {s} → {strategy}")
            return
    print(f"  {ticker} not in watchlist — use 'add' first")


def list_all(verbose: bool = True):
    """Display the complete watchlist grouped by strategy."""
    wl  = _load()
    sep = "─" * 60
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  MINERVINI WATCHLIST{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}")

    for s in STRATEGY_KEYS:
        items    = wl[s]
        s_colour = _GREEN if s == "SEPA" else _YELLOW if s == "QM" else _CYAN
        print(f"\n  {s_colour}{_BOLD}{s}  —  {STRATEGY_LABELS[s]}  "
              f"({len(items)}){_RESET}")
        print(f"  {sep}")
        if not items:
            print("  (empty)")
            continue
        for ticker, data in sorted(items.items()):
            rs        = data.get("rs_rank", "—")
            tt        = data.get("tt_score", "—")
            vcp_g     = data.get("vcp_grade", "—")
            price     = data.get("price", "—")
            pivot     = data.get("pivot")
            added     = data.get("added_date", "—")
            note      = data.get("note", "")
            pivot_str = f"Pivot: ${pivot:.2f}" if pivot else ""
            print(f"  {s_colour}{ticker:<6}{_RESET}  "
                f"${str(price):<8}  RS:{str(rs):<5}  "
                f"TT:{str(tt):<3}/10  VCP:{vcp_g}  "
                f"{pivot_str:<18}  Added:{added}"
                + (f"  [{note}]" if note else ""))

        total = sum(len(wl[s]) for s in STRATEGY_KEYS)
    print(f"\n  Total: {total} stocks tracked")
    print(f"{'═'*60}\n")
    return wl


def refresh(verbose: bool = True):
    """
    Re-analyze all watchlist stocks and auto-reclassify grades.
    Prints a summary of changes.
    """
    wl = _load()
    total = sum(len(wl[s]) for s in STRATEGY_KEYS)
    if total == 0:
        print("Watchlist is empty — nothing to refresh")
        return

    print(f"\nRefreshing {total} watchlist stocks...")
    all_tickers = {k: s for s in STRATEGY_KEYS for k in wl[s].keys()}
    changes = []

    for ticker, current_strategy in all_tickers.items():
        print(f"  Updating {ticker}...", end="\r")
        try:
            df  = get_enriched(ticker, period="2y")
            rs  = get_rs_rank(ticker)
            tt  = validate_trend_template(ticker, df=df, rs_rank=rs)
            vcp = detect_vcp(df) if df is not None and not df.empty else {}

            new_strategy = current_strategy

            # Update entry
            entry = wl[current_strategy].get(ticker, {})
            entry.update({
                "last_updated": datetime.now().strftime("%Y-%m-%d"),
                "rs_rank":      round(rs, 1) if rs != RS_NOT_RANKED else None,
                "tt_score":     tt.get("score", 0),
                "tt_passes":    tt.get("passes", False),
                "vcp_grade":    vcp.get("grade", "D"),
                "vcp_score":    vcp.get("vcp_score", 0),
                "t_count":      vcp.get("t_count", 0),
                "pivot":        vcp.get("pivot_price"),
                "price":        round(float(df.iloc[-1]["Close"]), 2)
                                if df is not None and not df.empty else None,
                "strategy":     current_strategy,
            })

            if new_strategy != current_strategy:
                wl[current_strategy].pop(ticker, None)
                wl[new_strategy][ticker] = entry
                changes.append((ticker, current_strategy, new_strategy))
            else:
                wl[current_strategy][ticker] = entry

        except Exception as exc:
            logger.warning(f"Refresh error for {ticker}: {exc}")

    _save(wl)
    print(f"\n  ✓ Refresh complete")

    if changes:
        print(f"\n  Strategy changes:")
        for ticker, old_s, new_s in changes:
            print(f"    {ticker}: {old_s} → {new_s}")
    else:
        print("  No strategy changes")

    list_all()


def get_grade_a_tickers() -> list:
    """Backward-compatible helper: return SEPA strategy ticker symbols."""
    wl = _load()
    return list(wl["SEPA"].keys())


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _auto_strategy(tt: dict, vcp: dict, rs_rank: float) -> str:
    """
    Default strategy assignment for new entries when caller does not specify.
    Existing behavior keeps entries in SEPA bucket by default.
    """
    return "SEPA"


def _empty_watchlist() -> dict:
    return {k: {} for k in STRATEGY_KEYS}


def _normalize_strategy(strategy: Optional[str]) -> Optional[str]:
    if strategy is None:
        return None
    s = str(strategy).upper().strip()
    if s in STRATEGY_KEYS:
        return s
    return _LEGACY_GRADE_TO_STRATEGY.get(s)


def _normalize_watchlist(data: dict) -> dict:
    """Normalize watchlist shape and migrate legacy A/B/C structure."""
    out = _empty_watchlist()
    if not isinstance(data, dict):
        return out

    # Legacy shape: {"A": {...}, "B": {...}, "C": {...}}
    if any(k in data for k in ("A", "B", "C")):
        for grade, target in _LEGACY_GRADE_TO_STRATEGY.items():
            bucket = data.get(grade, {}) or {}
            if not isinstance(bucket, dict):
                continue
            for ticker, entry in bucket.items():
                e = dict(entry or {})
                e["strategy"] = target
                e["market"] = _normalize_market(e.get("market"))
                out[target][ticker] = e

    # New shape: {"SEPA": {...}, "QM": {...}, "ML": {...}}
    for s in STRATEGY_KEYS:
        bucket = data.get(s, {}) or {}
        if not isinstance(bucket, dict):
            continue
        for ticker, entry in bucket.items():
            e = dict(entry or {})
            e["strategy"] = s
            e["market"] = _normalize_market(e.get("market"))
            out[s][ticker] = e

    return out


def filter_by_market(wl: dict, market: str) -> dict:
    """Return a strategy-grouped watchlist filtered by market code."""
    target = _normalize_market(market)
    src = _normalize_watchlist(wl)
    out = _empty_watchlist()
    for s in STRATEGY_KEYS:
        for ticker, entry in src.get(s, {}).items():
            e = dict(entry or {})
            if _normalize_market(e.get("market")) != target:
                continue
            out[s][ticker] = e
    return out
