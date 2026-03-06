"""Portfolio API routes — Watchlist CRUD, Positions CRUD, HTMX partials."""

import json
import logging
import threading
from flask import Blueprint, request, jsonify, render_template, make_response

import trader_config as C
from routes.helpers import (
    _new_job, _finish_job, _get_job,
    _clean,
    _load_watchlist, _load_positions,
    htmx_wl_rows,
)

bp = Blueprint("portfolio_api", __name__)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Watchlist JSON API
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    return jsonify(_load_watchlist())


@bp.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    strategy = data.get("strategy") or data.get("grade")
    note = data.get("note", "")
    jid = _new_job()

    def _run():
        try:
            from modules.watchlist import add
            add(ticker, grade=strategy, note=note)
            _finish_job(jid, result={"ticker": ticker, "watchlist": _load_watchlist()})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/watchlist/add/status/<jid>")
def api_watchlist_add_status(jid):
    return jsonify(_get_job(jid))


@bp.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import remove
        remove(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/watchlist/promote", methods=["POST"])
def api_watchlist_promote():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import promote
        promote(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/watchlist/demote", methods=["POST"])
def api_watchlist_demote():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import demote
        demote(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/watchlist/move", methods=["POST"])
def api_watchlist_move():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    strategy = str(data.get("strategy", "")).upper().strip()
    try:
        from modules.watchlist import move_to_strategy
        move_to_strategy(ticker, strategy)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/watchlist/refresh", methods=["POST"])
def api_watchlist_refresh():
    jid = _new_job()

    def _run():
        try:
            from modules.watchlist import refresh
            refresh()
            _finish_job(jid, result={"watchlist": _load_watchlist()})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/watchlist/refresh/status/<jid>")
def api_watchlist_refresh_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX – Watchlist (return HTML fragments)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/htmx/watchlist/body")
def htmx_wl_body():
    return htmx_wl_rows(_load_watchlist())


@bp.route("/htmx/watchlist/promote", methods=["POST"])
def htmx_wl_promote():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import promote
        promote(ticker)
        return htmx_wl_rows(_load_watchlist(), f"✅ {ticker} promoted")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@bp.route("/htmx/watchlist/demote", methods=["POST"])
def htmx_wl_demote():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import demote
        demote(ticker)
        return htmx_wl_rows(_load_watchlist(), f"✅ {ticker} demoted")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@bp.route("/htmx/watchlist/remove", methods=["POST"])
def htmx_wl_remove():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import remove
        remove(ticker)
        return htmx_wl_rows(_load_watchlist(), f"✅ {ticker} removed from watchlist")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@bp.route("/htmx/watchlist/move", methods=["POST"])
def htmx_wl_move():
    ticker = (request.form.get("ticker") or "").upper().strip()
    strategy = (request.form.get("strategy") or "").upper().strip()
    try:
        from modules.watchlist import move_to_strategy
        move_to_strategy(ticker, strategy)
        return htmx_wl_rows(_load_watchlist(), f"✅ {ticker} moved to {strategy}")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


# ═══════════════════════════════════════════════════════════════════════════════
# Positions JSON API
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/positions", methods=["GET"])
def api_positions_get():
    return jsonify(_load_positions())


@bp.route("/api/positions/add", methods=["POST"])
def api_positions_add():
    import time
    start_t = time.time()

    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()

    logging.info("[API] positions/add START: %s", ticker)

    try:
        from modules.position_monitor import add_position

        t1 = time.time()
        add_position(
            ticker,
            float(data["buy_price"]),
            int(data["shares"]),
            float(data["stop_loss"]),
            float(data["target"]) if data.get("target") else None,
            str(data.get("note", "")),
            str(data.get("pool") or data.get("strategy") or "FREE"),
        )
        t2 = time.time()
        logging.info("[API] add_position() completed in %.3fs", t2 - t1)

        buy_price = float(data["buy_price"])
        shares = int(data["shares"])
        stop_loss = float(data["stop_loss"])
        target = float(data["target"]) if data.get("target") else None

        if target is None:
            risk = buy_price - stop_loss
            target = buy_price + risk * 2

        stop_pct = (buy_price - stop_loss) / buy_price * 100
        rr = (target - buy_price) / (buy_price - stop_loss) if (buy_price - stop_loss) > 0 else 0
        risk_dol = shares * (buy_price - stop_loss)

        positions_dict = {
            ticker: {
                "buy_price": round(buy_price, 2),
                "shares": shares,
                "stop_loss": round(stop_loss, 2),
                "stop_pct": round(stop_pct, 2),
                "target": round(target, 2),
                "rr": round(rr, 2),
                "risk_dollar": round(risk_dol, 2),
                "strategy": str(data.get("pool") or data.get("strategy") or "FREE").upper(),
                "buy_date": None,
                "days_held": 0,
                "note": str(data.get("note", "")),
            }
        }

        elapsed = time.time() - start_t
        logging.info("[API] positions/add DONE in %.3fs: %s", elapsed, ticker)

        return jsonify({"ok": True, "positions": positions_dict})
    except Exception as exc:
        elapsed = time.time() - start_t
        logging.error("[API] positions/add FAILED (%.3fs): %s", elapsed, exc)
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/positions/close", methods=["POST"])
def api_positions_close():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    exit_price = float(data.get("exit_price", 0) or 0)
    reason = str(data.get("reason", ""))
    shares_to_close_raw = data.get("shares_to_close")
    ibkr_execute = bool(data.get("ibkr_execute", False))

    if not ticker:
        return jsonify({"ok": False, "error": "Missing ticker"}), 400
    if exit_price <= 0:
        return jsonify({"ok": False, "error": "exit_price must be > 0"}), 400

    positions = _load_positions()
    pos = positions.get(ticker)
    if not pos:
        return jsonify({"ok": False, "error": f"{ticker} not found in open positions"}), 404

    current_shares = int(pos.get("shares") or pos.get("qty") or 0)
    if current_shares <= 0:
        return jsonify({"ok": False, "error": f"{ticker} has no shares to close"}), 400

    shares_to_close = None
    if shares_to_close_raw not in (None, ""):
        try:
            shares_to_close = int(shares_to_close_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "shares_to_close must be an integer"}), 400
        if shares_to_close <= 0:
            return jsonify({"ok": False, "error": "shares_to_close must be > 0"}), 400
        if shares_to_close > current_shares:
            return jsonify({"ok": False, "error": "shares_to_close exceeds current shares"}), 400

    ibkr_result = None
    qty_to_sell = shares_to_close or current_shares

    try:
        if ibkr_execute:
            if not C.IBKR_ENABLED:
                return jsonify({"ok": False, "error": "IBKR integration is disabled"}), 503

            from modules import ibkr_client
            if not ibkr_client.is_ibkr_connected():
                return jsonify({"ok": False, "error": "IBKR is not connected"}), 503

            ibkr_result = ibkr_client.place_order(
                ticker=ticker,
                action="SELL",
                qty=qty_to_sell,
                order_type="MKT",
            )
            if not ibkr_result.get("success"):
                return jsonify({
                    "ok": False,
                    "error": ibkr_result.get("message", "IBKR sell order failed"),
                    "ibkr": ibkr_result,
                }), 502

        from modules.position_monitor import close_position
        close_position(
            ticker,
            exit_price,
            reason=reason,
            shares_to_close=shares_to_close,
        )
        return jsonify({
            "ok": True,
            "positions": _load_positions(),
            "ibkr": ibkr_result,
            "closed_shares": qty_to_sell,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/positions/check", methods=["POST"])
def api_positions_check():
    jid = _new_job()

    def _run():
        try:
            from modules.position_monitor import _check_position, _load
            data_store = _load()
            positions = data_store.get("positions", {})
            results = []
            for t, pos in positions.items():
                r = _check_position(t, pos)
                results.append(_clean(r))
            _finish_job(jid, result=results)
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@bp.route("/api/positions/check/status/<jid>")
def api_positions_check_status(jid):
    return jsonify(_get_job(jid))


@bp.route("/api/positions/update_stop", methods=["POST"])
def api_positions_update_stop():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    new_stop = float(data.get("new_stop", 0))
    try:
        from modules.position_monitor import update_stop
        update_stop(ticker, new_stop)
        return jsonify({"ok": True, "positions": _load_positions()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX – Positions (return HTML fragment)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/htmx/positions/add", methods=["POST"])
def htmx_positions_add():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        buy_price = float(request.form.get("buy_price") or 0)
        shares = int(request.form.get("shares") or 0)
        stop_loss = float(request.form.get("stop_loss") or 0)
        target_raw = request.form.get("target", "").strip()
        target = float(target_raw) if target_raw else None
        note = request.form.get("note", "")
        pool = (request.form.get("pool") or request.form.get("strategy") or "FREE").upper().strip()

        if not ticker or not buy_price or not shares or not stop_loss:
            resp = make_response("", 200)
            resp.headers["HX-Trigger"] = json.dumps({"showToast": "❌ Fill in Ticker, Entry, Shares and Stop Loss"})
            return resp

        from modules.position_monitor import add_position
        add_position(ticker, buy_price, shares, stop_loss, target, note, pool)

        if target is None:
            risk = buy_price - stop_loss
            target = round(buy_price + risk * 2, 2)
        rr = (target - buy_price) / (buy_price - stop_loss) if (buy_price - stop_loss) > 0 else 0
        risk_dol = shares * (buy_price - stop_loss)

        pos = {
            "buy_price": round(buy_price, 2),
            "shares": shares,
            "stop_loss": round(stop_loss, 2),
            "target": round(target, 2),
            "rr": round(rr, 2),
            "risk_dollar": round(risk_dol, 2),
            "strategy": pool,
            "days_held": 0,
            "note": note,
        }
        resp = make_response(render_template("_position_row.html", ticker=ticker, pos=pos))
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"✅ {ticker} 持倉已新增"})
        return resp

    except Exception as exc:
        logging.error("[htmx/positions/add] %s", exc)
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


# ═══════════════════════════════════════════════════════════════════════════════
# Quick-Add (Global, Multi-Strategy)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/quick-add-watch", methods=["POST"])
def api_quick_add_watch():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    grade = data.get("grade", "C")
    strategy = data.get("strategy", "")
    note = data.get("note", "")

    if not ticker:
        return jsonify({"ok": False, "error": "缺少 Ticker"}), 400

    try:
        from modules.watchlist import add
        from modules import db

        add(ticker, grade=grade, note=note)

        wl = _load_watchlist()
        if grade in wl and ticker in wl[grade]:
            wl[grade][ticker]["strategy"] = strategy
        db.wl_save(wl)

        wl = _load_watchlist()
        return jsonify({
            "ok": True,
            "message": f"✅ {ticker} 已加入觀察名單 (Grade {grade})",
            "watchlist": wl,
        })
    except Exception as exc:
        logger.error("[quick-add-watch] %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/quick-add-position", methods=["POST"])
def api_quick_add_position():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    buy_price = float(data.get("buy_price") or 0)
    shares = int(data.get("shares") or 0)
    stop_loss = float(data.get("stop_loss") or 0)
    target = data.get("target")
    target = float(target) if target else None
    strategy = data.get("strategy", "")
    note = data.get("note", "")

    if not ticker or not buy_price or not shares or not stop_loss:
        return jsonify({"ok": False, "error": "缺少必要資料"}), 400

    try:
        from modules.position_monitor import add_position
        from modules import db

        add_position(ticker, buy_price, shares, stop_loss, target, note)

        pos = _load_positions()
        if ticker in pos["positions"]:
            pos["positions"][ticker]["strategy"] = strategy
        db.pos_save(pos)

        pos = _load_positions()
        return jsonify({
            "ok": True,
            "message": f"✅ {ticker} 持倉已新增",
            "positions": pos,
        })
    except Exception as exc:
        logger.error("[quick-add-position] %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400
