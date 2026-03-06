"""Auto-Trade API routes — start/stop engine, poll status, query history."""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Blueprint, jsonify, request

import trader_config as C

bp = Blueprint("auto_trade_api", __name__)
logger = logging.getLogger(__name__)

_HK_TZ = ZoneInfo("Asia/Hong_Kong")
_US_TZ = ZoneInfo("America/New_York")


_RUNTIME_CFG_FIELDS = {
    "auto_trade_enabled": "AUTO_TRADE_ENABLED",
    "auto_trade_dry_run": "AUTO_TRADE_DRY_RUN",
    "auto_trade_max_buys_per_day": "AUTO_TRADE_MAX_BUYS_PER_DAY",
    "auto_trade_min_star_qm": "AUTO_TRADE_MIN_STAR_QM",
    "auto_trade_min_star_ml": "AUTO_TRADE_MIN_STAR_ML",
    "auto_trade_min_watch_score": "AUTO_TRADE_MIN_WATCH_SCORE",
    "auto_trade_poll_interval_sec": "AUTO_TRADE_POLL_INTERVAL_SEC",
    "auto_trade_order_type": "AUTO_TRADE_ORDER_TYPE",
    "auto_trade_attach_stop": "AUTO_TRADE_ATTACH_STOP",
    "auto_trade_max_candidates_qm": "AUTO_TRADE_MAX_CANDIDATES_QM",
    "auto_trade_max_candidates_ml": "AUTO_TRADE_MAX_CANDIDATES_ML",
}


def _settings_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / C.SETTINGS_FILE


def _read_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_settings(settings: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _runtime_cfg_defaults() -> dict:
    fallback = int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES", 10))
    return {
        "auto_trade_enabled": bool(getattr(C, "AUTO_TRADE_ENABLED", False)),
        "auto_trade_dry_run": bool(getattr(C, "AUTO_TRADE_DRY_RUN", True)),
        "auto_trade_max_buys_per_day": int(getattr(C, "AUTO_TRADE_MAX_BUYS_PER_DAY", 3)),
        "auto_trade_min_star_qm": float(getattr(C, "AUTO_TRADE_MIN_STAR_QM", 3.5)),
        "auto_trade_min_star_ml": float(getattr(C, "AUTO_TRADE_MIN_STAR_ML", 3.5)),
        "auto_trade_min_watch_score": int(getattr(C, "AUTO_TRADE_MIN_WATCH_SCORE", 70)),
        "auto_trade_poll_interval_sec": int(getattr(C, "AUTO_TRADE_POLL_INTERVAL_SEC", 300)),
        "auto_trade_order_type": str(getattr(C, "AUTO_TRADE_ORDER_TYPE", "LMT")).upper(),
        "auto_trade_attach_stop": bool(getattr(C, "AUTO_TRADE_ATTACH_STOP", True)),
        "auto_trade_max_candidates_qm": int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES_QM", fallback)),
        "auto_trade_max_candidates_ml": int(getattr(C, "AUTO_TRADE_MAX_CANDIDATES_ML", fallback)),
    }


def _apply_runtime_cfg_from_settings() -> dict:
    """Load persisted auto-trade runtime config and apply to trader_config module."""
    defaults = _runtime_cfg_defaults()
    settings = _read_settings()
    runtime_cfg = {k: settings.get(k, v) for k, v in defaults.items()}
    for key, attr in _RUNTIME_CFG_FIELDS.items():
        setattr(C, attr, runtime_cfg[key])
    return runtime_cfg


def _validate_runtime_patch(data: dict) -> tuple[dict, str]:
    """Validate partial runtime-config patch payload."""
    if not isinstance(data, dict):
        return {}, "Invalid JSON payload"

    clean: dict = {}
    for k, v in data.items():
        if k not in _RUNTIME_CFG_FIELDS:
            return {}, f"Unsupported field: {k}"
        if k in ("auto_trade_enabled", "auto_trade_dry_run", "auto_trade_attach_stop"):
            clean[k] = bool(v)
        elif k == "auto_trade_order_type":
            ov = str(v).upper().strip()
            if ov not in ("LMT", "MKT"):
                return {}, "auto_trade_order_type must be LMT or MKT"
            clean[k] = ov
        elif k in ("auto_trade_min_star_qm", "auto_trade_min_star_ml"):
            fv = float(v)
            if fv < 0 or fv > 6:
                return {}, f"{k} must be between 0 and 6"
            clean[k] = round(fv, 2)
        elif k == "auto_trade_min_watch_score":
            iv = int(v)
            if iv < 0 or iv > 100:
                return {}, "auto_trade_min_watch_score must be between 0 and 100"
            clean[k] = iv
        elif k == "auto_trade_poll_interval_sec":
            iv = int(v)
            if iv < 30 or iv > 3600:
                return {}, "auto_trade_poll_interval_sec must be between 30 and 3600"
            clean[k] = iv
        elif k in ("auto_trade_max_buys_per_day", "auto_trade_max_candidates_qm", "auto_trade_max_candidates_ml"):
            iv = int(v)
            if iv < 1 or iv > 100:
                return {}, f"{k} must be between 1 and 100"
            clean[k] = iv
    return clean, ""


def _parse_iso_to_utc(dt_raw) -> datetime | None:
    if not dt_raw:
        return None

    # Handle datetime objects returned directly from DuckDB driver.
    if isinstance(dt_raw, datetime):
        dt = dt_raw
        if dt.tzinfo is None:
            # Legacy rows were written with local HK wall-clock time.
            dt = dt.replace(tzinfo=_HK_TZ)
        return dt.astimezone(timezone.utc)

    s = str(dt_raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # Legacy rows were naive; treat as HK local time.
            dt = dt.replace(tzinfo=_HK_TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _augment_history_time_fields(row: dict) -> dict:
    out = dict(row)
    dt_utc = _parse_iso_to_utc(out.get("trade_time"))
    if not dt_utc:
        out["trade_time_hk"] = ""
        out["trade_time_us"] = ""
        out["trade_time_utc"] = ""
        return out
    out["trade_time_utc"] = dt_utc.isoformat()
    out["trade_time_hk"] = dt_utc.astimezone(_HK_TZ).strftime("%Y-%m-%d %H:%M:%S HKT")
    out["trade_time_us"] = dt_utc.astimezone(_US_TZ).strftime("%Y-%m-%d %H:%M:%S ET")
    return out


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
    _apply_runtime_cfg_from_settings()
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
    runtime_cfg = _apply_runtime_cfg_from_settings()
    status = _engine().get_status()
    status["config"] = {
        "enabled":           runtime_cfg["auto_trade_enabled"],
        "dry_run_default":   runtime_cfg["auto_trade_dry_run"],
        "max_buys_per_day":  runtime_cfg["auto_trade_max_buys_per_day"],
        "min_star_qm":       runtime_cfg["auto_trade_min_star_qm"],
        "min_star_ml":       runtime_cfg["auto_trade_min_star_ml"],
        "min_watch_score":   runtime_cfg["auto_trade_min_watch_score"],
        "poll_interval_sec": runtime_cfg["auto_trade_poll_interval_sec"],
        "order_type":        runtime_cfg["auto_trade_order_type"],
        "attach_stop":       runtime_cfg["auto_trade_attach_stop"],
        "max_candidates_qm": runtime_cfg["auto_trade_max_candidates_qm"],
        "max_candidates_ml": runtime_cfg["auto_trade_max_candidates_ml"],
    }
    status["worker_mode"] = "background_thread"
    return jsonify(status)


@bp.route("/api/auto-trade/config", methods=["GET"])
def api_auto_trade_config_get():
    """Get runtime-editable auto-trade configuration."""
    runtime_cfg = _apply_runtime_cfg_from_settings()
    return jsonify({"ok": True, **runtime_cfg})


@bp.route("/api/auto-trade/config", methods=["PATCH"])
def api_auto_trade_config_patch():
    """Patch runtime auto-trade config and persist to data/settings.json."""
    raw = request.get_json(silent=True) or {}
    clean, err = _validate_runtime_patch(raw)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    settings = _read_settings()
    settings.update(clean)
    settings["last_updated"] = datetime.now(timezone.utc).isoformat()
    _write_settings(settings)
    runtime_cfg = _apply_runtime_cfg_from_settings()
    return jsonify({"ok": True, "message": "Auto-trade config saved", **runtime_cfg})


# ═══════════════════════════════════════════════════════════════════════════════
# History
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/auto-trade/history", methods=["GET"])
def api_auto_trade_history():
    """Query auto-trade log from DuckDB."""
    _apply_runtime_cfg_from_settings()
    days = request.args.get("days", 30, type=int)
    days = max(1, min(days, 365))
    try:
        from modules import db
        rows = db.query_auto_trade_log(days=days)
        rows = [_augment_history_time_fields(r) for r in rows]
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
