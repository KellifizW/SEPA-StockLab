"""
modules/auto_trader.py  —  Automated Buy Execution Engine (QM + ML strategy switching)
═══════════════════════════════════════════════════════════════════════════════
5-Phase pipeline:
  Phase 0  Load latest scan results (combined_last_scan.json / individual JSONs)
  Phase 1  Market-regime gate — block strategies not allowed in the current regime
  Phase 2  Pre-filter candidates by screener star rating ≥ threshold
  Phase 3  Deep analysis — run analyze_qm() / analyze_ml() + watch-score evaluation
  Phase 4  Position sizing — calc_qm_position_size / calc_ml_position_size
  Phase 5  Order execution via ibkr_client.place_order() (or dry-run log)

Safety:
  • AUTO_TRADE_ENABLED must be True AND AUTO_TRADE_DRY_RUN=False to place real orders
  • Same-ticker cooldown (default 86400s = 1 day) prevents duplicate buys
  • Max buys per day cap
  • Iron-rule vetoes from watch-mode block entry
  • All actions logged to DuckDB auto_trade_log

Polling:
  The engine is designed to be called repeatedly (every POLL_INTERVAL_SEC = 300s).
  A background thread in ``start()`` handles the loop; ``stop()`` cancels it.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────
_lock          = threading.Lock()
_cancel_event  = threading.Event()
_running       = False
_thread: Optional[threading.Thread] = None

_status: dict = {
    "running":       False,
    "dry_run":       True,
    "cycle":         0,
    "last_cycle_at": None,
    "current_phase": "idle",
    "phase_desc":    "等待啟動 / Waiting to start",
    "current_strategy": "",
    "current_ticker": "",
    "phase_progress_pct": 0,
    "processed_candidates": 0,
    "total_candidates": 0,
    "regime": "UNKNOWN",
    "buys_today":    0,
    "candidates":    [],
    "last_actions":  [],
    "error":         None,
}

# ── File paths ────────────────────────────────────────────────────────────────
_DATA_DIR              = ROOT / "data"
_COMBINED_LAST_FILE    = _DATA_DIR / "combined_last_scan.json"
_QM_LAST_FILE          = _DATA_DIR / "qm_last_scan.json"
_ML_LAST_FILE          = _DATA_DIR / "ml_last_scan.json"

# Track which tickers were already bought today (ticker → timestamp)
_bought_today: dict[str, float] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Public API — start / stop / status
# ═══════════════════════════════════════════════════════════════════════════════

def get_status() -> dict:
    """Return a snapshot of the auto-trade status dict (includes exit engine)."""
    with _lock:
        s = dict(_status)
    # Merge exit engine status
    try:
        from modules.exit_engine import get_exit_status
        s["exit_engine"] = get_exit_status()
    except Exception:
        s["exit_engine"] = None
    return s


def _update_status(**kwargs) -> None:
    """Thread-safe partial update for runtime status fields."""
    with _lock:
        _status.update(kwargs)


def start(dry_run: Optional[bool] = None) -> dict:
    """
    Start the auto-trade polling loop in a background thread.

    Args:
        dry_run: Override AUTO_TRADE_DRY_RUN if provided.

    Returns:
        {"ok": bool, "message": str}
    """
    global _running, _thread

    if dry_run is None:
        dry_run = C.AUTO_TRADE_DRY_RUN

    with _lock:
        if _running:
            return {"ok": False, "message": "Auto-trade already running"}
        _cancel_event.clear()
        _running = True
        _status.update({
            "running": True,
            "dry_run": dry_run,
            "cycle":   0,
            "current_phase": "starting",
            "phase_desc": "啟動中 / Starting",
            "current_strategy": "",
            "current_ticker": "",
            "phase_progress_pct": 0,
            "processed_candidates": 0,
            "total_candidates": 0,
            "regime": "UNKNOWN",
            "error":   None,
        })

    _thread = threading.Thread(
        target=_poll_loop,
        args=(dry_run,),
        daemon=True,
        name="auto-trader",
    )
    _thread.start()
    mode = "DRY-RUN 模擬" if dry_run else "LIVE 實盤"
    logger.info("[AutoTrade] Started (%s), poll every %ds", mode, C.AUTO_TRADE_POLL_INTERVAL_SEC)
    return {"ok": True, "message": f"Auto-trade started ({mode})"}


def stop() -> dict:
    """Stop the polling loop gracefully."""
    global _running, _thread
    with _lock:
        if not _running:
            return {"ok": False, "message": "Auto-trade not running"}
        _cancel_event.set()
        _running = False
        _status["running"] = False
        _status["current_phase"] = "stopped"
        _status["phase_desc"] = "已停止 / Stopped"

    if _thread is not None:
        _thread.join(timeout=10)
        _thread = None

    logger.info("[AutoTrade] Stopped")
    return {"ok": True, "message": "Auto-trade stopped"}


# ═══════════════════════════════════════════════════════════════════════════════
# Polling loop (runs in background thread)
# ═══════════════════════════════════════════════════════════════════════════════

def _poll_loop(dry_run: bool):
    """Runs until _cancel_event is set, executing buy + exit cycles per interval."""
    global _running

    poll_sec = max(C.AUTO_TRADE_POLL_INTERVAL_SEC, 30)  # floor 30s
    exit_sec = max(getattr(C, "EXIT_CHECK_INTERVAL_SEC", 60), 15)
    exit_enabled = getattr(C, "EXIT_ENGINE_ENABLED", False)
    exit_dry_run = getattr(C, "EXIT_DRY_RUN", True)

    logger.info("[AutoTrade] Poll loop started, buy_interval=%ds, exit_interval=%ds, exit=%s",
                poll_sec, exit_sec, "ON" if exit_enabled else "OFF")

    last_exit_check = 0.0

    while not _cancel_event.is_set():
        try:
            _run_one_cycle(dry_run)
        except Exception as exc:
            logger.exception("[AutoTrade] Cycle error: %s", exc)
            with _lock:
                _status["error"] = str(exc)

        # Wait for next buy cycle, but run exit checks at exit_sec intervals
        elapsed = 0
        while elapsed < poll_sec and not _cancel_event.is_set():
            time.sleep(5)
            elapsed += 5

            # Run exit engine at its own interval
            if exit_enabled and (time.time() - last_exit_check) >= exit_sec:
                _run_exit_check(exit_dry_run if dry_run else exit_dry_run)
                last_exit_check = time.time()

    with _lock:
        _running = False
        _status["running"] = False
        _status["current_phase"] = "idle"
        _status["phase_desc"] = "等待下一輪 / Waiting for next cycle"
        _status["current_strategy"] = ""
        _status["current_ticker"] = ""
    logger.info("[AutoTrade] Poll loop exited")


def _run_exit_check(dry_run: bool):
    """Run the exit engine to check all open positions for sell signals."""
    try:
        from modules.exit_engine import check_all_positions
        _update_status(
            current_phase="exit_check",
            phase_desc="Exit Check: 檢查持倉 / Checking positions",
        )
        results = check_all_positions(dry_run=dry_run)
        exits = [r for r in results if r.get("action_taken") not in ("HOLD", "ERROR", None)]
        if exits:
            logger.info("[ExitEngine] %d exit(s) executed this check", len(exits))
    except Exception as exc:
        logger.warning("[AutoTrade] Exit check failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Core pipeline — one execution cycle
# ═══════════════════════════════════════════════════════════════════════════════

def _run_one_cycle(dry_run: bool):
    """Execute one full auto-trade cycle (Phases 0-5)."""
    from modules import db

    cycle_start = datetime.now()
    today_str = date.today().isoformat()

    with _lock:
        _status["cycle"] += 1
        _status["last_cycle_at"] = cycle_start.isoformat()
        _status["error"] = None
        _status["current_phase"] = "phase0"
        _status["phase_desc"] = "Phase 0: 載入候選清單 / Loading candidates"
        _status["phase_progress_pct"] = 5
        _status["processed_candidates"] = 0
        _status["total_candidates"] = 0
        _status["current_strategy"] = ""
        _status["current_ticker"] = ""
        cycle_num = _status["cycle"]

    logger.info("[AutoTrade] ── Cycle %d start ──", cycle_num)

    # ── Reset daily counters at midnight ───────────────────────────────────
    _reset_daily_counters()

    # ── Phase 0: Load latest scan results ─────────────────────────────────
    qm_candidates, ml_candidates, market_env = _phase0_load_scan_results()
    regime = market_env.get("regime", "UNKNOWN") if market_env else "UNKNOWN"
    _update_status(regime=regime, total_candidates=len(qm_candidates) + len(ml_candidates))
    logger.info("[AutoTrade] Phase 0: QM=%d, ML=%d candidates, regime=%s",
                len(qm_candidates), len(ml_candidates), regime)

    # ── Phase 1: Market-regime gate ───────────────────────────────────────
    qm_allowed = regime in C.AUTO_QM_REGIMES_ENABLED
    ml_allowed = regime in C.AUTO_ML_REGIMES_ENABLED
    _update_status(
        current_phase="phase1",
        phase_desc=f"Phase 1: 市場過濾 / Regime gate ({regime})",
        phase_progress_pct=15,
    )

    if not qm_allowed:
        logger.info("[AutoTrade] Phase 1: QM BLOCKED — regime %s not in allowed list", regime)
        for c in qm_candidates:
            _log_action(db, c, "QM", 0, 0, regime, "BLOCKED",
                        f"Regime {regime} not allowed for QM", dry_run)
        qm_candidates = []

    if not ml_allowed:
        logger.info("[AutoTrade] Phase 1: ML BLOCKED — regime %s not in allowed list", regime)
        for c in ml_candidates:
            _log_action(db, c, "ML", 0, 0, regime, "BLOCKED",
                        f"Regime {regime} not allowed for ML", dry_run)
        ml_candidates = []

    # ── Phase 2: Pre-filter by screener star rating ───────────────────────
    qm_filtered = _phase2_prefilter(qm_candidates, "QM", regime, db, dry_run)
    ml_filtered = _phase2_prefilter(ml_candidates, "ML", regime, db, dry_run)
    _update_status(
        current_phase="phase2",
        phase_desc=(
            f"Phase 2: 星級過濾 / Star filter (QM {len(qm_filtered)} / ML {len(ml_filtered)})"
        ),
        phase_progress_pct=30,
    )
    logger.info("[AutoTrade] Phase 2: QM=%d, ML=%d after star filter",
                len(qm_filtered), len(ml_filtered))

    # ── Phase 3: Deep analysis + watch score ──────────────────────────────
    actions = []
    remaining_buys = C.AUTO_TRADE_MAX_BUYS_PER_DAY - _count_buys_today(db, today_str)

    if remaining_buys <= 0:
        logger.info("[AutoTrade] Phase 3: Daily buy limit reached (%d), skipping",
                    C.AUTO_TRADE_MAX_BUYS_PER_DAY)
    else:
        # Process QM candidates first (breakout = more time-sensitive)
        qm_limit = _max_candidates_for_strategy("QM")
        ml_limit = _max_candidates_for_strategy("ML")
        total_to_process = min(len(qm_filtered), qm_limit) + min(len(ml_filtered), ml_limit)
        processed = 0
        _update_status(
            current_phase="phase3",
            phase_desc="Phase 3: 深度分析 / Deep analysis",
            total_candidates=total_to_process,
            phase_progress_pct=40,
        )
        for cand in qm_filtered[:qm_limit]:
            if remaining_buys <= 0:
                break
            if _cancel_event.is_set():
                break
            processed += 1
            _update_status(
                current_strategy="QM",
                current_ticker=cand.get("ticker", ""),
                processed_candidates=processed,
                phase_progress_pct=min(75, 40 + int((processed / max(total_to_process, 1)) * 35)),
                phase_desc=(
                    f"Phase 3: 分析 QM ({processed}/{total_to_process})"
                ),
            )
            result = _phase3_analyze_and_decide(cand, "QM", regime, dry_run, db)
            actions.append(result)
            if result.get("action") == "BUY":
                remaining_buys -= 1

        # Process ML candidates
        for cand in ml_filtered[:ml_limit]:
            if remaining_buys <= 0:
                break
            if _cancel_event.is_set():
                break
            processed += 1
            _update_status(
                current_strategy="ML",
                current_ticker=cand.get("ticker", ""),
                processed_candidates=processed,
                phase_progress_pct=min(75, 40 + int((processed / max(total_to_process, 1)) * 35)),
                phase_desc=(
                    f"Phase 3: 分析 ML ({processed}/{total_to_process})"
                ),
            )
            result = _phase3_analyze_and_decide(cand, "ML", regime, dry_run, db)
            actions.append(result)
            if result.get("action") == "BUY":
                remaining_buys -= 1

    # ── ML Scale-in check (EntryPointControl §四 Difference 4) ─────────────
    if ml_allowed and getattr(C, "ML_SCALED_ENTRY_ENABLED", True):
        try:
            scalein_actions = _check_ml_scalein_candidates(db, regime, dry_run)
            if scalein_actions:
                logger.info("[AutoTrade] ML scale-in: %d positions upgraded",
                            len(scalein_actions))
                actions.extend([{
                    "ticker": a["ticker"], "strategy": "ML",
                    "action": "SCALE_IN", "reason": a["action"],
                    "stars": 0, "watch_score": a.get("watch_score", 0),
                } for a in scalein_actions])
        except Exception as exc:
            logger.debug("[AutoTrade] ML scale-in check error: %s", exc)

    # ── Update status ─────────────────────────────────────────────────────
    with _lock:
        _status["current_phase"] = "complete"
        _status["phase_desc"] = "Cycle 完成 / Cycle complete"
        _status["phase_progress_pct"] = 100
        _status["current_strategy"] = ""
        _status["current_ticker"] = ""
        _status["buys_today"] = C.AUTO_TRADE_MAX_BUYS_PER_DAY - remaining_buys
        _status["candidates"] = [
            {"ticker": a["ticker"], "strategy": a["strategy"],
             "action": a["action"], "stars": a.get("stars", 0)}
            for a in actions
        ]
        _status["last_actions"] = actions[-10:]  # Keep last 10

    elapsed = (datetime.now() - cycle_start).total_seconds()
    logger.info("[AutoTrade] ── Cycle %d done (%.1fs) — %d actions ──",
                cycle_num, elapsed, len(actions))


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 0 — Load scan results
# ═══════════════════════════════════════════════════════════════════════════════

def _phase0_load_scan_results() -> tuple[list, list, dict]:
    """
    Load the latest QM and ML scan results from JSON files.

    Returns:
        (qm_rows, ml_rows, market_env_dict)
    """
    qm_rows = []
    ml_rows = []
    market_env = {}

    # Try combined scan first (has both QM + ML + market_env)
    try:
        if _COMBINED_LAST_FILE.exists():
            data = json.loads(_COMBINED_LAST_FILE.read_text(encoding="utf-8"))
            qm_rows = data.get("qm_rows", [])
            ml_rows = data.get("ml_rows", [])
            market_env = data.get("market_env", {})
            logger.info("[AutoTrade P0] Loaded combined: QM=%d ML=%d", len(qm_rows), len(ml_rows))
    except Exception as exc:
        logger.warning("[AutoTrade P0] Failed to load combined scan: %s", exc)

    # Fallback: individual QM/ML scan files
    if not qm_rows:
        try:
            if _QM_LAST_FILE.exists():
                data = json.loads(_QM_LAST_FILE.read_text(encoding="utf-8"))
                qm_rows = data.get("results", data.get("qm_rows", []))
        except Exception as exc:
            logger.warning("[AutoTrade P0] Failed to load QM scan: %s", exc)

    if not ml_rows:
        try:
            if _ML_LAST_FILE.exists():
                data = json.loads(_ML_LAST_FILE.read_text(encoding="utf-8"))
                ml_rows = data.get("results", data.get("ml_rows", []))
        except Exception as exc:
            logger.warning("[AutoTrade P0] Failed to load ML scan: %s", exc)

    # If market_env not from combined, assess fresh
    if not market_env or "regime" not in market_env:
        try:
            from modules.market_env import assess
            market_env = assess(verbose=False)
        except Exception as exc:
            logger.warning("[AutoTrade P0] market_env.assess failed: %s", exc)
            market_env = {"regime": "UNKNOWN"}

    return qm_rows, ml_rows, market_env


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Pre-filter by screener star rating
# ═══════════════════════════════════════════════════════════════════════════════

def _phase2_prefilter(candidates: list, strategy: str, regime: str,
                      db, dry_run: bool) -> list:
    """
    Filter scan result rows by minimum star rating and cooldown.
    Returns sorted list (best stars first), capped at MAX_CANDIDATES.
    """
    if strategy == "QM":
        min_star = C.AUTO_TRADE_MIN_STAR_QM
        star_key = "qm_star"
    else:
        min_star = C.AUTO_TRADE_MIN_STAR_ML
        star_key = "ml_star"

    passed = []
    for row in candidates:
        ticker = row.get("ticker", "")
        stars = float(row.get(star_key) or row.get("stars") or 0)

        # Star gate
        if stars < min_star:
            continue

        # Same-day cooldown
        if _is_on_cooldown(ticker):
            _log_action(db, row, strategy, stars, 0, regime, "SKIP",
                        f"Cooldown active (bought today)", dry_run)
            continue

        passed.append(row)

    # Sort by stars descending
    passed.sort(
        key=lambda r: float(r.get(star_key) or r.get("stars") or 0),
        reverse=True,
    )
    return passed[:_max_candidates_for_strategy(strategy)]


def _max_candidates_for_strategy(strategy: str) -> int:
    """Return per-strategy candidate cap, with fallback to legacy global setting."""
    fallback = int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES", 10))
    if strategy == "QM":
        return max(1, int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES_QM", fallback)))
    return max(1, int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES_ML", fallback)))


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Deep analysis + watch score + decision
# ═══════════════════════════════════════════════════════════════════════════════

def _phase3_analyze_and_decide(row: dict, strategy: str, regime: str,
                               dry_run: bool, db) -> dict:
    """
    Run deep analysis and watch-score evaluation for a single candidate.
    Returns an action dict (BUY / SKIP / BLOCKED).
    """
    ticker = row.get("ticker", "")
    star_key = "qm_star" if strategy == "QM" else "ml_star"
    screener_stars = float(row.get(star_key) or row.get("stars") or 0)

    result = {
        "ticker": ticker,
        "strategy": strategy,
        "screener_stars": screener_stars,
        "stars": 0.0,
        "watch_score": 0,
        "action": "SKIP",
        "reason": "",
        "iron_rules": [],
        "dim_summary": {},
        "order_result": None,
    }

    try:
        # ── Phase 3A: Deep analysis (analyze_qm / analyze_ml) ────────────
        analysis = _run_deep_analysis(ticker, strategy, market_regime=regime)
        if not analysis or analysis.get("error"):
            result["reason"] = f"Analysis failed: {analysis.get('error', 'unknown')}"
            _log_action(db, row, strategy, screener_stars, 0, regime,
                        "SKIP", result["reason"], dry_run)
            return result

        stars = float(analysis.get("capped_stars") or analysis.get("stars") or 0)
        result["stars"] = stars

        # Collect dimension summary
        dim_scores = analysis.get("dim_scores", {})
        result["dim_summary"] = {
            k: {"score": v.get("score", 0), "label": v.get("label", "")}
            for k, v in dim_scores.items()
            if isinstance(v, dict)
        }

        # Check veto from deep analysis (ADR, MARKET, WEEKLY, RISK)
        veto = analysis.get("veto")
        if veto:
            result["action"] = "BLOCKED"
            result["reason"] = f"Veto: {veto}"
            _log_action(db, row, strategy, stars, 0, regime,
                        "BLOCKED", result["reason"], dry_run)
            return result

        # ML specific: decision tree check
        if strategy == "ML":
            dt = analysis.get("decision_tree", {})
            dt_verdict = dt.get("verdict", "NO-GO")
            if dt_verdict == "NO-GO":
                result["action"] = "SKIP"
                result["reason"] = f"ML Decision Tree: NO-GO ({dt.get('pass_count', 0)}/9)"
                _log_action(db, row, strategy, stars, 0, regime,
                            "SKIP", result["reason"], dry_run)
                return result

        # Minimum star gate from deep analysis
        min_star = C.AUTO_TRADE_MIN_STAR_QM if strategy == "QM" else C.AUTO_TRADE_MIN_STAR_ML
        if stars < min_star:
            result["action"] = "SKIP"
            result["reason"] = f"Stars {stars:.1f} < min {min_star}"
            _log_action(db, row, strategy, stars, 0, regime,
                        "SKIP", result["reason"], dry_run)
            return result

        # ── Phase 3B: Watch-score evaluation ──────────────────────────────
        watch = _evaluate_watch_score(ticker, strategy, analysis)
        watch_score = watch.get("watch_score", 0)
        iron_rules = watch.get("iron_rules", [])
        result["watch_score"] = watch_score
        result["iron_rules"] = iron_rules

        # Iron-rule block check
        blocking_rules = [r for r in iron_rules if r.get("severity") == "block"]
        if blocking_rules:
            result["action"] = "BLOCKED"
            labels = [r.get("label", "unknown") for r in blocking_rules]
            result["reason"] = f"Iron rule block: {', '.join(labels)}"
            _log_action(db, row, strategy, stars, watch_score, regime,
                        "BLOCKED", result["reason"], dry_run,
                        iron_rules=iron_rules, dim_summary=result["dim_summary"])
            return result

        # Watch-score gate
        if watch_score < C.AUTO_TRADE_MIN_WATCH_SCORE:
            result["action"] = "SKIP"
            result["reason"] = f"Watch score {watch_score} < min {C.AUTO_TRADE_MIN_WATCH_SCORE}"
            _log_action(db, row, strategy, stars, watch_score, regime,
                        "SKIP", result["reason"], dry_run,
                        iron_rules=iron_rules, dim_summary=result["dim_summary"])
            return result

        # ── Phase 4 + 5: Position sizing + order execution ───────────────
        order_result = _phase4_size_and_execute(
            ticker, strategy, stars, analysis, regime, dry_run, db,
            watch_score, iron_rules, result["dim_summary"],
        )
        result["action"] = order_result.get("action", "SKIP")
        result["reason"] = order_result.get("reason", "")
        result["order_result"] = order_result
        return result

    except Exception as exc:
        logger.exception("[AutoTrade P3] %s/%s error: %s", strategy, ticker, exc)
        result["reason"] = f"Exception: {exc}"
        _log_action(db, row, strategy, screener_stars, 0, regime,
                    "SKIP", result["reason"], dry_run)
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Deep analysis helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _run_deep_analysis(ticker: str, strategy: str,
                       market_regime: Optional[str] = None) -> dict:
    """Run analyze_qm() or analyze_ml() and return the result dict."""
    if strategy == "QM":
        from modules.qm_analyzer import analyze_qm
        return analyze_qm(ticker, print_report=False, market_regime=market_regime)
    else:
        from modules.ml_analyzer import analyze_ml
        return analyze_ml(ticker, print_report=False, market_regime=market_regime)


def _evaluate_watch_score(ticker: str, strategy: str, analysis: dict) -> dict:
    """
    Compute watch-mode score for the candidate.
    Uses intraday data if available, otherwise estimates from daily data.

    Returns:
        {"watch_score": int, "iron_rules": list, "breakdown": dict}
    """
    try:
        from modules.data_pipeline import get_enriched

        # Fetch fresh intraday data
        df_intraday = get_enriched(ticker, period="5d")

        if df_intraday is not None and not df_intraday.empty:
            return _compute_watch_signals(ticker, strategy, df_intraday, analysis)

    except Exception as exc:
        logger.warning("[AutoTrade Watch] %s intraday fetch failed: %s", ticker, exc)

    # Fallback: estimate from daily data
    return _estimate_watch_score_from_daily(ticker, strategy, analysis)


def _compute_watch_signals(ticker: str, strategy: str,
                           df_intra: pd.DataFrame, analysis: dict) -> dict:
    """
    Compute watch-mode signals from intraday data.
    Enhanced with EntryPointControl.md entry timing logic:
      - Time-of-day scoring (Kristjan: early session; Martin: first 90 min)
      - Volume ratio hard gates
      - ORB / PMH level detection (ML)
      - EP mode classification (QM)
      - SPY VWAP market filter (ML)
    """
    import numpy as np

    close = float(df_intra["Close"].iloc[-1])
    opn = float(df_intra["Open"].iloc[0]) if len(df_intra) > 0 else close
    high = float(df_intra["High"].max())
    low = float(df_intra["Low"].min())
    vol_today = float(df_intra["Volume"].sum()) if "Volume" in df_intra.columns else 0

    trade_plan = analysis.get("trade_plan", {})
    stars = float(analysis.get("capped_stars") or analysis.get("stars") or 0)

    iron_rules = []
    breakdown = {}
    score = 50  # base score

    # ── Common signals ────────────────────────────────────────────────────
    # VWAP check (if available)
    if "VWAP" in df_intra.columns:
        vwap = float(df_intra["VWAP"].iloc[-1])
        if close > vwap:
            score += 10
            breakdown["vwap"] = "above"
        else:
            iron_rules.append({
                "label": "Below VWAP",
                "label_zh": "低於VWAP",
                "severity": "warn",
                "detail": f"Close ${close:.2f} < VWAP ${vwap:.2f}",
            })
            score -= 10
            breakdown["vwap"] = "below"

    # Close strength (close near high of day)
    day_range = high - low
    if day_range > 0:
        close_pct = (close - low) / day_range
        if close_pct >= 0.7:
            score += 10  # strong close
        elif close_pct < 0.3:
            score -= 10
            iron_rules.append({
                "label": "Weak close",
                "label_zh": "收盤位置偏弱",
                "severity": "warn",
                "detail": f"Close at {close_pct:.0%} of day range",
            })
        breakdown["close_strength"] = round(close_pct, 2)

    # Volume confirmation
    avg_vol = float(analysis.get("avg_vol_20d", 0) or 0)
    vol_ratio = 0.0
    if avg_vol > 0 and vol_today > 0:
        vol_ratio = vol_today / avg_vol
        if vol_ratio >= 1.5:
            score += 10  # volume surge
        elif vol_ratio < 0.5:
            score -= 5
    elif "dollar_volume_m" in analysis:
        score += 5  # has liquidity, give partial credit
    breakdown["vol_ratio"] = round(vol_ratio, 2)

    # Relative to entry/stop levels
    entry = trade_plan.get("entry") or trade_plan.get("entry_price")
    stop = trade_plan.get("day1_stop") or trade_plan.get("stop_price")
    if entry and close > float(entry) * 1.05:
        iron_rules.append({
            "label": "Extended beyond entry",
            "label_zh": "已超過進場點5%+",
            "severity": "block",
            "detail": f"Close ${close:.2f} > entry ${float(entry):.2f} by {((close/float(entry))-1)*100:.1f}%",
        })
        score -= 20

    if stop and close < float(stop):
        iron_rules.append({
            "label": "Below stop level",
            "label_zh": "已跌穿止損位",
            "severity": "block",
            "detail": f"Close ${close:.2f} < stop ${float(stop):.2f}",
        })
        score -= 30

    # ── Time-of-day scoring (EntryPointControl §2.4/§3.3) ────────────────
    tod_adj = _time_of_day_score(strategy)
    score += tod_adj
    breakdown["time_of_day_adj"] = tod_adj

    # Strategy-specific signals
    if strategy == "QM":
        score = _qm_watch_extras(score, iron_rules, close, df_intra, analysis,
                                 vol_ratio, breakdown)
    else:
        score = _ml_watch_extras(score, iron_rules, close, df_intra, analysis,
                                 vol_ratio, avg_vol, vol_today, breakdown)

    # Clamp 0-100
    score = max(0, min(100, score))

    return {"watch_score": score, "iron_rules": iron_rules, "breakdown": breakdown}


def _time_of_day_score(strategy: str) -> int:
    """
    Compute time-of-day adjustment based on US market hours.
    EntryPointControl.md:
      Kristjan: best 09:30-11:30 EST (first 30-120 min)
      Martin:   best 09:30-11:00 EST (first 0-90 min)

    Returns score adjustment (positive = bonus, negative = penalty).
    """
    from datetime import timezone, timedelta as td
    now_utc = datetime.now(timezone.utc)
    # US Eastern = UTC-5 (EST) or UTC-4 (EDT). Use a safe approximation.
    # Market open = 09:30 EST = 14:30 UTC (winter) / 13:30 UTC (summer)
    # For safety, calculate minutes since market potentially opened.
    # We use a simple heuristic: Eastern ≈ UTC-5 in winter, UTC-4 in summer.
    import calendar
    month = now_utc.month
    # DST in US: Mar second Sun → Nov first Sun
    is_dst = 3 < month < 11 or (month == 3 and now_utc.day >= 8) or (month == 11 and now_utc.day < 1)
    et_offset = td(hours=-4) if is_dst else td(hours=-5)
    now_et = now_utc + et_offset
    market_open_hour, market_open_min = 9, 30
    minutes_since_open = (now_et.hour * 60 + now_et.minute) - (market_open_hour * 60 + market_open_min)

    if minutes_since_open < 0 or minutes_since_open > 390:
        # Outside market hours — no adjustment
        return 0

    if strategy == "QM":
        prime_start = getattr(C, "QM_ENTRY_PRIME_START_MIN", 30)
        prime_end = getattr(C, "QM_ENTRY_PRIME_END_MIN", 120)
        prime_bonus = getattr(C, "QM_ENTRY_PRIME_BONUS", 10)
        late_penalty = getattr(C, "QM_ENTRY_LATE_PENALTY", -5)
        avoid_first = getattr(C, "QM_ENTRY_AVOID_FIRST_MIN", 5)

        if minutes_since_open < avoid_first:
            return late_penalty  # opening volatility — avoid
        elif prime_start <= minutes_since_open <= prime_end:
            return prime_bonus
        elif minutes_since_open > 360:  # last 30 min
            return late_penalty
        return 0
    else:  # ML
        prime_start = getattr(C, "ML_ENTRY_PRIME_START_MIN", 0)
        prime_end = getattr(C, "ML_ENTRY_PRIME_END_MIN", 90)
        prime_bonus = getattr(C, "ML_ENTRY_PRIME_BONUS", 10)
        midday_bonus = getattr(C, "ML_ENTRY_MIDDAY_BONUS", 5)
        late_penalty = getattr(C, "ML_ENTRY_LATE_PENALTY", -10)

        if prime_start <= minutes_since_open <= prime_end:
            return prime_bonus
        elif 120 <= minutes_since_open <= 210:  # 11:30-13:00 (midday)
            return midday_bonus
        elif minutes_since_open > 270:  # after 14:00 EST
            return late_penalty
        return 0


def _qm_watch_extras(score: int, iron_rules: list, close: float,
                     df_intra: pd.DataFrame, analysis: dict,
                     vol_ratio: float, breakdown: dict) -> int:
    """
    QM-specific watch signals (Enhanced with EntryPointControl.md):
      - Breakout above pivot with max chase check (Pivot × 1.05)
      - Volume ratio hard gate for breakout confirmation
      - EP mode classification (Mode A aggressive vs Mode B conservative)
      - VCP contraction ratio validation
    """
    trade_plan = analysis.get("trade_plan", {})
    pivot = trade_plan.get("pivot_price") or trade_plan.get("entry")

    # ── Pivot price check + max chase (Kristjan: don't chase > pivot × 1.05) ─
    if pivot:
        pivot = float(pivot)
        max_chase_pct = getattr(C, "QM_MAX_ENTRY_ABOVE_BO_PCT", 3.0)
        max_chase_price = pivot * (1 + max_chase_pct / 100)

        if close >= pivot and close <= max_chase_price:
            score += 10  # at or above pivot, within chasing range
            breakdown["pivot_status"] = "breakout_valid"
        elif close > max_chase_price:
            chase_pct = ((close / pivot) - 1) * 100
            iron_rules.append({
                "label": "Max chase exceeded",
                "label_zh": f"已超越最大追價 (Pivot+{max_chase_pct}%)",
                "severity": "block",
                "detail": (f"Close ${close:.2f} > max chase ${max_chase_price:.2f} "
                           f"(pivot ${pivot:.2f} +{chase_pct:.1f}%)"),
            })
            score -= 15
            breakdown["pivot_status"] = "chasing_blocked"
        elif close < pivot * 0.98:
            score -= 10
            breakdown["pivot_status"] = "below_pivot"

    # ── Volume ratio gate (EntryPointControl 公式2) ───────────────────────
    gate_vol = getattr(C, "QM_WATCH_BREAKOUT_VOL_GATE", 1.5)
    ideal_vol = getattr(C, "QM_WATCH_IDEAL_VOL_RATIO", 2.0)
    weak_penalty = getattr(C, "QM_WATCH_WEAK_VOL_PENALTY", -10)

    if vol_ratio >= ideal_vol:
        score += 5  # extra bonus for ideal volume
        breakdown["vol_gate"] = "ideal"
    elif vol_ratio >= gate_vol:
        breakdown["vol_gate"] = "pass"
    elif vol_ratio > 0 and vol_ratio < 1.2:
        score += weak_penalty
        iron_rules.append({
            "label": "Weak breakout volume",
            "label_zh": "突破量不足 (<1.2×)",
            "severity": "warn",
            "detail": f"Vol ratio {vol_ratio:.1f}× < 1.2× avg — 可能假突破",
        })
        breakdown["vol_gate"] = "weak"

    # ── EP mode classification (EntryPointControl §2.3) ───────────────────
    setup_type = analysis.get("setup_type", "")
    if setup_type == "EP":
        gap_pct = float(analysis.get("gap_pct", 0) or 0)
        mode_a_min = getattr(C, "QM_EP_MODE_A_MIN_GAP_PCT", 10.0)

        if gap_pct >= mode_a_min:
            # EP Mode A: aggressive same-day entry
            breakdown["ep_mode"] = "A_aggressive"
            if vol_ratio >= 3.0:
                score += 5  # strong confirmation for Mode A
        else:
            # EP Mode B: conservative — prefer waiting for consolidation
            consol_days = getattr(C, "QM_EP_MODE_B_CONSOL_DAYS", 3)
            iron_rules.append({
                "label": "EP Mode B — prefer wait",
                "label_zh": f"EP模式B — 建議等{consol_days}天整理後再買",
                "severity": "warn",
                "detail": (f"Gap {gap_pct:.1f}% < {mode_a_min}% — "
                           f"conservative EP favours {consol_days}-day consolidation"),
            })
            score -= 5
            breakdown["ep_mode"] = "B_conservative"

    # ── VCP contraction ratio (EntryPointControl 公式5) ───────────────────
    vcp_info = analysis.get("vcp", {}) or {}
    contractions = vcp_info.get("contractions", [])
    if len(contractions) >= 2:
        max_ratio = getattr(C, "QM_VCP_CONTRACTION_RATIO_MAX", 0.60)
        ratios = []
        for i in range(1, len(contractions)):
            prev = contractions[i - 1]
            curr = contractions[i]
            if prev > 0:
                ratios.append(curr / prev)
        if ratios and all(r < max_ratio for r in ratios):
            score += 5
            breakdown["vcp_contraction"] = "healthy"
        elif ratios:
            breakdown["vcp_contraction"] = "irregular"

    return score


def _ml_watch_extras(score: int, iron_rules: list, close: float,
                     df_intra: pd.DataFrame, analysis: dict,
                     vol_ratio: float, avg_vol: float,
                     vol_today: float, breakdown: dict) -> int:
    """
    ML-specific watch signals (Enhanced with EntryPointControl.md):
      - EMA confluence + pullback completion
      - Opening Range Breakout (ORB) detection
      - Pre-market High (PMH) breakout detection
      - Projected volume calculation from first N minutes
      - SPY VWAP intraday filter
    """
    # ── EMA alignment bonus ───────────────────────────────────────────────
    ema_info = analysis.get("ema_alignment", {})
    if ema_info.get("all_stacked"):
        score += 5
    if ema_info.get("all_rising"):
        score += 5

    # ── AVWAP support check ───────────────────────────────────────────────
    avwap = analysis.get("avwap_analysis", {})
    if avwap and avwap.get("near_avwap_support"):
        score += 10
        breakdown["avwap"] = "near_support"

    # ── Opening Range Breakout (EntryPointControl §3.2 Mode A) ────────────
    or_result = _detect_opening_range(df_intra, close)
    if or_result:
        or_high, or_low = or_result["or_high"], or_result["or_low"]
        breakdown["or_high"] = round(or_high, 2)
        breakdown["or_low"] = round(or_low, 2)

        orb_bonus = getattr(C, "ML_ORB_BREAKOUT_BONUS", 10)
        orb_penalty = getattr(C, "ML_ORB_BELOW_LOW_PENALTY", -10)
        vwap_bonus = getattr(C, "ML_ORB_VWAP_CONFIRM_BONUS", 5)

        if close > or_high:
            score += orb_bonus
            breakdown["orb_status"] = "breakout_above"
            # Additional bonus: OR breakout + above VWAP
            if breakdown.get("vwap") == "above":
                score += vwap_bonus
                breakdown["orb_vwap_confirm"] = True
        elif close < or_low:
            score += orb_penalty
            iron_rules.append({
                "label": "Below opening range",
                "label_zh": "跌穿開盤區間低點",
                "severity": "warn",
                "detail": f"Close ${close:.2f} < OR Low ${or_low:.2f}",
            })
            breakdown["orb_status"] = "below_or"

    # ── Pre-market High (PMH) breakout (EntryPointControl §3.2 Mode B) ────
    pmh = _get_premarket_high(df_intra)
    if pmh and pmh > 0:
        breakdown["pmh"] = round(pmh, 2)
        pmh_bonus = getattr(C, "ML_PMH_BREAKOUT_BONUS", 10)
        if close > pmh:
            score += pmh_bonus
            breakdown["pmh_status"] = "breakout"
        elif close > pmh * 0.99:
            score += 3  # approaching PMH
            breakdown["pmh_status"] = "approaching"

    # ── Projected Volume (EntryPointControl §3.4 公式3) ───────────────────
    proj_vol = _compute_projected_volume(df_intra, avg_vol)
    if proj_vol:
        breakdown["projected_vol_ratio"] = round(proj_vol["ratio"], 2)
        strong_mult = getattr(C, "ML_PROJ_VOL_STRONG_MULT", 2.0)
        weak_mult = getattr(C, "ML_PROJ_VOL_WEAK_MULT", 1.0)

        if proj_vol["ratio"] >= strong_mult:
            score += getattr(C, "ML_PROJ_VOL_STRONG_BONUS", 10)
            breakdown["proj_vol_signal"] = "strong"
        elif proj_vol["ratio"] < weak_mult:
            score += getattr(C, "ML_PROJ_VOL_WEAK_PENALTY", -5)
            breakdown["proj_vol_signal"] = "weak"

    # ── SPY VWAP intraday filter (EntryPointControl Difference 5) ─────────
    if getattr(C, "ML_SPY_VWAP_FILTER_ENABLED", True):
        spy_result = _check_spy_vwap_filter()
        if spy_result is not None:
            breakdown["spy_vwap"] = spy_result["status"]
            if spy_result["status"] == "bearish":
                iron_rules.append({
                    "label": "SPY below VWAP + prev close",
                    "label_zh": "SPY < VWAP 且 < 昨收 — Martin 建議減倉",
                    "severity": "warn",
                    "detail": spy_result["detail"],
                })
                score -= 10

    return score


# ── Intraday helpers for watch-score (EntryPointControl.md) ───────────────

def _detect_opening_range(df_intra: pd.DataFrame, close: float) -> dict | None:
    """
    Detect the Opening Range (OR) from intraday data.
    OR = high/low of first N minutes after market open.
    EntryPointControl §3.2 Mode A: Martin uses 5-15 min OR.

    Returns:
        dict with 'or_high', 'or_low' or None if insufficient data.
    """
    if df_intra is None or df_intra.empty:
        return None

    try:
        or_minutes = getattr(C, "ML_ORB_PERIOD_MINUTES", 15)
        idx = df_intra.index
        if hasattr(idx, 'tz_localize'):
            pass  # already timezone-aware

        # Use the first N rows as a proxy for the opening range
        # (intraday data from get_enriched(period="5d") is daily bars,
        #  so we approximate from the latest day's OHLC)
        # For 5d daily data, the "opening range" is approximated as the
        # last bar's Open vs first meaningful range
        if len(df_intra) >= 2:
            last_bar = df_intra.iloc[-1]
            # Use day's open ± intraday range as OR proxy
            day_open = float(last_bar.get("Open", close))
            day_high = float(last_bar.get("High", close))
            day_low = float(last_bar.get("Low", close))

            # Approximate OR as the tighter range around open
            day_range = day_high - day_low
            if day_range > 0:
                or_high = day_open + day_range * 0.3  # upper 30% of range from open
                or_low = day_open - day_range * 0.2   # lower 20% from open
                return {"or_high": or_high, "or_low": or_low}
    except Exception:
        pass
    return None


def _get_premarket_high(df_intra: pd.DataFrame) -> float | None:
    """
    Estimate the pre-market high (PMH) from intraday data.
    PMH = the high before the regular session opens.

    For daily-level data, we approximate PMH as the previous day's high
    (since true pre-market data requires intraday feeds).
    """
    if df_intra is None or len(df_intra) < 2:
        return None
    try:
        prev_bar = df_intra.iloc[-2]
        return float(prev_bar["High"])
    except Exception:
        return None


def _compute_projected_volume(df_intra: pd.DataFrame,
                              avg_vol_20d: float) -> dict | None:
    """
    Project full-day volume from partial-day volume.
    EntryPointControl §3.4 公式3:
      Projected Daily Volume = Volume_first_N_min / typical_pct

    For daily data proxy, use today's volume vs average.
    """
    if df_intra is None or df_intra.empty or avg_vol_20d <= 0:
        return None

    try:
        today_vol = float(df_intra.iloc[-1].get("Volume", 0))
        if today_vol <= 0:
            return None

        # For daily data, the volume is the actual day volume (or partial if market still open)
        # Use the ratio_fraction from config to project
        ratio_30 = getattr(C, "ML_PROJ_VOL_FIRST_30_RATIO", 0.25)

        # Heuristic: if we're during market hours, volume may be partial
        from datetime import timezone as tz
        now_utc = datetime.now(tz.utc)
        month = now_utc.month
        is_dst = 3 < month < 11
        et_offset = timedelta(hours=-4) if is_dst else timedelta(hours=-5)
        now_et = now_utc + et_offset
        minutes_since_open = (now_et.hour * 60 + now_et.minute) - (9 * 60 + 30)

        if 0 < minutes_since_open < 390:
            # Market is open — project volume
            elapsed_pct = min(1.0, minutes_since_open / 390)
            # Use empirical ratio: first 30 min ≈ 25% of daily volume
            if elapsed_pct > 0.05:
                projected = today_vol / max(elapsed_pct, 0.08)
                ratio = projected / avg_vol_20d
                return {"projected_vol": projected, "ratio": ratio,
                        "elapsed_pct": round(elapsed_pct, 2)}
        else:
            # Market closed — use actual volume
            ratio = today_vol / avg_vol_20d
            return {"projected_vol": today_vol, "ratio": ratio,
                    "elapsed_pct": 1.0}
    except Exception:
        pass
    return None


def _check_spy_vwap_filter() -> dict | None:
    """
    Check if SPY is below its VWAP and below yesterday's close.
    EntryPointControl Difference 5 (Martin Luk):
      IF SPY < today VWAP AND SPY < yesterday close
        THEN reduce new positions by 50% or delay entry

    Returns:
        dict with 'status' ("bullish"/"bearish") and 'detail', or None.
    """
    try:
        from modules.data_pipeline import get_enriched
        spy = get_enriched("SPY", period="5d")
        if spy is None or len(spy) < 2:
            return None

        spy_close = float(spy["Close"].iloc[-1])
        spy_prev_close = float(spy["Close"].iloc[-2])

        spy_vwap = None
        if "VWAP" in spy.columns:
            spy_vwap = float(spy["VWAP"].iloc[-1])

        if spy_vwap is None:
            # Approximate VWAP from OHLC typical price
            h = float(spy["High"].iloc[-1])
            l = float(spy["Low"].iloc[-1])
            spy_vwap = (h + l + spy_close) / 3

        below_vwap = spy_close < spy_vwap
        below_prev = spy_close < spy_prev_close

        if below_vwap and below_prev:
            return {
                "status": "bearish",
                "detail": (f"SPY ${spy_close:.2f} < VWAP ${spy_vwap:.2f} "
                           f"AND < prev close ${spy_prev_close:.2f}"),
            }
        return {"status": "bullish", "detail": f"SPY ${spy_close:.2f} OK"}
    except Exception as exc:
        logger.debug("[AutoTrade] SPY VWAP filter error: %s", exc)
        return None


def _estimate_watch_score_from_daily(ticker: str, strategy: str,
                                     analysis: dict) -> dict:
    """
    Estimate watch score from daily-level data when intraday is unavailable.
    More conservative — caps at 75 since intraday confirmation is missing.
    """
    stars = float(analysis.get("capped_stars") or analysis.get("stars") or 0)
    iron_rules = []
    score = 40  # lower base without intraday

    # Star rating contribution (higher stars → higher base score)
    if stars >= 5.0:
        score += 20
    elif stars >= 4.0:
        score += 15
    elif stars >= 3.5:
        score += 10

    # Recommendation bonus
    rec = analysis.get("recommendation", "")
    if rec in ("STRONG BUY", "BUY"):
        score += 10
    elif rec == "WATCH":
        score += 5

    # ML decision tree
    if strategy == "ML":
        dt = analysis.get("decision_tree", {})
        verdict = dt.get("verdict", "")
        if verdict == "GO":
            score += 10
        elif verdict == "CAUTION":
            score += 5

    iron_rules.append({
        "label": "No intraday data",
        "label_zh": "無日內數據",
        "severity": "warn",
        "detail": "Watch score estimated from daily data; capped at 75",
    })

    score = max(0, min(75, score))
    return {"watch_score": score, "iron_rules": iron_rules, "breakdown": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4 + 5 — Position sizing + order execution
# ═══════════════════════════════════════════════════════════════════════════════

def _phase4_size_and_execute(ticker: str, strategy: str, stars: float,
                             analysis: dict, regime: str, dry_run: bool, db,
                             watch_score: int, iron_rules: list,
                             dim_summary: dict) -> dict:
    """Calculate position size, then execute or log the order.

    ML scaled entry (EntryPointControl §四 Difference 4):
      When ML_SCALED_ENTRY_ENABLED, ML trades start with a probe position
      (30% of calculated shares). Subsequent cycles add confirmation (40%)
      and full (30%) tranches as watch-score improves.

    SPY VWAP size reduction (EntryPointControl Difference 5):
      When SPY < VWAP AND < prev close, ML position size is halved.
    """
    trade_plan = analysis.get("trade_plan", {})
    close = float(analysis.get("close", 0))
    entry_price = float(trade_plan.get("entry") or trade_plan.get("entry_price") or close)
    stop_price = float(trade_plan.get("day1_stop") or trade_plan.get("stop_price") or 0)

    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        reason = f"Invalid entry/stop: entry=${entry_price:.2f}, stop=${stop_price:.2f}"
        _log_action(db, {"ticker": ticker}, strategy, stars, watch_score, regime,
                    "SKIP", reason, dry_run, iron_rules=iron_rules, dim_summary=dim_summary)
        return {"action": "SKIP", "reason": reason}

    # ── Position sizing ───────────────────────────────────────────────────
    if strategy == "QM":
        from modules.qm_position_rules import calc_qm_position_size
        sizing = calc_qm_position_size(
            star_rating=stars,
            entry_price=entry_price,
            stop_price=stop_price,
        )
    else:
        from modules.ml_position_rules import calc_ml_position_size
        sizing = calc_ml_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
        )

    shares = sizing.get("shares", 0)
    if shares <= 0:
        reason = f"Position size = 0: {sizing.get('reason_zh', sizing.get('action', ''))}"
        _log_action(db, {"ticker": ticker}, strategy, stars, watch_score, regime,
                    "SKIP", reason, dry_run, iron_rules=iron_rules, dim_summary=dim_summary)
        return {"action": "SKIP", "reason": reason}

    # ── Apply regime size multiplier ──────────────────────────────────────
    mult_map = C.AUTO_QM_SIZE_MULTIPLIERS if strategy == "QM" else C.AUTO_ML_SIZE_MULTIPLIERS
    multiplier = mult_map.get(regime, 0.5)
    shares = max(1, int(shares * multiplier))

    # ── ML: SPY VWAP size reduction (EntryPointControl Difference 5) ──────
    spy_vwap_mult = 1.0
    if strategy == "ML" and getattr(C, "ML_SPY_VWAP_FILTER_ENABLED", True):
        spy_check = _check_spy_vwap_filter()
        if spy_check and spy_check["status"] == "bearish":
            spy_vwap_mult = getattr(C, "ML_SPY_BELOW_VWAP_SIZE_MULT", 0.5)
            shares = max(1, int(shares * spy_vwap_mult))
            logger.info("[AutoTrade P4] %s ML size reduced by SPY VWAP filter: ×%.1f",
                        ticker, spy_vwap_mult)

    # ── ML: Scaled entry — probe phase (EntryPointControl Difference 4) ──
    scaled_phase = None
    full_shares = shares
    if (strategy == "ML"
            and getattr(C, "ML_SCALED_ENTRY_ENABLED", True)
            and not _has_existing_position(ticker)):
        # Check which phase this ticker is at
        probe_pct = getattr(C, "ML_SCALED_ENTRY_PROBE_PCT", 30)
        confirm_pct = getattr(C, "ML_SCALED_ENTRY_CONFIRM_PCT", 40)
        probe_threshold = getattr(C, "ML_SCALED_ENTRY_PROBE_SCORE", 60)
        confirm_threshold = getattr(C, "ML_SCALED_ENTRY_CONFIRM_SCORE", 75)

        if watch_score >= confirm_threshold:
            # Strong confirmation — buy probe + confirm (70% of total)
            fraction = (probe_pct + confirm_pct) / 100
            shares = max(1, int(full_shares * fraction))
            scaled_phase = "probe+confirm"
            logger.info("[AutoTrade P4] %s ML scaled entry phase=probe+confirm: "
                        "%d/%d shares (%.0f%%)",
                        ticker, shares, full_shares, fraction * 100)
        elif watch_score >= probe_threshold:
            # Moderate — probe only (30% of total)
            fraction = probe_pct / 100
            shares = max(1, int(full_shares * fraction))
            scaled_phase = "probe"
            logger.info("[AutoTrade P4] %s ML scaled entry phase=probe: "
                        "%d/%d shares (%.0f%%)",
                        ticker, shares, full_shares, fraction * 100)
        # If watch_score < probe_threshold, skip (caught by watch-score gate above)

    # ── Pool control: drawdown / loss-streak throttle ─────────────────────
    from modules.position_controller import (
        get_pool_for_strategy, can_allocate, allocate_to_pool, adjusted_position_size,
    )
    pool = get_pool_for_strategy(strategy)

    adj = adjusted_position_size(shares)
    if adj["shares"] <= 0:
        reason = f"Pool throttle: {adj['reason']}"
        _log_action(db, {"ticker": ticker}, strategy, stars, watch_score, regime,
                    "SKIP", reason, dry_run, iron_rules=iron_rules, dim_summary=dim_summary)
        return {"action": "SKIP", "reason": reason}
    if adj["multiplier"] < 1.0:
        logger.info("[AutoTrade P4] %s size reduced %d→%d (%s)",
                    ticker, shares, adj["shares"], adj["reason"])
        shares = adj["shares"]

    # ── Pool control: allocation gate ─────────────────────────────────────
    gate = can_allocate(pool, entry_price, shares, stop_price)
    if not gate["allowed"]:
        reason = f"Pool gate ({pool}): {gate['reason']}"
        logger.info("[AutoTrade P4] %s BLOCKED — %s", ticker, reason)
        _log_action(db, {"ticker": ticker}, strategy, stars, watch_score, regime,
                    "SKIP", reason, dry_run, iron_rules=iron_rules, dim_summary=dim_summary)
        return {"action": "SKIP", "reason": reason}

    # ── Limit order price ─────────────────────────────────────────────────
    order_type = C.AUTO_TRADE_ORDER_TYPE
    limit_price = None
    if order_type == "LMT":
        limit_price = round(entry_price * (1 + C.AUTO_TRADE_LMT_BUFFER_PCT / 100), 2)

    scale_info = ""
    if scaled_phase:
        scale_info = f" [scaled={scaled_phase} {shares}/{full_shares}]"
    logger.info("[AutoTrade P4] %s/%s: %d shares @ $%.2f, stop=$%.2f, regime_mult=%.1f%s",
                strategy, ticker, shares, entry_price, stop_price, multiplier, scale_info)

    # ── Phase 5: Execute order ────────────────────────────────────────────
    order_id = None
    scaled_note = f" scaled={scaled_phase}" if scaled_phase else ""
    if dry_run:
        reason = f"DRY-RUN: Would buy {shares} shares @ ${limit_price or entry_price:.2f}{scaled_note}"
        logger.info("[AutoTrade P5] %s %s", ticker, reason)
    else:
        order_result = _execute_ibkr_order(
            ticker, shares, order_type, limit_price, stop_price,
        )
        if not order_result.get("success"):
            reason = f"Order failed: {order_result.get('message', 'unknown')}"
            _log_action(db, {"ticker": ticker}, strategy, stars, watch_score, regime,
                        "SKIP", reason, dry_run, iron_rules=iron_rules, dim_summary=dim_summary)
            return {"action": "SKIP", "reason": reason}
        order_id = order_result.get("order_id")
        reason = f"BUY {shares} shares @ ${limit_price or entry_price:.2f}, order_id={order_id}"

        # Record position
        try:
            from modules.position_monitor import add_position
            target = trade_plan.get("target") or trade_plan.get("target_price")
            target = float(target) if target else None
            add_position(
                ticker=ticker,
                buy_price=entry_price,
                shares=shares,
                stop_loss=stop_price,
                target=target,
                note=f"Auto-{strategy} {stars:.1f}★ ws={watch_score}{scaled_note}",
                pool=pool,
            )
            # Log pool allocation after position recorded
            try:
                allocate_to_pool(
                    ticker=ticker, pool=pool, shares=shares,
                    entry_price=entry_price, stop_price=stop_price,
                    note=f"Auto-{strategy} {stars:.1f}★ ws={watch_score} order={order_id}",
                )
            except Exception as exc:
                logger.warning("[AutoTrade] allocate_to_pool failed for %s: %s", ticker, exc)
        except Exception as exc:
            logger.warning("[AutoTrade] add_position failed for %s: %s", ticker, exc)

    # ── Mark ticker as bought today ───────────────────────────────────────
    _bought_today[ticker] = time.time()

    # ── Log to DuckDB ─────────────────────────────────────────────────────
    _log_action(
        db, {"ticker": ticker}, strategy, stars, watch_score, regime,
        "BUY", reason, dry_run,
        order_type=order_type,
        qty=shares,
        limit_price=limit_price,
        stop_price=stop_price,
        order_id=order_id,
        iron_rules=iron_rules,
        dim_summary=dim_summary,
    )

    return {"action": "BUY", "reason": reason, "shares": shares,
            "limit_price": limit_price, "stop_price": stop_price,
            "order_id": order_id}


def _execute_ibkr_order(ticker: str, qty: int, order_type: str,
                        limit_price: Optional[float], stop_price: float) -> dict:
    """Place BUY order + optional stop-loss bracket via IBKR."""
    from modules.ibkr_client import place_order

    # Main BUY order
    result = place_order(
        ticker=ticker,
        action="BUY",
        qty=qty,
        order_type=order_type,
        limit_price=limit_price if order_type == "LMT" else None,
    )

    # Attach Day1 stop-loss order if enabled
    if result.get("success") and C.AUTO_TRADE_ATTACH_STOP and stop_price > 0:
        try:
            stop_result = place_order(
                ticker=ticker,
                action="SELL",
                qty=qty,
                order_type="STP",
                aux_price=stop_price,
            )
            if stop_result.get("success"):
                logger.info("[AutoTrade] Stop-loss attached for %s @ $%.2f",
                            ticker, stop_price)
            else:
                logger.warning("[AutoTrade] Stop-loss order failed for %s: %s",
                               ticker, stop_result.get("message"))
        except Exception as exc:
            logger.warning("[AutoTrade] Stop-loss placement error: %s", exc)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_on_cooldown(ticker: str) -> bool:
    """Check if ticker was bought within the cooldown period."""
    last_buy = _bought_today.get(ticker)
    if last_buy is None:
        return False
    return (time.time() - last_buy) < C.AUTO_TRADE_COOLDOWN_SEC


def _count_buys_today(db, today_str: str) -> int:
    """Count how many BUY actions were logged today in DuckDB."""
    try:
        rows = db.query_auto_trade_log(days=1)
        return sum(
            1 for r in rows
            if str(r.get("trade_date", "")) == today_str
            and r.get("action") == "BUY"
        )
    except Exception:
        return 0


def _reset_daily_counters():
    """Reset the in-memory bought-today tracker at midnight."""
    global _bought_today
    today = date.today().isoformat()
    # Remove entries from previous days
    _bought_today = {
        t: ts for t, ts in _bought_today.items()
        if date.fromtimestamp(ts).isoformat() == today
    }
    # Reset exit engine daily counters
    try:
        from modules.exit_engine import reset_daily_counters as _exit_reset
        _exit_reset()
    except Exception:
        pass


def _has_existing_position(ticker: str) -> bool:
    """Check if we already hold a position in this ticker (for ML scaled entry)."""
    try:
        from modules.position_monitor import _load as _pm_load
        data = _pm_load()
        positions = data.get("positions", {})
        return ticker.upper() in {t.upper() for t in positions}
    except Exception:
        pass
    return False


def _check_ml_scalein_candidates(db, regime: str, dry_run: bool) -> list:
    """
    Check existing ML probe positions for scale-in opportunities.
    EntryPointControl §四 Difference 4 (Martin scaled entry):
      Phase 1 = probe (30%) → if confirmation signals met → Phase 2 add (40%)
      Phase 2 = confirm → if PMH breakout → Phase 3 full (30%)

    Called from _run_one_cycle() after normal candidate processing.
    Returns list of scale-in actions taken.
    """
    if not getattr(C, "ML_SCALED_ENTRY_ENABLED", True):
        return []

    actions = []
    try:
        from modules.position_monitor import _load as _pm_load
        data = _pm_load()
        pos_dict = data.get("positions", {})
        positions = [
            {"ticker": t, **v} for t, v in pos_dict.items()
        ]
        if not positions:
            return []

        for pos in positions:
            note = pos.get("note", "")
            ticker = pos.get("ticker", "")
            if "Auto-ML" not in note or "scaled=probe" not in note:
                continue
            # Already has confirm or full — skip
            if "probe+confirm" in note or "scaled=full" in note:
                continue

            # This is a probe-phase ML position — check for scale-in
            try:
                from modules.data_pipeline import get_enriched
                from modules.ml_analyzer import analyze_ml

                analysis = analyze_ml(ticker, print_report=False, market_regime=regime)
                if not analysis or analysis.get("veto"):
                    continue

                watch = _evaluate_watch_score(ticker, "ML", analysis)
                ws = watch.get("watch_score", 0)
                confirm_threshold = getattr(C, "ML_SCALED_ENTRY_CONFIRM_SCORE", 75)

                if ws >= confirm_threshold:
                    # Scale-in confirmed — compute remaining shares
                    stars = float(analysis.get("capped_stars") or analysis.get("stars") or 0)
                    existing_shares = int(pos.get("shares", 0))
                    probe_pct = getattr(C, "ML_SCALED_ENTRY_PROBE_PCT", 30)
                    confirm_pct = getattr(C, "ML_SCALED_ENTRY_CONFIRM_PCT", 40)

                    # Estimate full position from probe shares
                    full_shares = int(existing_shares / (probe_pct / 100)) if probe_pct > 0 else 0
                    add_shares = max(1, int(full_shares * confirm_pct / 100))

                    entry_price = float(pos.get("buy_price", 0))
                    stop_price = float(pos.get("stop_loss", 0))

                    if add_shares > 0 and entry_price > 0:
                        action_str = (
                            f"ML SCALE-IN: {ticker} +{add_shares} shares "
                            f"(confirm phase, ws={ws})"
                        )
                        logger.info("[AutoTrade ScaleIn] %s", action_str)

                        if not dry_run:
                            order_result = _execute_ibkr_order(
                                ticker, add_shares,
                                C.AUTO_TRADE_ORDER_TYPE,
                                round(entry_price * (1 + C.AUTO_TRADE_LMT_BUFFER_PCT / 100), 2)
                                if C.AUTO_TRADE_ORDER_TYPE == "LMT" else None,
                                stop_price,
                            )
                            if order_result.get("success"):
                                # Update position note to reflect scaled phase
                                try:
                                    from modules.position_monitor import _load as _pm_ld, _save as _pm_sv
                                    pm_data = _pm_ld()
                                    if ticker in pm_data.get("positions", {}):
                                        p = pm_data["positions"][ticker]
                                        p["note"] = note.replace("scaled=probe", "scaled=probe+confirm")
                                        p["shares"] = int(p.get("shares", 0)) + add_shares
                                        _pm_sv(pm_data)
                                except Exception:
                                    pass

                        actions.append({"ticker": ticker, "action": action_str,
                                        "add_shares": add_shares, "watch_score": ws})
            except Exception as exc:
                logger.debug("[AutoTrade ScaleIn] %s error: %s", ticker, exc)

    except Exception as exc:
        logger.debug("[AutoTrade ScaleIn] load error: %s", exc)

    return actions


def _log_action(db, row: dict, strategy: str, stars: float,
                watch_score: int, regime: str, action: str,
                reason: str, dry_run: bool, **kwargs):
    """Write a single action to the DuckDB auto_trade_log."""
    try:
        db.append_auto_trade({
            "trade_date":  date.today().isoformat(),
            # Persist in UTC to avoid ambiguous local-time interpretation in UI.
            "trade_time":  datetime.now(timezone.utc).isoformat(),
            "ticker":      row.get("ticker", ""),
            "strategy":    strategy,
            "stars":       stars,
            "watch_score": watch_score,
            "regime":      regime,
            "action":      action,
            "reason":      reason,
            "order_type":  kwargs.get("order_type", ""),
            "qty":         kwargs.get("qty", 0),
            "limit_price": kwargs.get("limit_price"),
            "stop_price":  kwargs.get("stop_price"),
            "order_id":    kwargs.get("order_id"),
            "dry_run":     dry_run,
            "iron_rules":  kwargs.get("iron_rules", []),
            "dim_summary": kwargs.get("dim_summary", {}),
        })
    except Exception as exc:
        logger.warning("[AutoTrade] Failed to log action: %s", exc)
