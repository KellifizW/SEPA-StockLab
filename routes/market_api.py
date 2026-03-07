"""Market, VCP, Report, RS, DB history, and YF status API routes."""

import logging
import threading
from pathlib import Path
from datetime import datetime
import pandas as pd
from flask import Blueprint, request, jsonify

import trader_config as C
from routes.helpers import (
    ROOT, _LOG_DIR,
    _new_job, _finish_job, _get_job,
    _clean, _sanitize_for_json,
    _get_cached, _set_cache,
    _load_watchlist, _load_positions, _latest_report,
    _market_job_ids,
    _save_market_last, _load_market_last,
)

bp = Blueprint("market_api", __name__)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# VCP
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/vcp", methods=["POST"])
def api_vcp():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    jid = _new_job()

    def _run():
        try:
            from modules.data_pipeline import get_enriched
            from modules.vcp_detector import detect_vcp
            df = get_enriched(ticker, period="2y")
            if df is None or df.empty:
                _finish_job(jid, error=f"No price data for {ticker}")
                return
            result = detect_vcp(df)
            last_close = float(df["Close"].iloc[-1]) if not df.empty else None
            result["current_price"] = round(last_close, 2) if last_close else None
            result["ticker"] = ticker
            _finish_job(jid, result=_clean(result))
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/vcp/status/<jid>")
def api_vcp_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# Market Environment
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/market/run", methods=["POST"])
def api_market_run():
    jid = _new_job()
    _market_job_ids.add(jid)

    market_log_file = _LOG_DIR / f"market_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    market_handler = logging.FileHandler(market_log_file, encoding="utf-8")
    market_handler.setLevel(logging.DEBUG)
    market_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _MARKET_LOGGERS = ["modules.market_env", "modules.data_pipeline"]
    for ln in _MARKET_LOGGERS:
        logging.getLogger(ln).addHandler(market_handler)

    def _run():
        try:
            from modules.market_env import assess
            logging.getLogger("modules.market_env").info(
                "=== Market Assessment started (job %s) ===", jid)
            result = assess(verbose=False)
            logging.getLogger("modules.market_env").info(
                "=== Market Assessment finished — regime: %s ===",
                result.get("regime", "UNKNOWN"))

            if getattr(C, "DB_ENABLED", True) and result:
                try:
                    from modules.db import append_market_env
                    append_market_env(result)
                except Exception as exc:
                    logging.warning("DB market_env write skipped: %s", exc)

            if result:
                _save_market_last(_clean(result))

            log_rel = str(market_log_file.relative_to(ROOT)) if market_log_file.exists() else ""
            _finish_job(jid, result=_clean(result), log_file=log_rel)
        except Exception as exc:
            logging.getLogger("modules.market_env").exception("Market assessment thread error")
            log_rel = str(market_log_file.relative_to(ROOT)) if market_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            for ln in _MARKET_LOGGERS:
                logging.getLogger(ln).removeHandler(market_handler)
            market_handler.close()
            _market_job_ids.discard(jid)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/market/status/<jid>")
def api_market_status(jid):
    return jsonify(_get_job(jid))


@bp.route("/api/market/last", methods=["GET"])
def api_market_last():
    """Return the latest cached market assessment snapshot."""
    cached = _load_market_last()
    d = cached.get("result") if isinstance(cached, dict) else None
    if not isinstance(d, dict) or not d:
        return jsonify({"ok": False, "error": "No cached market assessment"}), 404

    return jsonify({
        "ok": True,
        "saved_at": cached.get("saved_at"),
        "result": d,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/report/generate", methods=["POST"])
def api_report_generate():
    jid = _new_job()

    def _run():
        try:
            from modules.report import generate_html_report, generate_csv
            wl = _load_watchlist()
            pos = _load_positions()
            path = generate_html_report(watchlist=wl, positions=pos)
            generate_csv(None)
            _finish_job(jid, result={"path": str(path) if path else None})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/report/status/<jid>", methods=["GET"])
def api_report_status(jid):
    try:
        job = _get_job(jid)
        return jsonify(_sanitize_for_json(job))
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# RS Ranking
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/rs/top", methods=["GET"])
def api_rs_top():
    min_rs = float(request.args.get("min", C.TT9_MIN_RS_RANK))
    try:
        from modules.rs_ranking import get_rs_top
        df = get_rs_top(min_rs)
        if df is not None and not df.empty:
            rows = df.fillna("").to_dict(orient="records")
        else:
            rows = []
        return jsonify({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# YF status
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/yf-status", methods=["GET"])
def api_yf_status():
    try:
        from modules.data_pipeline import get_yf_status
        return jsonify({"ok": True, **get_yf_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "calls": 0,
                        "errors": 0, "rate_limited": False})


# ═══════════════════════════════════════════════════════════════════════════════
# DB History API
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/db/stats", methods=["GET"])
def api_db_stats():
    try:
        from modules.db import db_stats
        return jsonify({"ok": True, "stats": db_stats()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/scan-trend/<ticker>", methods=["GET"])
def api_db_scan_trend(ticker: str):
    days = int(request.args.get("days", 90))
    try:
        from modules.db import query_scan_trend
        df = query_scan_trend(ticker.upper(), days)
        return jsonify({"ok": True, "ticker": ticker.upper(),
                        "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/persistent-signals", methods=["GET"])
def api_db_persistent_signals():
    days = int(request.args.get("days", 30))
    min_app = int(request.args.get("min", 5))
    try:
        from modules.db import query_persistent_signals
        df = query_persistent_signals(min_app, days)
        return jsonify({"ok": True, "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/rs-trend/<ticker>", methods=["GET"])
def api_db_rs_trend(ticker: str):
    days = int(request.args.get("days", 90))
    try:
        from modules.db import query_rs_trend
        df = query_rs_trend(ticker.upper(), days)
        return jsonify({"ok": True, "ticker": ticker.upper(),
                        "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/market-history", methods=["GET"])
def api_db_market_history():
    days = int(request.args.get("days", 60))
    try:
        from modules.db import query_market_env_history
        df = query_market_env_history(days)
        return jsonify({"ok": True, "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/watchlist-history", methods=["GET"])
def api_db_watchlist_history():
    try:
        cached = _get_cached("watchlist-history")
        if cached:
            return jsonify({"ok": True, "rows": cached, "cached": True})

        from modules.db import query_persistent_signals
        df = query_persistent_signals(min_appearances=3, days=30)
        rows = df.to_dict(orient="records")
        _set_cache("watchlist-history", rows)
        return jsonify({"ok": True, "rows": rows, "cached": False})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@bp.route("/api/db/price-history/<ticker>", methods=["GET"])
def api_db_price_history(ticker: str):
    days = int(request.args.get("days", 90))
    ticker_upper = ticker.upper()

    try:
        try:
            from modules.db import query_price_history
            df = query_price_history(ticker_upper, days)
            if not df.empty:
                rows = df.to_dict(orient="records")
                for r in rows:
                    if hasattr(r.get("date"), "isoformat"):
                        r["date"] = r["date"].isoformat()
                return jsonify({"ok": True, "ticker": ticker_upper,
                                "rows": rows, "source": "duckdb"})
        except Exception:
            pass

        from modules.data_pipeline import get_historical
        df_pd = get_historical(ticker_upper, use_cache=True)
        if df_pd.empty:
            df_pd = get_historical(ticker_upper, use_cache=False)

        if not df_pd.empty:
            cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
            df_pd = df_pd[df_pd.index >= cutoff].copy()
            if hasattr(df_pd.index, "date"):
                df_pd.index = df_pd.index.date
            elif hasattr(df_pd.index, "normalize"):
                df_pd.index = df_pd.index.normalize()
            rows = [
                {"date": str(idx), "open": round(float(row["Open"]), 4),
                 "high": round(float(row["High"]), 4),
                 "low": round(float(row["Low"]), 4),
                 "close": round(float(row["Close"]), 4),
                 "volume": int(row["Volume"])}
                for idx, row in df_pd.iterrows()
            ]
            return jsonify({"ok": True, "ticker": ticker_upper,
                            "rows": rows, "source": "pandas"})

        return jsonify({"ok": True, "ticker": ticker_upper,
                        "rows": [], "source": "none"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
