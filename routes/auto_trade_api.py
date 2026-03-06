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


# ═══════════════════════════════════════════════════════════════════════════════
# Position Control  (3-pool status, pool log, exit log)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/position-control/status", methods=["GET"])
def api_pool_status():
    """Return live snapshot of all 3 pools (ML/QM/FREE)."""
    try:
        from modules.position_controller import get_pool_status
        status = get_pool_status()
        return jsonify({"ok": True, **status})
    except Exception as exc:
        logger.exception("[PoolAPI] status error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/position-control/pool-log", methods=["GET"])
def api_pool_log():
    """Query pool allocation/release log from DuckDB."""
    days = request.args.get("days", 30, type=int)
    days = max(1, min(days, 365))
    try:
        from modules import db
        rows = db.query_pool_log(days=days)
        return jsonify({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.exception("[PoolAPI] pool-log error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/position-control/exit-log", methods=["GET"])
def api_exit_log():
    """Query exit/partial-sell log from DuckDB."""
    days = request.args.get("days", 30, type=int)
    days = max(1, min(days, 365))
    try:
        from modules import db
        rows = db.query_exit_log(days=days)
        return jsonify({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.exception("[PoolAPI] exit-log error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Exit Engine  (status, manual trigger, config)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/exit-engine/status", methods=["GET"])
def api_exit_engine_status():
    """Return current exit engine status + config."""
    try:
        from modules.exit_engine import get_exit_status
        status = get_exit_status()
        status["config"] = {
            "enabled":             getattr(C, "EXIT_ENGINE_ENABLED", False),
            "dry_run":             getattr(C, "EXIT_DRY_RUN", True),
            "check_interval_sec":  getattr(C, "EXIT_CHECK_INTERVAL_SEC", 60),
            "time_stop_days":      getattr(C, "EXIT_TIME_STOP_DAYS", 5),
            "max_sells_per_cycle": getattr(C, "EXIT_MAX_SELLS_PER_CYCLE", 3),
            "order_type":          getattr(C, "EXIT_ORDER_TYPE", "MKT"),
        }
        return jsonify({"ok": True, **status})
    except Exception as exc:
        logger.exception("[ExitAPI] status error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/exit-engine/check-now", methods=["POST"])
def api_exit_engine_check_now():
    """Manually trigger one exit engine check cycle (always dry-run)."""
    try:
        from modules.exit_engine import check_all_positions
        results = check_all_positions(dry_run=True)
        exits = [r for r in results if r.get("action_taken") not in ("HOLD", "ERROR", None)]
        return jsonify({
            "ok": True,
            "positions_checked": len(results),
            "exits_found": len(exits),
            "results": results,
        })
    except Exception as exc:
        logger.exception("[ExitAPI] check-now error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
