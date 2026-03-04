"""SEPA and QM Backtest API routes."""

import uuid
import logging
import threading
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify

from routes.helpers import (
    _LOG_DIR,
    _bt_jobs, _bt_lock,
    _qm_bt_jobs, _qm_bt_lock,
    _clean,
)

bp = Blueprint("backtest_api", __name__)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SEPA Backtest
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    body = request.get_json(force=True) or {}
    ticker = str(body.get("ticker", "")).strip().upper()
    min_vcp_score = int(body.get("min_vcp_score", 35))
    outcome_days = int(body.get("outcome_days", 120))

    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    jid = f"bt_{ticker}_{uuid.uuid4().hex[:8]}"

    bt_log_file = _LOG_DIR / f"backtest_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    bt_handler = logging.FileHandler(bt_log_file, encoding="utf-8")
    bt_handler.setLevel(logging.DEBUG)
    bt_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    _BT_LOGGERS = ["modules.backtester", "modules.vcp_detector", "modules.data_pipeline"]
    for ln in _BT_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)
        _lg.addHandler(bt_handler)

    with _bt_lock:
        _bt_jobs[jid] = {
            "status": "running", "pct": 0, "msg": "Initializing…",
            "result": None, "error": None, "log_file": bt_log_file.name,
        }

    def _run():
        from modules.backtester import run_backtest

        def _cb(pct, msg):
            with _bt_lock:
                _bt_jobs[jid]["pct"] = pct
                _bt_jobs[jid]["msg"] = msg

        try:
            logging.getLogger("modules.backtester").info(
                "Starting backtest: %s  min_score=%s  outcome_days=%s (job %s)",
                ticker, min_vcp_score, outcome_days, jid)
            result = run_backtest(
                ticker=ticker,
                min_vcp_score=min_vcp_score,
                outcome_days=outcome_days,
                progress_cb=_cb,
                log_file=bt_log_file,
            )
            with _bt_lock:
                _bt_jobs[jid]["status"] = "done"
                _bt_jobs[jid]["result"] = _clean(result)
            logging.getLogger("modules.backtester").info(
                "Backtest complete: %s  signals=%s  win_rate=%s%%",
                ticker,
                result.get("summary", {}).get("total_signals"),
                result.get("summary", {}).get("win_rate_pct"))
        except Exception as exc:
            logger.exception("[Backtest] run error for %s", ticker)
            logging.getLogger("modules.backtester").error("Backtest error: %s", exc)
            with _bt_lock:
                _bt_jobs[jid]["status"] = "error"
                _bt_jobs[jid]["error"] = str(exc)
        finally:
            for ln in _BT_LOGGERS:
                logging.getLogger(ln).removeHandler(bt_handler)
            bt_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "log_file": bt_log_file.name})


@bp.route("/api/backtest/status/<jid>")
def api_backtest_status(jid):
    with _bt_lock:
        job = _bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({
        "ok": True, "status": job["status"], "pct": job["pct"],
        "msg": job["msg"], "result": job.get("result"),
        "error": job.get("error"),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# QM Backtest
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/qm/backtest/run", methods=["POST"])
def api_qm_backtest_run():
    body = request.get_json(force=True) or {}
    ticker = str(body.get("ticker", "")).strip().upper()
    min_star = float(body.get("min_star", 3.0))
    max_hold_days = int(body.get("max_hold_days", 120))
    debug_mode = bool(body.get("debug", False))

    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    jid = f"qmbt_{ticker}_{uuid.uuid4().hex[:8]}"

    bt_log = _LOG_DIR / f"qm_backtest_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    bt_handler = logging.FileHandler(bt_log, encoding="utf-8")
    bt_handler.setLevel(logging.DEBUG)
    bt_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    _QM_BT_LOGGERS = [
        "modules.qm_backtester", "modules.qm_analyzer",
        "modules.qm_setup_detector", "modules.data_pipeline",
    ]
    for ln in _QM_BT_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)
        _lg.addHandler(bt_handler)

    with _qm_bt_lock:
        _qm_bt_jobs[jid] = {
            "status": "running", "pct": 0, "msg": "Initializing…",
            "result": None, "error": None, "log_file": bt_log.name,
        }

    def _run():
        from modules.qm_backtester import run_qm_backtest

        def _cb(pct, msg):
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["pct"] = pct
                _qm_bt_jobs[jid]["msg"] = msg

        try:
            logging.getLogger("modules.qm_backtester").info(
                "Starting QM backtest: %s  min_star=%s  max_hold=%s  debug=%s (job %s)",
                ticker, min_star, max_hold_days, debug_mode, jid)
            result = run_qm_backtest(
                ticker=ticker,
                min_star=min_star,
                max_hold_days=max_hold_days,
                progress_cb=_cb,
                log_file=bt_log,
                debug_mode=debug_mode,
            )
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["status"] = "complete"
                _qm_bt_jobs[jid]["pct"] = 100
                _qm_bt_jobs[jid]["result"] = _clean(result)
            logging.getLogger("modules.qm_backtester").info(
                "QM backtest complete: %s  signals=%s  win_rate=%s%%",
                ticker,
                result.get("summary", {}).get("total_signals"),
                result.get("summary", {}).get("win_rate_pct"))
        except Exception as exc:
            logger.exception("[QM Backtest] run error for %s", ticker)
            logging.getLogger("modules.qm_backtester").error("QM backtest error: %s", exc)
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["status"] = "error"
                _qm_bt_jobs[jid]["error"] = str(exc)
        finally:
            for ln in _QM_BT_LOGGERS:
                logging.getLogger(ln).removeHandler(bt_handler)
            bt_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "log_file": bt_log.name,
                    "log_url": f"/api/qm/backtest/log/{jid}"})


@bp.route("/api/qm/backtest/status/<jid>")
def api_qm_backtest_status(jid):
    with _qm_bt_lock:
        job = _qm_bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({
        "ok": True, "status": job["status"], "pct": job["pct"],
        "msg": job["msg"], "result": job.get("result"),
        "error": job.get("error"),
    })


@bp.route("/api/qm/backtest/log/<jid>")
def api_qm_backtest_log(jid):
    """Return the last 100 lines of the backtest log."""
    with _qm_bt_lock:
        job = _qm_bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    log_file = job.get("log_file")
    if not log_file:
        return jsonify({"ok": False, "error": "No log file"}), 404

    try:
        log_path = (Path(log_file) if log_file.startswith("/") or ":" in log_file
                    else _LOG_DIR / log_file)
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return jsonify({"ok": True, "lines": lines[-100:],
                                "total_lines": len(lines)})
        else:
            return jsonify({"ok": False, "error": f"Log not found: {log_path}"}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
