"""Auto-Trade API routes — start/stop engine, poll status, query history."""

import logging
from flask import Blueprint, jsonify, request

import trader_config as C

bp = Blueprint("auto_trade_api", __name__)
logger = logging.getLogger(__name__)


def _engine():
    """Lazy-import auto_trader module."""
    from modules import auto_trader
    return auto_trader


# ═══════════════════════════════════════════════════════════════════════════════
# Engine control
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/auto-trade/start", methods=["POST"])
def api_auto_trade_start():
    """Start the auto-trade polling loop."""
    data = request.get_json(silent=True) or {}
    dry_run = data.get("dry_run", C.AUTO_TRADE_DRY_RUN)
    result = _engine().start(dry_run=dry_run)
    code = 200 if result["ok"] else 409
    return jsonify(result), code


@bp.route("/api/auto-trade/stop", methods=["POST"])
def api_auto_trade_stop():
    """Stop the auto-trade polling loop (gracefully cancels pending orders)."""
    result = _engine().stop()
    code = 200 if result["ok"] else 409
    return jsonify(result), code


@bp.route("/api/auto-trade/status", methods=["GET"])
def api_auto_trade_status():
    """Poll current auto-trade engine status."""
    status = _engine().get_status()
    status["config"] = {
        "enabled":               C.AUTO_TRADE_ENABLED,
        "dry_run_default":       C.AUTO_TRADE_DRY_RUN,
        "max_buys_qm":           getattr(C, "AUTO_TRADE_MAX_BUYS_PER_DAY_QM", 2),
        "max_buys_ml":           getattr(C, "AUTO_TRADE_MAX_BUYS_PER_DAY_ML", 2),
        "min_screener_star_qm":  getattr(C, "AUTO_TRADE_MIN_SCREENER_STAR_QM", C.AUTO_TRADE_MIN_STAR_QM),
        "min_deep_star_qm":      getattr(C, "AUTO_TRADE_MIN_DEEP_STAR_QM", 4.0),
        "min_screener_star_ml":  getattr(C, "AUTO_TRADE_MIN_SCREENER_STAR_ML", C.AUTO_TRADE_MIN_STAR_ML),
        "min_deep_star_ml":      getattr(C, "AUTO_TRADE_MIN_DEEP_STAR_ML", 4.0),
        "min_watch_score":       C.AUTO_TRADE_MIN_WATCH_SCORE,
        "ml_caution_score_boost": getattr(C, "AUTO_TRADE_ML_CAUTION_SCORE_BOOST", 15),
        "ml_caution_size_mult":  getattr(C, "AUTO_TRADE_ML_CAUTION_SIZE_MULT", 0.5),
        "poll_interval_sec":     C.AUTO_TRADE_POLL_INTERVAL_SEC,
        "order_type":            C.AUTO_TRADE_ORDER_TYPE,
        "lmt_use_atr":           getattr(C, "AUTO_TRADE_LMT_USE_ATR", True),
        "lmt_atr_mult":          getattr(C, "AUTO_TRADE_LMT_ATR_MULT", 0.10),
        "attach_stop":           C.AUTO_TRADE_ATTACH_STOP,
        "stop_attach_critical":  getattr(C, "AUTO_TRADE_STOP_ATTACH_CRITICAL", True),
        "start_time_et":         getattr(C, "AUTO_TRADE_START_TIME_ET", "09:45"),
        "end_time_et":           getattr(C, "AUTO_TRADE_END_TIME_ET", "15:30"),
        "scan_max_age_min":      getattr(C, "AUTO_TRADE_SCAN_MAX_AGE_MIN", 30),
        "max_total_exposure_pct": getattr(C, "AUTO_TRADE_MAX_TOTAL_EXPOSURE_PCT", 60.0),
        "check_buying_power":    getattr(C, "AUTO_TRADE_CHECK_BUYING_POWER", True),
    }
    return jsonify(status)


# ═══════════════════════════════════════════════════════════════════════════════
# History
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/auto-trade/history", methods=["GET"])
def api_auto_trade_history():
    """Query auto-trade log from DuckDB."""
    days = request.args.get("days", 30, type=int)
    days = max(1, min(days, 365))
    try:
        from modules import db
        rows = db.query_auto_trade_log(days=days)
        return jsonify({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.exception("[AutoTradeAPI] history error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Pending orders
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/auto-trade/pending-orders", methods=["GET"])
def api_auto_trade_pending_orders():
    """Return session-tracked pending (unconfirmed) orders."""
    from modules import auto_trader
    with auto_trader._lock:
        orders = dict(auto_trader._pending_orders)
    return jsonify({"ok": True, "pending": orders, "count": len(orders)})


@bp.route("/api/auto-trade/cancel-pending", methods=["POST"])
def api_auto_trade_cancel_pending():
    """Cancel all session-placed pending LMT orders (graceful cleanup)."""
    from modules import auto_trader
    try:
        auto_trader._cancel_session_pending_orders()
        return jsonify({"ok": True, "message": "Pending orders cancelled"})
    except Exception as exc:
        logger.exception("[AutoTradeAPI] cancel-pending error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
