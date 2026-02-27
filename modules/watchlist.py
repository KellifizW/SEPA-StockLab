"""
modules/watchlist.py
──────────────────────
A/B/C Grade Watchlist Management  (Minervini Part 8.1)

Grade A: "Ready to trade"   — 3-8 stocks, analysis done, awaiting breakout
Grade B: "Close to ready"   — 10-20 stocks, passes TT but base not mature
Grade C: "Long-term track"  — 20-50 stocks, strong fundamentals, tech not ready

Storage: data/watchlist.json  (auto-created on first use)
"""

import sys
import json
import logging
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

GRADE_LIMITS = {"A": 8, "B": 20, "C": 50}
GRADE_LABELS = {
    "A": "Ready to Trade  (awaiting breakout)",
    "B": "Close to Ready  (base forming)",
    "C": "Long-term Track (monitoring)",
}

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
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"A": {}, "B": {}, "C": {}}


def _save(data: dict):
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Core operations
# ═══════════════════════════════════════════════════════════════════════════════

def add(ticker: str, grade: str = None, note: str = ""):
    """
    Add a ticker to the watchlist.
    If grade is None, auto-assigns grade based on SEPA score.
    """
    ticker = ticker.upper().strip()
    wl = _load()

    # Check if already in watchlist
    for g in "ABC":
        if ticker in wl[g]:
            print(f"  {ticker} already in Grade-{g} watchlist — updating...")
            break

    print(f"\nAdding {ticker} to watchlist...")
    # Quick analysis to determine grade
    try:
        df      = get_enriched(ticker, period="2y")
        rs      = get_rs_rank(ticker)
        snap    = get_snapshot(ticker)
        tt      = validate_trend_template(ticker, df=df, rs_rank=rs)
        vcp     = detect_vcp(df) if df is not None and not df.empty else {}

        if grade is None:
            grade = _auto_grade(tt, vcp, rs)

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
        }
        # Remove from other grades first
        for g in "ABC":
            wl[g].pop(ticker, None)
        wl[grade][ticker] = entry
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
                log_watchlist_action(ticker, "ADD", grade=grade,
                                     sepa_score=score, note=note)
            except Exception:
                pass

        g_colour = _GREEN if grade == "A" else _YELLOW if grade == "B" else _CYAN
        print(f"  ✓ {ticker} added to {g_colour}Grade-{grade}{_RESET}: {GRADE_LABELS[grade]}")
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
        }
        wl[grade or "C"][ticker] = entry
        _save(wl)
        
        # — DuckDB 異動日誌 ————————————————————
        if getattr(C, "DB_ENABLED", True):
            try:
                from modules.db import log_watchlist_action
                log_watchlist_action(ticker, "ADD", grade=grade or "C",
                                     sepa_score=None, note=note)
            except Exception:
                pass

        print(f"  Added {ticker} to Grade-{grade or 'C'} (no analysis data: {exc})")


def remove(ticker: str):
    """Remove a ticker from any watchlist grade."""
    ticker = ticker.upper().strip()
    wl = _load()
    removed = False
    removed_grade = None
    for g in "ABC":
        if ticker in wl[g]:
            del wl[g][ticker]
            removed = True
            removed_grade = g
            print(f"  ✓ Removed {ticker} from Grade-{g} watchlist")
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


def promote(ticker: str):
    """Manually promote a ticker to the next higher grade."""
    ticker = ticker.upper().strip()
    wl = _load()
    for g, next_g in [("C", "B"), ("B", "A")]:
        if ticker in wl[g]:
            entry = wl[g].pop(ticker)
            entry["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            wl[next_g][ticker] = entry
            _save(wl)
            print(f"  ✓ {ticker} promoted: Grade-{g} → Grade-{next_g}")
            return
    if ticker in wl["A"]:
        print(f"  {ticker} already at Grade-A (highest)")
    else:
        print(f"  {ticker} not in watchlist — use 'add' first")


def demote(ticker: str):
    """Manually demote a ticker to the next lower grade."""
    ticker = ticker.upper().strip()
    wl = _load()
    for g, next_g in [("A", "B"), ("B", "C")]:
        if ticker in wl[g]:
            entry = wl[g].pop(ticker)
            entry["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            wl[next_g][ticker] = entry
            _save(wl)
            print(f"  ✓ {ticker} demoted: Grade-{g} → Grade-{next_g}")
            return
    if ticker in wl["C"]:
        print(f"  {ticker} already at Grade-C (lowest)")
    else:
        print(f"  {ticker} not in watchlist — use 'add' first")


def list_all(verbose: bool = True):
    """Display the complete watchlist grouped by grade."""
    wl  = _load()
    sep = "─" * 60
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  MINERVINI WATCHLIST{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}")

    for g in "ABC":
        items    = wl[g]
        g_colour = _GREEN if g == "A" else _YELLOW if g == "B" else _CYAN
        print(f"\n  {g_colour}{_BOLD}Grade-{g}  —  {GRADE_LABELS[g]}  "
              f"({len(items)}/{GRADE_LIMITS[g]}){_RESET}")
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
            print(f"  {g_colour}{ticker:<6}{_RESET}  "
                  f"${str(price):<8}  RS:{str(rs):<5}  "
                  f"TT:{str(tt):<3}/10  VCP:{vcp_g}  "
                  f"{pivot_str:<18}  Added:{added}"
                  + (f"  [{note}]" if note else ""))

    total = sum(len(wl[g]) for g in "ABC")
    print(f"\n  Total: {total} stocks tracked")
    print(f"{'═'*60}\n")
    return wl


def refresh(verbose: bool = True):
    """
    Re-analyze all watchlist stocks and auto-reclassify grades.
    Prints a summary of changes.
    """
    wl = _load()
    total = sum(len(wl[g]) for g in "ABC")
    if total == 0:
        print("Watchlist is empty — nothing to refresh")
        return

    print(f"\nRefreshing {total} watchlist stocks...")
    all_tickers = {k: g for g in "ABC" for k in wl[g].keys()}
    changes = []

    for ticker, current_grade in all_tickers.items():
        print(f"  Updating {ticker}...", end="\r")
        try:
            df  = get_enriched(ticker, period="2y")
            rs  = get_rs_rank(ticker)
            tt  = validate_trend_template(ticker, df=df, rs_rank=rs)
            vcp = detect_vcp(df) if df is not None and not df.empty else {}

            new_grade = _auto_grade(tt, vcp, rs)

            # Update entry
            entry = wl[current_grade].get(ticker, {})
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
            })

            if new_grade != current_grade:
                wl[current_grade].pop(ticker, None)
                wl[new_grade][ticker] = entry
                changes.append((ticker, current_grade, new_grade))
            else:
                wl[current_grade][ticker] = entry

        except Exception as exc:
            logger.warning(f"Refresh error for {ticker}: {exc}")

    _save(wl)
    print(f"\n  ✓ Refresh complete")

    if changes:
        print(f"\n  Grade changes:")
        for ticker, old_g, new_g in changes:
            arrow = "⬆️" if new_g < old_g else "⬇️"
            print(f"    {arrow}  {ticker}: Grade-{old_g} → Grade-{new_g}")
    else:
        print("  No grade changes")

    list_all()


def get_grade_a_tickers() -> list:
    """Return list of Grade-A ticker symbols."""
    wl = _load()
    return list(wl["A"].keys())


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _auto_grade(tt: dict, vcp: dict, rs_rank: float) -> str:
    """
    Automatically assign A/B/C grade based on analysis results.
    Grade A: passes TT + valid VCP + RS ≥ 80
    Grade B: passes TT + RS ≥ 70 (base forming but not complete VCP)
    Grade C: everything else that clears basic criteria
    """
    tt_pass  = tt.get("passes", False)
    vcp_ok   = vcp.get("is_valid_vcp", False)
    vcp_grade = vcp.get("grade", "D")

    if tt_pass and vcp_ok and rs_rank >= C.TT9_IDEAL_RS_RANK:
        return "A"
    elif tt_pass and rs_rank >= C.TT9_MIN_RS_RANK:
        return "B"
    else:
        return "C"
