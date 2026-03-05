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
    """Stop the auto-trade polling loop."""
    result = _engine().stop()
    code = 200 if result["ok"] else 409
    return jsonify(result), code


@bp.route("/api/auto-trade/status", methods=["GET"])
def api_auto_trade_status():
    """Poll current auto-trade engine status."""
    status = _engine().get_status()
    status["config"] = {
        "enabled":           C.AUTO_TRADE_ENABLED,
        "dry_run_default":   C.AUTO_TRADE_DRY_RUN,
        "max_buys_per_day":  C.AUTO_TRADE_MAX_BUYS_PER_DAY,
        "min_star_qm":       C.AUTO_TRADE_MIN_STAR_QM,
        "min_star_ml":       C.AUTO_TRADE_MIN_STAR_ML,
        "min_watch_score":   C.AUTO_TRADE_MIN_WATCH_SCORE,
        "poll_interval_sec": C.AUTO_TRADE_POLL_INTERVAL_SEC,
        "order_type":        C.AUTO_TRADE_ORDER_TYPE,
        "attach_stop":       C.AUTO_TRADE_ATTACH_STOP,
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
