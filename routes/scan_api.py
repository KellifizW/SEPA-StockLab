"""Scan API routes — SEPA, QM, ML, and Combined scan endpoints."""

import logging
import threading
from datetime import date, datetime
from flask import Blueprint, request, jsonify

import trader_config as C
from routes.helpers import (
    ROOT, _LOG_DIR,
    _new_job, _get_cancel, _finish_job, _get_job,
    _jobs, _jobs_lock,
    _cancel_events, _qm_job_ids, _ml_job_ids,
    _scan_file_handlers, _scan_log_paths,
    _clean, _sanitize_for_json, df_to_rows,
    _save_last_scan, _load_last_scan,
    _save_qm_last_scan, _load_qm_last_scan,
    _save_ml_last_scan, _load_ml_last_scan,
    _save_combined_last, _load_combined_last,
    _save_combined_scan_csv,
    _normalize_market, _load_market_mode,
    _cache_lock,
)

bp = Blueprint("scan_api", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SEPA Scan
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/scan/run", methods=["POST"])
def api_scan_run():
    data = request.get_json(silent=True) or {}
    market = _normalize_market(data.get("market") or _load_market_mode())
    refresh_rs = data.get("refresh_rs", False)
    stage1_source = data.get("stage1_source") or None
    jid = _new_job()
    cancel_ev = _get_cancel(jid)

    scan_log_file = _LOG_DIR / f"scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _SCAN_LOGGERS = ["modules.screener", "modules.rs_ranking", "modules.data_pipeline"]
    for ln in _SCAN_LOGGERS:
        logging.getLogger(ln).addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.screener import run_scan, set_scan_cancel
            set_scan_cancel(cancel_ev)
            scan_result = run_scan(refresh_rs=refresh_rs, stage1_source=stage1_source)
            if isinstance(scan_result, tuple):
                df_passed, df_all = scan_result
            else:
                df_passed = scan_result
                df_all = scan_result
            rows = df_to_rows(df_passed, "SEPA")
            all_rows = df_to_rows(df_all, "SEPA-all")
            _save_last_scan(rows, all_rows=all_rows, market=market)
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
        except Exception as exc:
            logging.exception("Scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for ln in _SCAN_LOGGERS:
                    logging.getLogger(ln).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/scan/cancel/<jid>", methods=["POST"])
def api_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…", "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@bp.route("/api/scan/last", methods=["GET"])
def api_scan_last():
    market = _normalize_market(request.args.get("market") or _load_market_mode())
    data = _load_last_scan(market=market)
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@bp.route("/api/scan/cache-info", methods=["GET"])
def api_scan_cache_info():
    try:
        today = date.today().isoformat()
        cache_dir = ROOT / C.PRICE_CACHE_DIR

        rs_file = ROOT / C.DATA_DIR / "rs_cache.csv"
        rs_cached = False
        rs_count = 0
        if rs_file.exists():
            try:
                with open(rs_file, "r", encoding="utf-8") as f:
                    header = f.readline().strip()
                    cols = header.split(",")
                    date_idx = cols.index("CacheDate") if "CacheDate" in cols else -1
                    line_count = 0
                    cached_date = ""
                    for line in f:
                        line_count += 1
                        if line_count == 1 and date_idx >= 0:
                            parts = line.strip().split(",")
                            if len(parts) > date_idx:
                                cached_date = parts[date_idx]
                    if cached_date == today and line_count > 10:
                        rs_cached = True
                        rs_count = line_count
            except Exception:
                pass

        price_cached_today = 0
        if cache_dir.exists():
            metas = list(cache_dir.glob("*_2y.meta"))
            if len(metas) > 200:
                import random
                sample = random.sample(metas, 200)
                hits = sum(1 for m in sample if m.read_text().strip() == today)
                price_cached_today = int(hits / 200 * len(metas))
            else:
                for meta in metas:
                    try:
                        if meta.read_text().strip() == today:
                            price_cached_today += 1
                    except Exception:
                        pass

        fund_cached_today = 0
        if cache_dir.exists():
            fmetas = list(cache_dir.glob("*_fundamentals.fmeta"))
            if len(fmetas) > 200:
                import random
                sample = random.sample(fmetas, 200)
                hits = sum(1 for m in sample if m.read_text().strip() == today)
                fund_cached_today = int(hits / 200 * len(fmetas))
            else:
                for fmeta in fmetas:
                    try:
                        if fmeta.read_text().strip() == today:
                            fund_cached_today += 1
                    except Exception:
                        pass

        finviz_cached = False
        try:
            from modules.data_pipeline import _finviz_cache
            finviz_cached = len(_finviz_cache) > 0
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "cache": {
                "rs_cached": rs_cached,
                "rs_count": rs_count,
                "price_cached_today": price_cached_today,
                "fund_cached_today": fund_cached_today,
                "finviz_cached": finviz_cached,
                "finviz_ttl_hours": getattr(C, "FINVIZ_CACHE_TTL_HOURS", 4),
            },
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/scan/status/<jid>")
def api_scan_status(jid):
    try:
        job = _get_job(jid)
        return jsonify(_sanitize_for_json(job))
    except Exception as e:
        logging.error(f"[SCAN_STATUS {jid}] Exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# QM Scan
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/qm/scan/run", methods=["POST"])
def api_qm_scan_run():
    data = request.get_json(silent=True) or {}
    market = _normalize_market(data.get("market") or _load_market_mode())
    min_star = float(data.get("min_star", getattr(C, "QM_SCAN_MIN_STAR", 3.0)))
    top_n = int(data.get("top_n", getattr(C, "QM_SCAN_TOP_N", 50)))
    stage1_source = data.get("stage1_source") or None
    jid = _new_job()
    cancel_ev = _get_cancel(jid)
    _qm_job_ids.add(jid)

    scan_log_file = _LOG_DIR / f"qm_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _QM_SCAN_LOGGERS = ["modules.qm_screener", "modules.qm_analyzer", "modules.data_pipeline"]
    for ln in _QM_SCAN_LOGGERS:
        logging.getLogger(ln).addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.qm_screener import run_qm_scan, set_qm_scan_cancel
            set_qm_scan_cancel(cancel_ev)
            result = run_qm_scan(min_star=min_star, top_n=top_n, stage1_source=stage1_source)
            if isinstance(result, tuple):
                df_passed, df_all = result
            else:
                df_passed = result
                df_all = result
            rows = df_to_rows(df_passed, "QM")
            all_rows = df_to_rows(df_all, "QM-all")
            _save_qm_last_scan(rows, all_rows=all_rows, market=market)
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
        except Exception as exc:
            logging.exception("QM scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            _qm_job_ids.discard(jid)
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for ln in _QM_SCAN_LOGGERS:
                    logging.getLogger(ln).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/qm/scan/cancel/<jid>", methods=["POST"])
def api_qm_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…", "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@bp.route("/api/qm/scan/last", methods=["GET"])
def api_qm_scan_last():
    market = _normalize_market(request.args.get("market") or _load_market_mode())
    data = _load_qm_last_scan(market=market)
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@bp.route("/api/qm/scan/status/<jid>")
def api_qm_scan_status(jid):
    try:
        job = _get_job(jid)
        return jsonify(_sanitize_for_json(job))
    except Exception as e:
        logging.error(f"[QM_SCAN_STATUS {jid}] Exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/api/qm/scan/progress")
def api_qm_scan_progress():
    try:
        from modules.qm_screener import get_qm_scan_progress
        return jsonify(get_qm_scan_progress())
    except Exception as exc:
        return jsonify({"stage": "Error", "pct": 0, "msg": str(exc)})


@bp.route("/api/qm/scan/logs/<jid>")
def api_qm_scan_logs(jid):
    if jid not in _scan_log_paths:
        return jsonify({"ok": False, "error": "Job ID not found or logs not available"}), 404
    log_file = _scan_log_paths[jid]
    if not log_file.exists():
        return jsonify({"ok": False, "error": "Log file not found"}), 404
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        stage1_logs = [
            line for line in content.split("\n")
            if any(m in line for m in [
                "[QM Stage1]", "[Finviz", "finvizfinance", "screener_view",
                "Performance", "Overview", "fallback",
            ])
        ]
        return jsonify({
            "ok": True, "job_id": jid,
            "log_file": str(log_file.relative_to(ROOT)),
            "full_logs": content.split("\n")[-100:],
            "stage1_diagnostics": stage1_logs[-50:],
            "total_lines": len(content.split("\n")),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to read logs: {exc}"}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Combined Scan  (SEPA + QM)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/combined/scan/run", methods=["POST"])
def api_combined_scan_run():
    data = request.get_json(silent=True) or {}
    market = _normalize_market(data.get("market") or _load_market_mode())
    refresh_rs = data.get("refresh_rs", False)
    stage1_source = data.get("stage1_source") or None
    min_star = float(data["min_star"]) if "min_star" in data else None
    top_n = int(data["top_n"]) if "top_n" in data else None
    strict_rs = bool(data.get("strict_rs", False))
    jid = _new_job()
    cancel_ev = _get_cancel(jid)

    combined_log_file = _LOG_DIR / f"combined_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    combined_log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handler = logging.FileHandler(combined_log_file, encoding="utf-8")
    log_handler.setLevel(logging.DEBUG)
    log_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _COMBINED_LOGGERS = [
        "modules.combined_scanner", "modules.screener", "modules.qm_screener",
        "modules.data_pipeline", "modules.rs_ranking", "modules.market_env",
        "modules.qm_analyzer",
    ]
    for ln in _COMBINED_LOGGERS:
        lgr = logging.getLogger(ln)
        lgr.addHandler(log_handler)
        lgr.setLevel(logging.DEBUG)
    _scan_file_handlers[jid] = log_handler
    _scan_log_paths[jid] = combined_log_file

    def _run():
        try:
            from modules.combined_scanner import run_combined_scan, set_combined_cancel
            set_combined_cancel(cancel_ev)
            try:
                sepa_result, qm_result = run_combined_scan(
                    refresh_rs=refresh_rs, stage1_source=stage1_source,
                    verbose=False, min_star=min_star, top_n=top_n, strict_rs=strict_rs,
                )
            except Exception as run_err:
                error_msg = (
                    str(run_err) if "DataFrame" not in str(type(run_err))
                    else f"{type(run_err).__name__}: DataFrame-related error during analysis"
                )
                raise RuntimeError(error_msg) from run_err

            sepa_rows = df_to_rows(sepa_result.get("passed"), "combined-SEPA")
            sepa_all_rows = df_to_rows(sepa_result.get("all"), "combined-SEPA-all")
            qm_rows = df_to_rows(qm_result.get("passed"), "combined-QM")
            qm_all_source = qm_result.get("all_scored")
            if qm_all_source is None or (hasattr(qm_all_source, "empty") and qm_all_source.empty):
                qm_all_source = qm_result.get("all")
            qm_all_rows = df_to_rows(qm_all_source, "combined-QM-all")
            market_env = sepa_result.get("market_env", {})
            timing = sepa_result.get("timing", {})
            qm_was_blocked = qm_result.get("blocked", False)

            from datetime import datetime as _dt_now
            _scan_ts = _dt_now.now()
            sepa_csv_path, qm_csv_path = _save_combined_scan_csv(
                sepa_result.get("passed"), qm_result.get("passed"), scan_ts=_scan_ts,
            )
            _save_combined_last(sepa_rows, qm_rows, market_env, timing,
                                sepa_csv_path, qm_csv_path, market=market)
            _save_last_scan(sepa_rows, all_rows=sepa_all_rows, market=market)
            if not qm_was_blocked:
                _save_qm_last_scan(qm_rows, all_rows=qm_all_rows, market=market)

            result = {
                "sepa": {"passed": sepa_rows, "count": len(sepa_rows)},
                "qm": {"passed": qm_rows, "count": len(qm_rows), "blocked": qm_was_blocked},
                "market": market_env, "timing": timing,
                "sepa_csv": sepa_csv_path, "qm_csv": qm_csv_path,
                "active_market": market,
            }
            log_rel = str(combined_log_file.relative_to(ROOT)) if combined_log_file.exists() else ""
            _finish_job(jid, result=result, log_file=log_rel)
        except Exception as exc:
            logging.exception("[CRITICAL] Combined scan thread error")
            log_rel = str(combined_log_file.relative_to(ROOT)) if combined_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for ln in _COMBINED_LOGGERS:
                    lgr = logging.getLogger(ln)
                    if handler in lgr.handlers:
                        lgr.removeHandler(handler)
                handler.flush()
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/combined/scan/status/<jid>", methods=["GET"])
def api_combined_scan_status(jid):
    from modules.combined_scanner import get_combined_progress
    try:
        job = _get_job(jid)
        if not job:
            return jsonify({"status": "not_found"}), 404
        status = job["status"]
        if status == "pending":
            return jsonify({"status": "pending", "progress": get_combined_progress()})
        elif status == "done":
            try:
                return jsonify({"status": "done", "result": job.get("result")})
            except TypeError:
                return jsonify({"status": "done", "result": _sanitize_for_json(job.get("result"))})
        else:
            return jsonify({"status": "error", "error": job.get("error")})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/api/combined/scan/cancel/<jid>", methods=["POST"])
def api_combined_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling...", "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@bp.route("/api/combined/scan/last", methods=["GET"])
def api_combined_scan_last():
    market = _normalize_market(request.args.get("market") or _load_market_mode())
    return jsonify(_load_combined_last(market=market))


# ═══════════════════════════════════════════════════════════════════════════════
# ML Scan
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ml/scan/run", methods=["POST"])
def api_ml_scan_run():
    data = request.get_json(silent=True) or {}
    market = _normalize_market(data.get("market") or _load_market_mode())
    min_star = float(data.get("min_star", getattr(C, "ML_SCAN_MIN_STAR", 3.0)))
    top_n = int(data.get("top_n", getattr(C, "ML_SCAN_TOP_N", 50)))
    stage1_source = data.get("stage1_source") or None
    use_universe_cache = bool(data.get("use_universe_cache", False))
    jid = _new_job()
    cancel_ev = _get_cancel(jid)
    _ml_job_ids.add(jid)

    scan_log_file = _LOG_DIR / f"ml_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _ML_SCAN_LOGGERS = ["modules.ml_screener", "modules.ml_analyzer", "modules.data_pipeline"]
    for ln in _ML_SCAN_LOGGERS:
        logging.getLogger(ln).addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.ml_screener import run_ml_scan, set_ml_scan_cancel
            set_ml_scan_cancel(cancel_ev)
            result = run_ml_scan(
                min_star=min_star, top_n=top_n,
                stage1_source=stage1_source,
                use_universe_cache=use_universe_cache,
            )

            if isinstance(result, tuple) and len(result) == 2:
                df_passed, df_all = result
            else:
                logging.error(f"[ML Scan] Unexpected result type: {type(result).__name__}")
                _finish_job(jid, error=f"Internal scan error: invalid result type {type(result).__name__}")
                return

            rows = df_to_rows(df_passed, "ML")
            all_rows = df_to_rows(df_all, "ML-all")

            # Validate rows
            if not isinstance(rows, list):
                rows = []
            else:
                rows = [r for r in rows if isinstance(r, dict)]

            # Theme report
            themes = []
            triple_summary = {}
            try:
                from modules.ml_theme_tracker import get_theme_report
                theme_rpt = get_theme_report(rows if isinstance(rows, list) else [])
                if isinstance(theme_rpt, dict):
                    themes = _clean(theme_rpt.get("themes", []))
            except Exception as _te:
                logging.warning("ML theme report failed: %s", _te)

            # Triple channel summary
            try:
                channel_counts = {"GAP": 0, "GAINER": 0, "LEADER": 0}
                for row in (rows if isinstance(rows, list) else []):
                    if not isinstance(row, dict):
                        continue
                    ch = str(row.get("channel", "")).upper()
                    if ch in channel_counts:
                        channel_counts[ch] += 1
                triple_summary = channel_counts
            except Exception:
                triple_summary = {"GAP": 0, "GAINER": 0, "LEADER": 0}

            _save_ml_last_scan(rows, all_rows=all_rows, triple_summary=triple_summary, market=market)
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
            try:
                with _jobs_lock:
                    _jobs[jid]["themes"] = _sanitize_for_json(themes)
                    _jobs[jid]["triple_summary"] = _sanitize_for_json(triple_summary)
            except Exception:
                with _jobs_lock:
                    _jobs[jid]["themes"] = []
                    _jobs[jid]["triple_summary"] = {"GAP": 0, "GAINER": 0, "LEADER": 0}
        except Exception as exc:
            logging.exception("ML scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            _ml_job_ids.discard(jid)
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for ln in _ML_SCAN_LOGGERS:
                    logging.getLogger(ln).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/ml/universe-cache", methods=["GET"])
def api_ml_universe_cache():
    try:
        from modules.ml_screener import get_ml_universe_cache_info
        return jsonify(get_ml_universe_cache_info())
    except Exception as exc:
        return jsonify({"exists": False, "error": str(exc)}), 500


@bp.route("/api/ml/scan/cancel/<jid>", methods=["POST"])
def api_ml_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…", "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@bp.route("/api/ml/scan/last", methods=["GET"])
def api_ml_scan_last():
    market = _normalize_market(request.args.get("market") or _load_market_mode())
    data = _load_ml_last_scan(market=market)
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@bp.route("/api/ml/scan/status/<jid>")
def api_ml_scan_status(jid):
    try:
        job = _get_job(jid)
        return jsonify(_sanitize_for_json(job))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/api/ml/scan/progress")
def api_ml_scan_progress():
    try:
        from modules.ml_screener import get_ml_scan_progress
        return jsonify(get_ml_scan_progress())
    except Exception as exc:
        return jsonify({"stage": "Error", "pct": 0, "msg": str(exc)})


@bp.route("/api/ml/scan/logs/<jid>")
def api_ml_scan_logs(jid):
    if jid not in _scan_log_paths:
        return jsonify({"ok": False, "error": "Job ID not found"}), 404
    log_file = _scan_log_paths[jid]
    if not log_file.exists():
        return jsonify({"ok": False, "error": "Log file not found"}), 404
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({
            "ok": True, "job_id": jid,
            "log_file": str(log_file.relative_to(ROOT)),
            "full_logs": content.split("\n")[-100:],
            "total_lines": len(content.split("\n")),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to read logs: {exc}"}), 500
