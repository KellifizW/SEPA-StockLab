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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")  # US Eastern timezone (handles DST automatically)

# ── Module-level state ────────────────────────────────────────────────────────
_lock          = threading.Lock()
_cancel_event  = threading.Event()
_running       = False
_thread: Optional[threading.Thread] = None

# Race-condition protection
_in_flight: set[str] = set()       # Tickers currently being analyzed
_cycle_running: bool = False        # Is a cycle currently executing?
_last_heartbeat: float = 0.0       # Timestamp of last successful cycle completion

# Pending orders placed this session: ticker → {order_id, stop_order_id, qty, strategy}
_pending_orders: dict[str, dict] = {}

_status: dict = {
    "running":         False,
    "dry_run":         True,
    "cycle":           0,
    "last_cycle_at":   None,
    "buys_today":      0,
    "buys_today_qm":   0,
    "buys_today_ml":   0,
    "candidates":      [],
    "last_actions":    [],
    "pending_orders":  0,
    "error":           None,
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
    """Return a snapshot of the auto-trade status dict."""
    with _lock:
        return dict(_status)


def start(dry_run: Optional[bool] = None) -> dict:
    """
    Start the auto-trade polling loop in a background thread.

    Args:
        dry_run: Override AUTO_TRADE_DRY_RUN if provided.

    Returns:
        {"ok": bool, "message": str}
    """
    global _running, _thread, _last_heartbeat

    if dry_run is None:
        dry_run = C.AUTO_TRADE_DRY_RUN

    with _lock:
        if _running:
            return {"ok": False, "message": "Auto-trade already running"}
        _cancel_event.clear()
        _running = True
        _last_heartbeat = time.time()
        _status.update({
            "running":       True,
            "dry_run":       dry_run,
            "cycle":         0,
            "buys_today_qm": 0,
            "buys_today_ml": 0,
            "error":         None,
        })

    # Restore today's buy history from DuckDB (recovery after restart)
    try:
        from modules import db as _db
        _restore_todays_buys(_db)
    except Exception as exc:
        logger.warning("[AutoTrade] Could not restore today's buys from DuckDB: %s", exc)

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
    """Stop the polling loop gracefully, then cancel any session-placed pending orders."""
    global _running, _thread
    with _lock:
        if not _running:
            return {"ok": False, "message": "Auto-trade not running"}
        _cancel_event.set()
        _running = False
        _status["running"] = False

    if _thread is not None:
        _thread.join(timeout=15)   # Give current cycle time to finish
        _thread = None

    # Graceful shutdown: cancel any pending LMT orders placed this session
    _cancel_session_pending_orders()

    logger.info("[AutoTrade] Stopped")
    return {"ok": True, "message": "Auto-trade stopped"}


# ═══════════════════════════════════════════════════════════════════════════════
# Polling loop (runs in background thread)
# ═══════════════════════════════════════════════════════════════════════════════

def _poll_loop(dry_run: bool):
    """Runs until _cancel_event is set, executing one cycle per interval.

    Guards against overlapping cycles: if the previous cycle is still running
    when the interval fires, the new cycle is skipped (not queued).
    Heartbeat monitoring: sends Telegram alert if no activity for HEARTBEAT_MAX_SEC.
    """
    global _running, _cycle_running, _last_heartbeat

    poll_sec = max(C.AUTO_TRADE_POLL_INTERVAL_SEC, 30)  # floor 30s
    heartbeat_max = getattr(C, "AUTO_TRADE_HEARTBEAT_MAX_SEC", 600)
    logger.info("[AutoTrade] Poll loop started, interval=%ds", poll_sec)

    while not _cancel_event.is_set():
        # Single-cycle guard: skip if previous cycle is still running
        if _cycle_running:
            logger.warning(
                "[AutoTrade] Previous cycle still running — skipping this interval. "
                "Consider increasing POLL_INTERVAL_SEC or reducing MAX_CANDIDATES."
            )
        else:
            _cycle_running = True
            try:
                _run_one_cycle(dry_run)
                _last_heartbeat = time.time()
            except Exception as exc:
                logger.exception("[AutoTrade] Cycle error: %s", exc)
                with _lock:
                    _status["error"] = str(exc)
            finally:
                _cycle_running = False

        # Heartbeat check
        if time.time() - _last_heartbeat > heartbeat_max:
            logger.error("[AutoTrade] Heartbeat timeout — no activity for >%ds", heartbeat_max)
            _notify_telegram(
                f"\u26a0\ufe0f [AutoTrade] \u5fc3\u8df3\u8d85\u6642\u544a\u8b66\n"
                f"\u8f2a\u8a62\u5faa\u74b0\u8d85\u904e {heartbeat_max} \u79d2\u6c92\u6709\u6d3b\u52d5\uff0c\u8acb\u6aa2\u67e5\u5f15\u64ce\u72c0\u614b\u3002"
            )
            _last_heartbeat = time.time()  # Reset to prevent repeated alerts

        # Wait for next cycle, checking cancel every 5s
        for _ in range(poll_sec // 5):
            if _cancel_event.is_set():
                break
            time.sleep(5)
        remainder = poll_sec % 5
        if remainder and not _cancel_event.is_set():
            time.sleep(remainder)

    with _lock:
        _running = False
        _status["running"] = False
    logger.info("[AutoTrade] Poll loop exited")


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
        cycle_num = _status["cycle"]

    logger.info("[AutoTrade] ── Cycle %d start ──", cycle_num)

    # ── Reset daily counters at midnight ───────────────────────────────────
    _reset_daily_counters()

    # ── Phase 1a: Trading hours gate ──────────────────────────────────────
    if not _is_market_hours():
        now_et = datetime.now(_ET)
        logger.info("[AutoTrade] Phase 1a: Outside trading hours (%s ET) — skipping cycle",
                    now_et.strftime("%H:%M"))
        with _lock:
            _status["error"] = f"Outside trading hours ({now_et.strftime('%H:%M')} ET)"
        return

    # ── Phase 0: Load latest scan results ─────────────────────────────────
    qm_candidates, ml_candidates, market_env, scan_age_min = _phase0_load_scan_results()
    regime = market_env.get("regime", "UNKNOWN") if market_env else "UNKNOWN"
    logger.info("[AutoTrade] Phase 0: QM=%d, ML=%d candidates, regime=%s, scan_age=%dmin",
                len(qm_candidates), len(ml_candidates), regime, scan_age_min)

    # ── Phase 0 check: data freshness ─────────────────────────────────────
    scan_max_age = getattr(C, "AUTO_TRADE_SCAN_MAX_AGE_MIN", 30)
    if scan_age_min > scan_max_age:
        logger.warning(
            "[AutoTrade] Phase 0: Scan data is %d minutes old (max %d) — skipping cycle",
            scan_age_min, scan_max_age,
        )
        with _lock:
            _status["error"] = f"Scan data too old ({scan_age_min}min > {scan_max_age}min)"
        return

    # ── Phase 1b: Market-regime gate ──────────────────────────────────────
    qm_allowed = regime in C.AUTO_QM_REGIMES_ENABLED
    ml_allowed = regime in C.AUTO_ML_REGIMES_ENABLED

    if not qm_allowed:
        logger.info("[AutoTrade] Phase 1b: QM BLOCKED — regime %s not in allowed list", regime)
        for c in qm_candidates:
            _log_action(db, c, "QM", 0, 0, 0, regime, "BLOCKED",
                        f"Regime {regime} not allowed for QM", dry_run,
                        scan_data_age_min=scan_age_min)
        qm_candidates = []

    if not ml_allowed:
        logger.info("[AutoTrade] Phase 1b: ML BLOCKED — regime %s not in allowed list", regime)
        for c in ml_candidates:
            _log_action(db, c, "ML", 0, 0, 0, regime, "BLOCKED",
                        f"Regime {regime} not allowed for ML", dry_run,
                        scan_data_age_min=scan_age_min)
        ml_candidates = []

    # ── Phase 2: Pre-filter by screener star rating + prioritize ──────────
    qm_filtered = _phase2_prefilter(qm_candidates, "QM", regime, db, dry_run, scan_age_min)
    ml_filtered = _phase2_prefilter(ml_candidates, "ML", regime, db, dry_run, scan_age_min)
    logger.info("[AutoTrade] Phase 2: QM=%d, ML=%d after star filter + prioritization",
                len(qm_filtered), len(ml_filtered))

    # ── Phase 3: Deep analysis + watch score ──────────────────────────────
    actions = []
    today_str = date.today().isoformat()
    remaining_qm = C.AUTO_TRADE_MAX_BUYS_PER_DAY_QM - _count_buys_today(db, today_str, "QM")
    remaining_ml = C.AUTO_TRADE_MAX_BUYS_PER_DAY_ML - _count_buys_today(db, today_str, "ML")

    if remaining_qm <= 0 and remaining_ml <= 0:
        logger.info("[AutoTrade] Phase 3: Daily buy limits reached (QM=%d, ML=%d), skipping",
                    C.AUTO_TRADE_MAX_BUYS_PER_DAY_QM, C.AUTO_TRADE_MAX_BUYS_PER_DAY_ML)
    else:
        # Process QM candidates first (breakout = more time-sensitive)
        for cand in qm_filtered[:C.AUTO_TRADE_MAX_CANDIDATES]:
            if remaining_qm <= 0 or _cancel_event.is_set():
                break
            result = _phase3_analyze_and_decide(
                cand, "QM", regime, dry_run, db, scan_age_min)
            actions.append(result)
            if result.get("action") == "BUY":
                remaining_qm -= 1

        # Process ML candidates
        for cand in ml_filtered[:C.AUTO_TRADE_MAX_CANDIDATES]:
            if remaining_ml <= 0 or _cancel_event.is_set():
                break
            result = _phase3_analyze_and_decide(
                cand, "ML", regime, dry_run, db, scan_age_min)
            actions.append(result)
            if result.get("action") == "BUY":
                remaining_ml -= 1

    # ── Update status ─────────────────────────────────────────────────────
    with _lock:
        qm_bought = C.AUTO_TRADE_MAX_BUYS_PER_DAY_QM - remaining_qm
        ml_bought = C.AUTO_TRADE_MAX_BUYS_PER_DAY_ML - remaining_ml
        _status["buys_today_qm"] = qm_bought
        _status["buys_today_ml"] = ml_bought
        _status["buys_today"]    = qm_bought + ml_bought
        _status["pending_orders"] = len(_pending_orders)
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

def _phase0_load_scan_results() -> tuple[list, list, dict, int]:
    """
    Load the latest QM and ML scan results from JSON files.

    Returns:
        (qm_rows, ml_rows, market_env_dict, scan_age_minutes)
        scan_age_minutes = how old the scan file is; 9999 if not found / unreadable.
    """
    qm_rows = []
    ml_rows = []
    market_env = {}
    scan_age_min = 9999  # Assume stale until proven fresh

    # Try combined scan first (has both QM + ML + market_env)
    try:
        if _COMBINED_LAST_FILE.exists():
            scan_age_min = _get_file_age_minutes(_COMBINED_LAST_FILE)
            data = json.loads(_COMBINED_LAST_FILE.read_text(encoding="utf-8"))
            qm_rows = data.get("qm_rows", [])
            ml_rows = data.get("ml_rows", [])
            market_env = data.get("market_env", {})
            logger.info("[AutoTrade P0] Loaded combined: QM=%d ML=%d, age=%dmin",
                        len(qm_rows), len(ml_rows), scan_age_min)
    except Exception as exc:
        logger.warning("[AutoTrade P0] Failed to load combined scan: %s", exc)

    # Fallback: individual QM/ML scan files
    if not qm_rows:
        try:
            if _QM_LAST_FILE.exists():
                age = _get_file_age_minutes(_QM_LAST_FILE)
                scan_age_min = min(scan_age_min, age)
                data = json.loads(_QM_LAST_FILE.read_text(encoding="utf-8"))
                qm_rows = data.get("results", data.get("qm_rows", []))
        except Exception as exc:
            logger.warning("[AutoTrade P0] Failed to load QM scan: %s", exc)

    if not ml_rows:
        try:
            if _ML_LAST_FILE.exists():
                age = _get_file_age_minutes(_ML_LAST_FILE)
                scan_age_min = min(scan_age_min, age)
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

    return qm_rows, ml_rows, market_env, scan_age_min


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Pre-filter by screener star rating
# ═══════════════════════════════════════════════════════════════════════════════

def _phase2_prefilter(candidates: list, strategy: str, regime: str,
                      db, dry_run: bool, scan_age_min: int = 0) -> list:
    """
    Filter scan result rows by:
    1. Minimum SCREENER star rating (Phase 2 gate — uses MIN_SCREENER_STAR)
    2. Same-day cooldown (ticker bought today)
    3. Already holding the ticker in IBKR open positions
    4. Currently being analyzed in another thread (in-flight set)

    Returns candidates sorted by priority score, capped at MAX_CANDIDATES.
    """
    if strategy == "QM":
        min_star = getattr(C, "AUTO_TRADE_MIN_SCREENER_STAR_QM", C.AUTO_TRADE_MIN_STAR_QM)
        star_key = "qm_star"
    else:
        min_star = getattr(C, "AUTO_TRADE_MIN_SCREENER_STAR_ML", C.AUTO_TRADE_MIN_STAR_ML)
        star_key = "ml_star"

    # Fetch already-held tickers from IBKR (best-effort)
    held_tickers: set[str] = _get_held_tickers()

    passed = []
    for row in candidates:
        ticker = row.get("ticker", "")
        stars = float(row.get(star_key) or row.get("stars") or 0)

        # Screener star gate
        if stars < min_star:
            continue

        # Same-day cooldown
        if _is_on_cooldown(ticker):
            _log_action(db, row, strategy, stars, stars, 0, regime, "SKIP",
                        "Cooldown active (bought today)", dry_run,
                        scan_data_age_min=scan_age_min)
            continue

        # Already holding this ticker
        if ticker in held_tickers:
            _log_action(db, row, strategy, stars, stars, 0, regime, "SKIP",
                        "Already holding position", dry_run,
                        scan_data_age_min=scan_age_min)
            continue

        # Currently under analysis in another thread
        with _lock:
            in_flight_now = ticker in _in_flight
        if in_flight_now:
            logger.debug("[AutoTrade P2] %s/%s already in-flight — skipping", strategy, ticker)
            continue

        passed.append(row)

    # Prioritize: stars × regime_mult + watch_score_hint
    mult_map = C.AUTO_QM_SIZE_MULTIPLIERS if strategy == "QM" else C.AUTO_ML_SIZE_MULTIPLIERS
    regime_mult = mult_map.get(regime, 0.5)
    passed.sort(
        key=lambda r: (float(r.get(star_key) or r.get("stars") or 0) * regime_mult),
        reverse=True,
    )
    return passed[:C.AUTO_TRADE_MAX_CANDIDATES]


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Deep analysis + watch score + decision
# ═══════════════════════════════════════════════════════════════════════════════

def _phase3_analyze_and_decide(row: dict, strategy: str, regime: str,
                               dry_run: bool, db,
                               scan_age_min: int = 0) -> dict:
    """
    Run deep analysis and watch-score evaluation for a single candidate.
    Returns an action dict (BUY / SKIP / BLOCKED).

    Race-condition safety: registers ticker in _in_flight for the duration
    of analysis so concurrent cycles cannot duplicate the evaluation.
    """
    ticker = row.get("ticker", "")
    star_key = "qm_star" if strategy == "QM" else "ml_star"
    screener_stars = float(row.get(star_key) or row.get("stars") or 0)

    # Register in-flight
    with _lock:
        if ticker in _in_flight:
            return {
                "ticker": ticker, "strategy": strategy,
                "screener_stars": screener_stars, "stars": 0.0,
                "watch_score": 0, "action": "SKIP",
                "reason": "Skipped — duplicate in-flight analysis",
                "iron_rules": [], "dim_summary": {}, "order_result": None,
            }
        _in_flight.add(ticker)

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
        "caution_flag": False,
    }

    try:
        # ── Phase 3A: Deep analysis (analyze_qm / analyze_ml) ────────────
        analysis = _run_deep_analysis(ticker, strategy)
        if not analysis or analysis.get("error"):
            result["reason"] = f"Analysis failed: {analysis.get('error', 'unknown') if analysis else 'no result'}"
            _log_action(db, row, strategy, screener_stars, screener_stars, 0, regime,
                        "SKIP", result["reason"], dry_run,
                        scan_data_age_min=scan_age_min)
            return result

        stars = float(analysis.get("capped_stars") or analysis.get("stars") or 0)
        result["stars"] = stars

        # Current price for audit log
        current_price = float(analysis.get("close", 0) or 0)

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
            _log_action(db, row, strategy, screener_stars, stars, 0, regime,
                        "BLOCKED", result["reason"], dry_run,
                        current_price=current_price, scan_data_age_min=scan_age_min)
            return result

        # ── ML: Decision tree + CAUTION handling ──────────────────────────
        caution_flag = False
        if strategy == "ML":
            dt = analysis.get("decision_tree", {})
            dt_verdict = dt.get("verdict", "NO-GO")
            if dt_verdict == "NO-GO":
                result["action"] = "SKIP"
                result["reason"] = f"ML Decision Tree: NO-GO ({dt.get('pass_count', 0)}/9)"
                _log_action(db, row, strategy, screener_stars, stars, 0, regime,
                            "SKIP", result["reason"], dry_run,
                            current_price=current_price, scan_data_age_min=scan_age_min)
                return result
            if dt_verdict == "CAUTION":
                caution_flag = True
                result["caution_flag"] = True
                logger.info("[AutoTrade P3] %s ML CAUTION — applying score boost + half position",
                            ticker)

        # ── Deep-analysis star gate (Phase 3, stricter than Phase 2) ──────
        min_deep = getattr(
            C,
            f"AUTO_TRADE_MIN_DEEP_STAR_{strategy}",
            getattr(C, f"AUTO_TRADE_MIN_STAR_{strategy}", 3.5),
        )
        if stars < min_deep:
            result["action"] = "SKIP"
            result["reason"] = f"Deep stars {stars:.1f} < min {min_deep} (Phase 3 gate)"
            _log_action(db, row, strategy, screener_stars, stars, 0, regime,
                        "SKIP", result["reason"], dry_run,
                        current_price=current_price, scan_data_age_min=scan_age_min,
                        caution_flag=caution_flag)
            return result

        # ── Phase 3B: Watch-score evaluation ──────────────────────────────
        watch = _evaluate_watch_score(ticker, strategy, analysis)
        watch_score = watch.get("watch_score", 0)
        iron_rules = watch.get("iron_rules", [])
        result["watch_score"] = watch_score
        result["iron_rules"] = iron_rules

        # ML CAUTION: require higher watch score
        min_watch = C.AUTO_TRADE_MIN_WATCH_SCORE
        if caution_flag:
            caution_boost = getattr(C, "AUTO_TRADE_ML_CAUTION_SCORE_BOOST", 15)
            min_watch = min_watch + caution_boost

        # Iron-rule block check
        blocking_rules = [r for r in iron_rules if r.get("severity") == "block"]
        if blocking_rules:
            result["action"] = "BLOCKED"
            labels = [r.get("label", "unknown") for r in blocking_rules]
            result["reason"] = f"Iron rule block: {', '.join(labels)}"
            _log_action(db, row, strategy, screener_stars, stars, watch_score, regime,
                        "BLOCKED", result["reason"], dry_run,
                        current_price=current_price, scan_data_age_min=scan_age_min,
                        iron_rules=iron_rules, dim_summary=result["dim_summary"],
                        caution_flag=caution_flag)
            return result

        # Watch-score gate (CAUTION: boosted threshold)
        if watch_score < min_watch:
            caution_note = f" (CAUTION +{min_watch - C.AUTO_TRADE_MIN_WATCH_SCORE})" if caution_flag else ""
            result["action"] = "SKIP"
            result["reason"] = (
                f"Watch score {watch_score} < min {min_watch}{caution_note}"
            )
            _log_action(db, row, strategy, screener_stars, stars, watch_score, regime,
                        "SKIP", result["reason"], dry_run,
                        current_price=current_price, scan_data_age_min=scan_age_min,
                        iron_rules=iron_rules, dim_summary=result["dim_summary"],
                        caution_flag=caution_flag)
            return result

        # ── Phase 4 + 5: Position sizing + order execution ───────────────
        order_result = _phase4_size_and_execute(
            ticker, strategy, stars, analysis, regime, dry_run, db,
            watch_score, iron_rules, result["dim_summary"],
            screener_stars=screener_stars,
            current_price=current_price,
            scan_age_min=scan_age_min,
            caution_flag=caution_flag,
        )
        result["action"] = order_result.get("action", "SKIP")
        result["reason"] = order_result.get("reason", "")
        result["order_result"] = order_result
        return result

    except Exception as exc:
        logger.exception("[AutoTrade P3] %s/%s error: %s", strategy, ticker, exc)
        result["reason"] = f"Exception: {exc}"
        _log_action(db, row, strategy, screener_stars, screener_stars, 0, regime,
                    "SKIP", result["reason"], dry_run,
                    scan_data_age_min=scan_age_min)
        return result
    finally:
        # Always remove from in-flight
        with _lock:
            _in_flight.discard(ticker)


# ═══════════════════════════════════════════════════════════════════════════════
# Deep analysis helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _run_deep_analysis(ticker: str, strategy: str) -> dict:
    """Run analyze_qm() or analyze_ml() and return the result dict."""
    if strategy == "QM":
        from modules.qm_analyzer import analyze_qm
        return analyze_qm(ticker, print_report=False)
    else:
        from modules.ml_analyzer import analyze_ml
        return analyze_ml(ticker, print_report=False)


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
    Mirrors the iron-rule + scoring logic from chart_api.py but as a standalone function.
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
    score = 50  # base score

    # ── Common signals ────────────────────────────────────────────────────
    # VWAP check (if available)
    if "VWAP" in df_intra.columns:
        vwap = float(df_intra["VWAP"].iloc[-1])
        if close > vwap:
            score += 10
        else:
            iron_rules.append({
                "label": "Below VWAP",
                "label_zh": "低於VWAP",
                "severity": "warn",
                "detail": f"Close ${close:.2f} < VWAP ${vwap:.2f}",
            })
            score -= 10

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

    # Volume confirmation
    avg_vol = float(analysis.get("avg_vol_20d", 0) or 0)
    if avg_vol > 0 and vol_today > 0:
        vol_ratio = vol_today / avg_vol
        if vol_ratio >= 1.5:
            score += 10  # volume surge
        elif vol_ratio < 0.5:
            score -= 5
    elif "dollar_volume_m" in analysis:
        score += 5  # has liquidity, give partial credit

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

    # Strategy-specific signals
    if strategy == "QM":
        score = _qm_watch_extras(score, iron_rules, close, df_intra, analysis)
    else:
        score = _ml_watch_extras(score, iron_rules, close, df_intra, analysis)

    # Clamp 0-100
    score = max(0, min(100, score))

    return {"watch_score": score, "iron_rules": iron_rules, "breakdown": {}}


def _qm_watch_extras(score: int, iron_rules: list, close: float,
                     df_intra: pd.DataFrame, analysis: dict) -> int:
    """QM-specific watch signals: breakout above pivot, SMA check."""
    # Above SMA10/20 (breakout continuation)
    trade_plan = analysis.get("trade_plan", {})
    pivot = trade_plan.get("pivot_price") or trade_plan.get("entry")
    if pivot:
        pivot = float(pivot)
        if close >= pivot:
            score += 10
        elif close < pivot * 0.98:
            score -= 10

    return score


def _ml_watch_extras(score: int, iron_rules: list, close: float,
                     df_intra: pd.DataFrame, analysis: dict) -> int:
    """ML-specific watch signals: EMA confluence, pullback completion."""
    # EMA alignment bonus
    ema_info = analysis.get("ema_alignment", {})
    if ema_info.get("all_stacked"):
        score += 5
    if ema_info.get("all_rising"):
        score += 5

    # AVWAP support check
    avwap = analysis.get("avwap_analysis", {})
    if avwap and avwap.get("near_avwap_support"):
        score += 10

    return score


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
                             dim_summary: dict, **kwargs) -> dict:
    """Calculate position size, check portfolio risk, then execute or log the order."""
    screener_stars  = kwargs.get("screener_stars", stars)
    current_price   = kwargs.get("current_price", 0.0)
    scan_age_min    = kwargs.get("scan_data_age_min", 0)
    caution_flag    = kwargs.get("caution_flag", False)

    trade_plan = analysis.get("trade_plan", {})
    close = float(analysis.get("close", 0))
    entry_price = float(trade_plan.get("entry") or trade_plan.get("entry_price") or close)
    stop_price  = float(trade_plan.get("day1_stop") or trade_plan.get("stop_price") or 0)

    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        reason = f"Invalid entry/stop: entry=${entry_price:.2f}, stop=${stop_price:.2f}"
        _log_action(db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score,
                    regime, "SKIP", reason, dry_run,
                    current_price=current_price, scan_data_age_min=scan_age_min,
                    caution_flag=caution_flag)
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
        _log_action(db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score,
                    regime, "SKIP", reason, dry_run,
                    current_price=current_price, scan_data_age_min=scan_age_min,
                    caution_flag=caution_flag)
        return {"action": "SKIP", "reason": reason}

    # ── Apply regime size multiplier ──────────────────────────────────────
    mult_map = C.AUTO_QM_SIZE_MULTIPLIERS if strategy == "QM" else C.AUTO_ML_SIZE_MULTIPLIERS
    multiplier = mult_map.get(regime, 0.5)

    # ML CAUTION: halve the position size
    if caution_flag:
        caution_mult = getattr(C, "AUTO_TRADE_ML_CAUTION_SIZE_MULT", 0.5)
        multiplier = multiplier * caution_mult
        logger.info("[AutoTrade P4] %s ML CAUTION — position size x%.2f", ticker, caution_mult)

    shares = max(1, int(shares * multiplier))

    # ── Portfolio-level risk check ────────────────────────────────────────
    order_value = entry_price * shares
    exposure_ok, exposure_reason, portfolio_pct = _check_portfolio_risk(
        ticker, order_value, db, dry_run
    )
    if not exposure_ok:
        _log_action(db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score,
                    regime, "BLOCKED", exposure_reason, dry_run,
                    current_price=current_price, scan_data_age_min=scan_age_min,
                    portfolio_exposure_pct=portfolio_pct, caution_flag=caution_flag,
                    iron_rules=iron_rules, dim_summary=dim_summary)
        return {"action": "BLOCKED", "reason": exposure_reason}

    # ── Compute limit order price (ATR-based or fixed buffer) ────────────
    order_type = C.AUTO_TRADE_ORDER_TYPE
    limit_price = None
    if order_type == "LMT":
        atr = float(analysis.get("atr", 0) or analysis.get("atr14", 0) or 0)
        limit_price = _compute_lmt_price(entry_price, atr)

    logger.info(
        "[AutoTrade P4] %s/%s: %d shares @ $%.2f, stop=$%.2f, "
        "regime_mult=%.2f, caution=%s, exposure=%.1f%%",
        strategy, ticker, shares, entry_price, stop_price,
        multiplier, caution_flag, portfolio_pct,
    )

    # ── Phase 5: Execute order ────────────────────────────────────────────
    order_id = None
    stop_order_id = None
    stop_attached = False

    if dry_run:
        reason = (
            f"DRY-RUN: Would buy {shares} shares @ ${limit_price or entry_price:.2f}"
            + (f" [CAUTION x{caution_mult:.1f}]" if caution_flag else "")
        )
        logger.info("[AutoTrade P5] %s %s", ticker, reason)
        _notify_telegram(
            f"\U0001f4cb [AutoTrade DRY-RUN] {ticker}\n"
            f"策略: {strategy} | {stars:.1f}\u2605 | ws={watch_score}\n"
            f"{reason}\n"
            f"市場環境: {regime} | 曝險: {portfolio_pct:.1f}%"
            + (f"\n\u26a0\ufe0f ML CAUTION — 半倉執行" if caution_flag else "")
        )
    else:
        order_result_raw = _execute_ibkr_order(
            ticker, shares, order_type, limit_price, stop_price,
        )
        if not order_result_raw.get("success"):
            reason = f"Order failed: {order_result_raw.get('message', 'unknown')}"
            _log_action(db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score,
                        regime, "SKIP", reason, dry_run,
                        current_price=current_price, scan_data_age_min=scan_age_min,
                        portfolio_exposure_pct=portfolio_pct, caution_flag=caution_flag,
                        iron_rules=iron_rules, dim_summary=dim_summary)
            return {"action": "SKIP", "reason": reason}

        order_id       = order_result_raw.get("order_id")
        stop_order_id  = order_result_raw.get("stop_order_id")
        stop_attached  = order_result_raw.get("stop_attached", False)

        # If STOP_ATTACH_CRITICAL and stop failed → void the buy record
        stop_critical = getattr(C, "AUTO_TRADE_STOP_ATTACH_CRITICAL", True)
        if C.AUTO_TRADE_ATTACH_STOP and stop_critical and not stop_attached:
            reason = (
                f"CRITICAL: Stop-loss attachment failed for {ticker} "
                f"(order_id={order_id}) — position left unprotected. "
                "Manually set stop immediately!"
            )
            logger.critical("[AutoTrade P5] %s", reason)
            _notify_telegram(
                f"\U0001f6a8 [AutoTrade CRITICAL] {ticker}\n"
                f"\u6b62\u640d\u55ae\u9644\u639b\u5931\u6557\uff01\u88f8\u6301\u5009\uff01\n"
                f"order_id={order_id} | {shares}\u80a1 @ ${limit_price or entry_price:.2f}\n"
                f"\u8acb\u7acb\u5373\u624b\u52d5\u8a2d\u7f6e\u6b62\u640d\uff01"
            )
            _log_action(db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score,
                        regime, "BLOCKED", reason, dry_run,
                        order_id=order_id, stop_order_id=stop_order_id,
                        stop_attached=False, current_price=current_price,
                        scan_data_age_min=scan_age_min,
                        portfolio_exposure_pct=portfolio_pct,
                        caution_flag=caution_flag,
                        iron_rules=iron_rules, dim_summary=dim_summary,
                        qty=shares, limit_price=limit_price, stop_price=stop_price,
                        order_type=order_type)
            return {"action": "BLOCKED", "reason": reason}

        reason = (
            f"BUY {shares} shares @ ${limit_price or entry_price:.2f}, "
            f"stop=${stop_price:.2f}, order_id={order_id}"
            + (f", stop_order_id={stop_order_id}" if stop_order_id else "")
            + (f" [CAUTION x{getattr(C,'AUTO_TRADE_ML_CAUTION_SIZE_MULT',0.5):.1f}]"
               if caution_flag else "")
        )

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
                note=f"Auto-{strategy} {stars:.1f}★ ws={watch_score}"
                     + (" [CAUTION]" if caution_flag else ""),
            )
        except Exception as exc:
            logger.warning("[AutoTrade] add_position failed for %s: %s", ticker, exc)

        # Telegram: real BUY
        _notify_telegram(
            f"\U0001f7e2 [AutoTrade BUY] {ticker}\n"
            f"策略: {strategy} | {stars:.1f}\u2605 | ws={watch_score}\n"
            f"{shares}\u80a1 @ ${limit_price or entry_price:.2f} | "
            f"\u6b62\u640d: ${stop_price:.2f}\n"
            f"\u5e02\u5834\u74b0\u5883: {regime} | \u66dd\u96aa: {portfolio_pct:.1f}%"
            + (f"\n\u26a0\ufe0f ML CAUTION — \u534a\u5009\u57f7\u884c" if caution_flag else "")
        )

    # ── Mark ticker as bought today + track pending order ─────────────────
    _bought_today[ticker] = time.time()
    if order_id and not dry_run:
        with _lock:
            _pending_orders[ticker] = {
                "order_id":      order_id,
                "stop_order_id": stop_order_id,
                "qty":           shares,
                "strategy":      strategy,
                "limit_price":   limit_price,
                "stop_price":    stop_price,
            }
            _status["pending_orders"] = len(_pending_orders)

    # ── Log to DuckDB ─────────────────────────────────────────────────────
    _log_action(
        db, {"ticker": ticker}, strategy, screener_stars, stars, watch_score, regime,
        "BUY", reason, dry_run,
        order_type=order_type,
        qty=shares,
        limit_price=limit_price,
        stop_price=stop_price,
        current_price=current_price or entry_price,
        scan_data_age_min=scan_age_min,
        portfolio_exposure_pct=portfolio_pct,
        caution_flag=caution_flag,
        order_id=order_id,
        stop_order_id=stop_order_id,
        stop_attached=stop_attached,
        iron_rules=iron_rules,
        dim_summary=dim_summary,
    )

    return {
        "action":      "BUY",
        "reason":      reason,
        "shares":      shares,
        "limit_price": limit_price,
        "stop_price":  stop_price,
        "order_id":    order_id,
    }


def _execute_ibkr_order(ticker: str, qty: int, order_type: str,
                        limit_price: Optional[float], stop_price: float) -> dict:
    """
    Place BUY order + optional stop-loss bracket via IBKR.

    Returns:
        {"success": bool, "order_id": int|None, "stop_order_id": int|None,
         "stop_attached": bool, "message": str}
    """
    from modules.ibkr_client import place_order

    # Main BUY order
    result = place_order(
        ticker=ticker,
        action="BUY",
        qty=qty,
        order_type=order_type,
        limit_price=limit_price if order_type == "LMT" else None,
    )

    stop_order_id = None
    stop_attached = False

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
                stop_order_id = stop_result.get("order_id")
                stop_attached = True
                logger.info("[AutoTrade] Stop-loss attached for %s @ $%.2f (stop_id=%s)",
                            ticker, stop_price, stop_order_id)
            else:
                logger.warning("[AutoTrade] Stop-loss order failed for %s: %s",
                               ticker, stop_result.get("message"))
        except Exception as exc:
            logger.warning("[AutoTrade] Stop-loss placement error for %s: %s", ticker, exc)

    return {
        **result,
        "stop_order_id": stop_order_id,
        "stop_attached": stop_attached,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_on_cooldown(ticker: str) -> bool:
    """Check if ticker was bought within the cooldown period."""
    last_buy = _bought_today.get(ticker)
    if last_buy is None:
        return False
    return (time.time() - last_buy) < C.AUTO_TRADE_COOLDOWN_SEC


def _count_buys_today(db, today_str: str, strategy: Optional[str] = None) -> int:
    """Count BUY actions logged today. Optionally filter by strategy (QM or ML)."""
    try:
        rows = db.query_auto_trade_log(days=1)
        return sum(
            1 for r in rows
            if str(r.get("trade_date", "")) == today_str
            and r.get("action") == "BUY"
            and (strategy is None or r.get("strategy") == strategy)
        )
    except Exception:
        return 0


def _reset_daily_counters():
    """Reset the in-memory bought-today tracker at midnight."""
    global _bought_today
    today = date.today().isoformat()
    _bought_today = {
        t: ts for t, ts in _bought_today.items()
        if date.fromtimestamp(ts).isoformat() == today
    }


def _log_action(db, row: dict, strategy: str, screener_stars: float,
                stars: float, watch_score: int, regime: str, action: str,
                reason: str, dry_run: bool, **kwargs):
    """Write a single action to the DuckDB auto_trade_log."""
    try:
        db.append_auto_trade({
            "trade_date":             date.today().isoformat(),
            "trade_time":             datetime.now().isoformat(),
            "ticker":                 row.get("ticker", ""),
            "strategy":               strategy,
            "screener_stars":         screener_stars,
            "stars":                  stars,
            "watch_score":            watch_score,
            "regime":                 regime,
            "action":                 action,
            "reason":                 reason,
            "order_type":             kwargs.get("order_type", ""),
            "qty":                    kwargs.get("qty", 0),
            "limit_price":            kwargs.get("limit_price"),
            "stop_price":             kwargs.get("stop_price"),
            "current_price":          kwargs.get("current_price"),
            "scan_data_age_min":      kwargs.get("scan_data_age_min", 0),
            "portfolio_exposure_pct": kwargs.get("portfolio_exposure_pct"),
            "caution_flag":           kwargs.get("caution_flag", False),
            "order_id":               kwargs.get("order_id"),
            "stop_order_id":          kwargs.get("stop_order_id"),
            "stop_attached":          kwargs.get("stop_attached", False),
            "dry_run":                dry_run,
            "iron_rules":             kwargs.get("iron_rules", []),
            "dim_summary":            kwargs.get("dim_summary", {}),
        })
    except Exception as exc:
        logger.warning("[AutoTrade] Failed to log action: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# New helpers — market hours, data freshness, portfolio risk, LMT buffer,
#               Telegram notification, graceful shutdown, recovery
# ═══════════════════════════════════════════════════════════════════════════════

def _is_market_hours() -> bool:
    """
    Return True if current US Eastern time is within configured trading hours.
    Defaults: 09:45 – 15:30 ET (skip first 15 min & last 30 min of session).
    """
    start_str = getattr(C, "AUTO_TRADE_START_TIME_ET", "09:45")
    end_str   = getattr(C, "AUTO_TRADE_END_TIME_ET",   "15:30")
    now_et    = datetime.now(_ET)
    try:
        sh, sm = [int(x) for x in start_str.split(":")]
        eh, em = [int(x) for x in end_str.split(":")]
        start_minutes = sh * 60 + sm
        end_minutes   = eh * 60 + em
        now_minutes   = now_et.hour * 60 + now_et.minute
        # Also check it's a weekday
        if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        return start_minutes <= now_minutes <= end_minutes
    except Exception as exc:
        logger.warning("[AutoTrade] _is_market_hours parse error: %s", exc)
        return True  # Fail-open: don't block trading on config error


def _get_file_age_minutes(file_path: Path) -> int:
    """Return how many minutes old a file is (based on mtime). Returns 9999 if not found."""
    try:
        mtime = file_path.stat().st_mtime
        return int((time.time() - mtime) / 60)
    except Exception:
        return 9999


def _get_held_tickers() -> set[str]:
    """
    Fetch the set of tickers currently held in IBKR open positions.
    Returns empty set on error — fail-open to avoid blocking on IBKR issues.
    """
    try:
        from modules.ibkr_client import get_positions
        positions = get_positions()
        return {p.get("ticker", p.get("symbol", "")).upper() for p in positions if p}
    except Exception as exc:
        logger.debug("[AutoTrade] get_held_tickers failed (non-critical): %s", exc)
        return set()


def _check_portfolio_risk(
    ticker: str, order_value: float, db, dry_run: bool
) -> tuple[bool, str, float]:
    """
    Validate portfolio-level risk before placing an order.

    Checks:
    1. Total existing open-position exposure ≤ AUTO_TRADE_MAX_TOTAL_EXPOSURE_PCT
    2. IBKR buying power ≥ order_value (if AUTO_TRADE_CHECK_BUYING_POWER)

    Returns:
        (ok: bool, reason: str, current_exposure_pct: float)
    """
    max_exposure = getattr(C, "AUTO_TRADE_MAX_TOTAL_EXPOSURE_PCT", 60.0)
    check_bp     = getattr(C, "AUTO_TRADE_CHECK_BUYING_POWER", True)
    account_size = getattr(C, "ACCOUNT_SIZE", 0)

    portfolio_pct = 0.0

    # ── Total exposure check ──────────────────────────────────────────────
    try:
        from modules import db as _db
        from modules.ibkr_client import get_account_summary
        acct = get_account_summary()
        nav  = float(acct.get("nav", 0) or acct.get("net_liquidation", 0))
        if nav <= 0 and account_size > 0:
            nav = account_size  # Fallback to configured account size

        if nav > 0:
            # Get open positions value
            from modules.ibkr_client import get_positions
            positions = get_positions()
            pos_value = sum(
                float(p.get("market_value", 0) or 0)
                for p in positions if p
            )
            portfolio_pct = (pos_value / nav) * 100
            if portfolio_pct > max_exposure:
                reason = (
                    f"Total exposure {portfolio_pct:.1f}% > max {max_exposure:.0f}% "
                    f"(positions=${pos_value:,.0f} / nav=${nav:,.0f})"
                )
                return False, reason, portfolio_pct
    except Exception as exc:
        logger.debug("[AutoTrade] Portfolio exposure check failed (non-critical): %s", exc)

    # ── Buying power check ────────────────────────────────────────────────
    if check_bp and not dry_run:
        try:
            from modules.ibkr_client import get_account_summary
            acct = get_account_summary()
            bp   = float(acct.get("buying_power", 0))
            if bp > 0 and bp < order_value:
                reason = (
                    f"Insufficient buying power: ${bp:,.0f} < order ${order_value:,.0f}"
                )
                return False, reason, portfolio_pct
        except Exception as exc:
            logger.debug("[AutoTrade] Buying power check failed (non-critical): %s", exc)

    return True, "", portfolio_pct


def _compute_lmt_price(entry_price: float, atr: float) -> float:
    """
    Compute limit order price.
    Uses ATR-based buffer if AUTO_TRADE_LMT_USE_ATR=True and ATR is available,
    otherwise falls back to fixed AUTO_TRADE_LMT_BUFFER_PCT.
    """
    use_atr  = getattr(C, "AUTO_TRADE_LMT_USE_ATR", True)
    atr_mult = getattr(C, "AUTO_TRADE_LMT_ATR_MULT", 0.10)
    if use_atr and atr > 0:
        buffer = atr * atr_mult
    else:
        buffer = entry_price * (C.AUTO_TRADE_LMT_BUFFER_PCT / 100)
    return round(entry_price + buffer, 2)


def _notify_telegram(message: str) -> None:
    """
    Send a Telegram notification. Best-effort — never raises.
    Uses TG_ADMIN_CHAT_ID from trader_config.
    """
    try:
        from modules.telegram_bot import send_message
        chat_id = getattr(C, "TG_ADMIN_CHAT_ID", None)
        if chat_id:
            send_message(message, chat_id=str(chat_id), parse_mode="HTML")
    except Exception as exc:
        logger.debug("[AutoTrade] Telegram notify failed (non-critical): %s", exc)


def _cancel_session_pending_orders() -> None:
    """
    Cancel all pending LMT orders that were placed during this engine session.
    Called during graceful shutdown to avoid orphaned orders.
    """
    if not _pending_orders:
        return

    try:
        from modules.ibkr_client import cancel_order
    except ImportError:
        logger.warning("[AutoTrade] ibkr_client unavailable — cannot cancel pending orders")
        return

    cancelled = []
    failed    = []
    for ticker, info in list(_pending_orders.items()):
        oid = info.get("order_id")
        if oid:
            try:
                result = cancel_order(oid)
                if result.get("success") or result.get("cancelled"):
                    cancelled.append(ticker)
                    logger.info("[AutoTrade] Cancelled pending order %s (order_id=%s)", ticker, oid)
                else:
                    failed.append(ticker)
                    logger.warning("[AutoTrade] Could not cancel order %s: %s",
                                   oid, result.get("message"))
            except Exception as exc:
                failed.append(ticker)
                logger.warning("[AutoTrade] cancel_order error for %s: %s", ticker, exc)

    if cancelled or failed:
        msg = (
            f"\U0001f6d1 [AutoTrade] 引擎停止 — 取消掛單\n"
            f"已取消: {', '.join(cancelled) or '無'}\n"
            f"取消失敗: {', '.join(failed) or '無'}"
        )
        _notify_telegram(msg)
        logger.info("[AutoTrade] Graceful shutdown: cancelled=%s, failed=%s", cancelled, failed)

    with _lock:
        _pending_orders.clear()
        _status["pending_orders"] = 0


def _restore_todays_buys(db) -> None:
    """
    On engine startup, reload today's BUY history from DuckDB to restore
    the _bought_today cooldown map (recovery after restart).
    """
    global _bought_today
    today_str = date.today().isoformat()
    try:
        rows = db.query_auto_trade_log(days=1)
        restored = 0
        for r in rows:
            if (str(r.get("trade_date", "")) == today_str
                    and r.get("action") == "BUY"):
                ticker = r.get("ticker", "")
                if ticker and ticker not in _bought_today:
                    # Use trade_time as approximate timestamp
                    try:
                        ts = datetime.fromisoformat(
                            str(r.get("trade_time", ""))).timestamp()
                    except Exception:
                        ts = time.time()
                    _bought_today[ticker] = ts
                    restored += 1
        if restored:
            logger.info("[AutoTrade] Restored %d today's buy(s) from DuckDB: %s",
                        restored, list(_bought_today.keys()))
    except Exception as exc:
        logger.warning("[AutoTrade] _restore_todays_buys failed: %s", exc)
