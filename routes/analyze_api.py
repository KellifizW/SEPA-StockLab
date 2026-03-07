"""Analyze API routes — SEPA, QM, and ML single-stock analysis + HTMX partials."""

import logging
import threading
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template, make_response

import trader_config as C
from routes.helpers import (
    ROOT, _LOG_DIR,
    _new_job, _finish_job, _get_job,
    _clean, _get_account_size,
    _qm_analyze_cache, _ml_analyze_cache,
    _load_combined_last, _load_market_last,
)

bp = Blueprint("analyze_api", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SEPA Analyze
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    acct, _, _ = _get_account_size()
    if "account_size" in data and data["account_size"]:
        try:
            override = float(data["account_size"])
            if override > 0:
                acct = override
        except (ValueError, TypeError):
            pass
    jid = _new_job()

    analyze_log_file = _LOG_DIR / f"analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    _ANALYZE_LOGGERS = [
        "modules.stock_analyzer", "modules.data_pipeline",
        "modules.screener", "modules.vcp_detector",
        "modules.rs_ranking", "modules.db",
    ]
    for ln in _ANALYZE_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)
        _lg.addHandler(analyze_handler)

    def _run():
        try:
            from modules.stock_analyzer import analyze
            r = analyze(ticker, account_size=acct, print_report=False)
            if r is None:
                _finish_job(jid, error=f"No data returned for {ticker}")
                return
            _write_analyze_report(analyze_log_file, r, acct)
            _finish_job(jid, result=_clean(r))
        except Exception as exc:
            logging.exception("Analyze thread error for %s", ticker)
            _finish_job(jid, error=str(exc))
        finally:
            for ln in _ANALYZE_LOGGERS:
                logging.getLogger(ln).removeHandler(analyze_handler)
            analyze_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid, "log_file": analyze_log_file.name})


@bp.route("/api/analyze/status/<jid>")
def api_analyze_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# QM Analyze
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/qm/analyze", methods=["POST"])
def api_qm_analyze():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    analyze_log_file = _LOG_DIR / f"qm_analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    _QM_ANALYZE_LOGGERS = [
        "modules.qm_analyzer", "modules.qm_setup_detector",
        "modules.data_pipeline", "modules.market_env",
    ]
    for ln in _QM_ANALYZE_LOGGERS:
        logging.getLogger(ln).addHandler(analyze_handler)

    try:
        from modules.qm_analyzer import analyze_qm
        result = analyze_qm(ticker, print_report=False)
        clean_result = _clean(result) if result else {}
        _qm_analyze_cache[ticker] = clean_result
        log_rel = str(analyze_log_file.relative_to(ROOT)) if analyze_log_file.exists() else ""
        return jsonify({"ok": True, "ticker": ticker, "result": clean_result, "log_file": log_rel})
    except Exception as exc:
        logging.exception("QM analyze error: %s", ticker)
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker})
    finally:
        for ln in _QM_ANALYZE_LOGGERS:
            logging.getLogger(ln).removeHandler(analyze_handler)
        analyze_handler.close()


# ═══════════════════════════════════════════════════════════════════════════════
# ML Analyze
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ml/analyze", methods=["POST"])
def api_ml_analyze():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    analyze_log_file = _LOG_DIR / f"ml_analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s"))
    _ML_ANALYZE_LOGGERS = [
        "modules.ml_analyzer", "modules.ml_setup_detector",
        "modules.data_pipeline", "modules.market_env",
    ]
    for ln in _ML_ANALYZE_LOGGERS:
        logging.getLogger(ln).addHandler(analyze_handler)

    try:
        from modules.ml_analyzer import analyze_ml
        result = analyze_ml(ticker, print_report=False)
        clean_result = _clean(result) if result else {}
        _ml_analyze_cache[ticker] = clean_result
        log_rel = str(analyze_log_file.relative_to(ROOT)) if analyze_log_file.exists() else ""
        return jsonify({"ok": True, "ticker": ticker, "result": clean_result, "log_file": log_rel})
    except Exception as exc:
        logging.exception("ML analyze error: %s", ticker)
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker})
    finally:
        for ln in _ML_ANALYZE_LOGGERS:
            logging.getLogger(ln).removeHandler(analyze_handler)
        analyze_handler.close()


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX result partials
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/htmx/analyze/result/<jid>")
def htmx_analyze_result(jid):
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200,
        )
    d = job.get("result") or {}
    ticker = str(d.get("ticker", "")).upper()
    return render_template("_analyze_result.html", d=d, ticker=ticker)


@bp.route("/htmx/vcp/result/<jid>")
def htmx_vcp_result(jid):
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200,
        )
    d = job.get("result") or {}
    ticker = str(d.get("ticker") or request.args.get("ticker", "")).upper()
    return render_template("_vcp_result.html", d=d, ticker=ticker)


@bp.route("/htmx/market/result/<jid>")
def htmx_market_result(jid):
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200,
        )
    d = job.get("result") or {}
    log_file = job.get("log_file", "")
    return render_template("_market_result.html", d=d, log_file=log_file)

@bp.route("/htmx/market/result/last")
def htmx_market_result_last():
    """Render latest cached market assessment if available."""
    cached = _load_market_last()
    d = cached.get("result") if isinstance(cached, dict) else None
    if not isinstance(d, dict) or not d:
        return make_response(
            "<p class='text-muted py-3'><i class='bi bi-clock-history me-2'></i>No cached market assessment yet.</p>",
            200,
        )

    d = dict(d)
    d["cached_saved_at"] = cached.get("saved_at", "")
    return render_template("_market_result.html", d=d, log_file="")


@bp.route("/htmx/qm/analyze/result")
def htmx_qm_analyze_result():
    ticker = request.args.get("ticker", "").upper().strip()
    d = _qm_analyze_cache.get(ticker)
    if not d:
        return make_response(
            f"<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            f"Result for {ticker} not found in cache. Please re-run analysis.</p>", 200,
        )
    return render_template("_qm_analyze_result.html", d=d, ticker=ticker)


@bp.route("/htmx/ml/analyze/result")
def htmx_ml_analyze_result():
    ticker = request.args.get("ticker", "").upper().strip()
    d = _ml_analyze_cache.get(ticker)
    if not d:
        return make_response(
            f"<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            f"Result for {ticker} not found in cache. Please re-run analysis.</p>", 200,
        )
    return render_template("_ml_analyze_result.html", d=d, ticker=ticker)


@bp.route("/htmx/dashboard/highlights")
def htmx_dashboard_highlights():
    r = _load_combined_last()
    return render_template("_dashboard_highlights.html", r=r)


# ═══════════════════════════════════════════════════════════════════════════════
# _write_analyze_report  (SEPA full analysis log)
# ═══════════════════════════════════════════════════════════════════════════════

def _write_analyze_report(log_file: Path, r: dict, acct: float):
    """Append a human-readable full analysis report to the log file."""
    try:
        sep = "=" * 72
        lines: list = []
        def ln(s=""):
            lines.append(s)

        ticker = r.get("ticker", "?")
        company = r.get("company", ticker)
        rec = r.get("recommendation") or {}
        tt = r.get("trend_template") or {}
        scored = r.get("scored_pillars") or {}
        pos = r.get("position") or {}
        vcp = r.get("vcp") or {}
        funds = r.get("fundamentals") or {}
        fund_checks = funds.get("checks") or []
        eps_accel = r.get("eps_acceleration") or {}

        ln(sep)
        ln(f"  SEPA ANALYSIS REPORT — {ticker} ({company})")
        ln(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Account: ${acct:,.0f}")
        ln(sep)
        ln()
        ln("[ SUMMARY ]")
        ln(f"  Sector:         {r.get('sector','')} / {r.get('industry','')}")
        ln(f"  Price:          ${r.get('price', 0):.2f}")
        mktcap = r.get("market_cap") or 0
        ln(f"  Market Cap:     ${mktcap/1e9:.2f}B" if mktcap >= 1e9 else f"  Market Cap:     ${mktcap/1e6:.0f}M")
        ln(f"  RS Rank:        {r.get('rs_rank', 0):.1f} percentile")
        ln(f"  SEPA Score:     {float(r.get('sepa_score') or 0):.1f} / 100")
        ln(f"  Recommendation: {rec.get('action','N/A')}  —  {rec.get('description','')}")
        for reason in rec.get("reasons") or []:
            if reason:
                ln(f"    • {reason}")

        ln()
        ln("[ SEPA 5-PILLAR SCORES ]")
        pillar_map = {
            "trend_score": "趨勢 Trend",
            "fundamental_score": "基本面 Fundamentals",
            "catalyst_score": "催化劑 Catalyst",
            "entry_score": "入場時機 Entry",
            "rr_score": "風險回報 R/R",
        }
        for key, label in pillar_map.items():
            val = float(scored.get(key) or 0)
            bar = "█" * int(val / 5) + "░" * (20 - int(val / 5))
            ln(f"  {label:<28} {val:5.1f}  [{bar}]")
        ln(f"  {'Total':<28} {float(r.get('sepa_score') or 0):5.1f}")

        ln()
        ln("[ TREND TEMPLATE (TT1-TT10) ]")
        tt_labels = {
            "tt1": "Price>SMA50>SMA150>SMA200", "tt2": "Price>SMA200",
            "tt3": "SMA150>SMA200", "tt4": "SMA200 rising 22d",
            "tt5": "SMA50>SMA150 & SMA200", "tt6": "Price>SMA50",
            "tt7": "Price >25% above 52W low", "tt8": "Within 25% of 52W high",
            "tt9": "RS Rank ≥70", "tt10": "Sector top 35%",
        }
        passed = sum(1 for k in tt_labels if tt.get(k))
        ln(f"  Passed: {passed}/10")
        for k, lbl in tt_labels.items():
            mark = "PASS" if tt.get(k) else "FAIL"
            ln(f"  [{mark}] {k.upper()}: {lbl}")

        ln()
        ln("[ VCP DETECTION ]")
        ln(f"  Valid VCP:       {'YES ✓' if vcp.get('is_valid_vcp') else 'NO'}")
        ln(f"  VCP Score:       {vcp.get('vcp_score', 0)}/100  Grade: {vcp.get('grade','—')}")
        ln(f"  T-count:         {vcp.get('t_count', 0)}")
        ln(f"  Base weeks:      {vcp.get('base_weeks', 0)}")
        base_d = vcp.get("base_depth_pct")
        ln(f"  Base depth:      {float(base_d):.1f}%" if base_d is not None else "  Base depth:      N/A")
        if vcp.get("pivot_price"):
            ln(f"  Pivot price:     ${float(vcp['pivot_price']):.2f}")
        for i, c_item in enumerate(vcp.get("contractions") or []):
            ln(f"  T-{i+1}: range {c_item.get('range_pct',0):.1f}%  "
               f"vol_ratio {c_item.get('vol_ratio',0):.2f}")

        ln()
        ln("[ FUNDAMENTALS CHECKLIST ]")
        ln(f"  Score: {funds.get('passes',0)}/{funds.get('total',0)} ({funds.get('pct',0)}%)")
        for fc in fund_checks:
            mark = "PASS" if fc.get("pass") else "FAIL"
            ln(f"  [{mark}] {fc.get('id',''):<25} {fc.get('note','')}")

        ln()
        ln("[ POSITION SIZING ]")
        ln(f"  Entry:          ${float(pos.get('entry') or 0):.2f}")
        ln(f"  Stop Loss:      ${float(pos.get('stop') or 0):.2f}  (-{float(pos.get('risk_pct') or 0):.1f}%)")
        ln(f"  Target:         ${float(pos.get('target') or 0):.2f}")
        ln(f"  Shares:         {int(pos.get('shares') or 0)}")
        ln(f"  Position Value: ${float(pos.get('position_value') or 0):,.0f}")
        ln()
        ln(sep)
        ln()

        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as exc:
        logging.warning("_write_analyze_report failed: %s", exc, exc_info=True)
