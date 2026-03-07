"""Settings, Currency, Account size, Position calculator, Admin, and Telegram API routes."""

import json
import sys
import os
import subprocess
import logging
import threading
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify

import trader_config as C
from routes.helpers import (
    ROOT, _LOG_DIR,
    _get_account_size, _save_nav_cache, _load_nav_cache,
    _load_currency_setting, _save_currency_setting, _convert_amount,
    _load_market_mode, _save_market_mode, _normalize_market,
    _get_market_account_size, _get_market_split_pct,
    _tg_enabled, _tg_thread,
)

bp = Blueprint("settings_api", __name__)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/settings", methods=["GET"])
def api_get_settings():
    """Get current runtime settings (account size, etc.)."""
    try:
        settings_path = ROOT / C.SETTINGS_FILE
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = {"account_size": C.ACCOUNT_SIZE}
        return jsonify({"ok": True, **settings})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "account_size": C.ACCOUNT_SIZE})


@bp.route("/api/settings/account-size", methods=["PATCH"])
def api_update_account_size():
    """Update account size and persist to settings.json."""
    data = request.get_json(silent=True) or {}
    new_size = data.get("value")
    if new_size is None:
        return jsonify({"ok": False, "error": "缺少 value 參數"}), 400

    try:
        new_size = float(new_size)
        if new_size <= 0:
            return jsonify({"ok": False, "error": "帳戶大小必須 > 0"}), 400

        settings_path = ROOT / C.SETTINGS_FILE
        settings_path.parent.mkdir(exist_ok=True, parents=True)

        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = {}

        settings["account_size"] = new_size
        settings["last_updated"] = datetime.now().isoformat()

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

        C.ACCOUNT_SIZE = new_size

        return jsonify({"ok": True,
                        "message": f"✅ 帳戶大小已更新為 ${new_size:,.0f}",
                        "account_size": new_size})
    except ValueError as exc:
        return jsonify({"ok": False, "error": f"無效的數值: {exc}"}), 400
    except Exception as exc:
        logger.error("[update-account-size] %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Account Size
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/account/size", methods=["GET"])
def api_get_account_size():
    """Get current account size from IBKR or cache."""
    nav, last_sync, status = _get_account_size()
    market = _load_market_mode()
    market_nav = _get_market_account_size(nav, market)
    return jsonify({
        "ok": True,
        "nav": nav,
        "market": market,
        "market_nav": market_nav,
        "market_split_pct": _get_market_split_pct(market),
        "last_sync": last_sync,
        "sync_status": status,
    })


@bp.route("/api/market-mode", methods=["GET"])
def api_get_market_mode():
    """Get currently active market mode (US/HK)."""
    market = _load_market_mode()
    return jsonify({
        "ok": True,
        "market": market,
        "label": "美股 US" if market == "US" else "港股 HK",
        "split_pct": _get_market_split_pct(market),
    })


@bp.route("/api/market-mode", methods=["POST"])
def api_set_market_mode():
    """Switch active market mode (US/HK)."""
    data = request.get_json(silent=True) or {}
    requested = _normalize_market(data.get("market"))
    saved = _save_market_mode(requested)
    return jsonify({
        "ok": True,
        "market": saved,
        "label": "美股 US" if saved == "US" else "港股 HK",
        "split_pct": _get_market_split_pct(saved),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR NAV Sync (lives here because it's settings-adjacent)
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ibkr/sync-nav", methods=["POST"])
def api_sync_nav():
    """Sync account NAV from IBKR and cache it."""
    if not C.IBKR_ENABLED:
        return jsonify({"ok": False, "error": "IBKR integration is disabled"}), 403

    try:
        from modules import ibkr_client
        status = ibkr_client.get_status()
        if status.get("connected") and status.get("nav", 0) > 0:
            nav = float(status["nav"])
            buying_power = float(status.get("buying_power", 0))
            account = status.get("account", "")
            _save_nav_cache(nav, buying_power, account)
            return jsonify({
                "ok": True, "nav": nav, "buying_power": buying_power,
                "account": account,
                "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"✅ NAV synced: ${nav:,.2f}"})
        else:
            return jsonify({"ok": False,
                            "error": "Not connected to IBKR or no NAV data available"}), 503
    except Exception as exc:
        logger.error("api_sync_nav error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Currency
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/currency", methods=["GET"])
def api_get_currency():
    """Get current currency display setting and exchange rate."""
    currency, usd_hkd_rate = _load_currency_setting()
    nav, _, _ = _get_account_size()
    _, currency_symbol, display_str = _convert_amount(nav, currency)
    return jsonify({"ok": True, "currency": currency,
                    "currency_symbol": currency_symbol,
                    "usd_hkd_rate": usd_hkd_rate,
                    "nav_usd": nav, "nav_display": display_str})


@bp.route("/api/currency", methods=["POST"])
def api_set_currency():
    """Change currency display setting."""
    data = request.get_json(silent=True) or {}
    target_currency = data.get("currency", "USD").upper()
    new_rate = data.get("usd_hkd_rate")
    if target_currency not in ("USD", "HKD"):
        return jsonify({"ok": False, "error": "Currency must be USD or HKD"}), 400

    try:
        _save_currency_setting(target_currency, new_rate)
        nav, _, _ = _get_account_size()
        _, currency_symbol, display_str = _convert_amount(nav, target_currency)
        loaded_currency, loaded_rate = _load_currency_setting()
        return jsonify({
            "ok": True, "currency": loaded_currency,
            "currency_symbol": currency_symbol,
            "usd_hkd_rate": loaded_rate, "nav_display": display_str,
            "message": f"✅ Currency changed to {loaded_currency}"})
    except Exception as exc:
        logger.error("api_set_currency error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Position Size Calculator
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/calc/position-size", methods=["POST"])
def api_calc_position_size():
    """Calculate position size based on strategy and entry/stop prices."""
    data = request.get_json(silent=True) or {}
    strategy = data.get("strategy", "SEPA")
    entry_price = float(data.get("entry_price") or 0)
    stop_price = float(data.get("stop_price") or 0)
    star_rating = data.get("star_rating")
    account_size = float(data.get("account_size") or C.ACCOUNT_SIZE)

    if not entry_price or not stop_price or stop_price >= entry_price:
        return jsonify({"ok": False, "error": "無效的 entry/stop 價格"}), 400

    try:
        if strategy.upper() == "SEPA":
            stop_distance = entry_price - stop_price
            stop_pct = (stop_distance / entry_price) * 100
            if stop_pct > C.MAX_STOP_LOSS_PCT:
                stop_price = entry_price * (1 - C.MAX_STOP_LOSS_PCT / 100)
                stop_pct = C.MAX_STOP_LOSS_PCT

            risk_dollar = account_size * (C.MAX_RISK_PER_TRADE_PCT / 100)
            risk_per_share = entry_price - stop_price
            shares = int(risk_dollar / risk_per_share) if risk_per_share > 0 else 0
            position_value = shares * entry_price
            position_pct = position_value / account_size * 100

            if position_pct > C.MAX_POSITION_SIZE_PCT:
                shares = int(account_size * C.MAX_POSITION_SIZE_PCT / 100 / entry_price)
                position_value = shares * entry_price
                position_pct = position_value / account_size * 100

            target_multiplier = 1 + C.IDEAL_RISK_REWARD * stop_pct / 100
            target_price = entry_price * target_multiplier
            rr_ratio = ((target_price - entry_price) / (entry_price - stop_price)
                        if (entry_price - stop_price) > 0 else 0)

            result = {
                "position_size": round(position_value, 0),
                "shares": round(shares, 2),
                "risk_dollar": round(risk_dollar, 0),
                "risk_pct": round(C.MAX_RISK_PER_TRADE_PCT, 2),
                "account_pct": round(position_pct, 2),
                "rr_ratio": round(rr_ratio, 2),
                "target_price": round(target_price, 2),
                "stop_pct": round(stop_pct, 2),
            }
        elif strategy.upper() == "QM":
            from modules.qm_position_rules import calc_qm_position_size
            star_rating = float(star_rating or 3.0)
            qm_result = calc_qm_position_size(star_rating, entry_price, stop_price, account_size)
            result = {
                "position_size": qm_result.get("position_value", 0),
                "shares": qm_result.get("shares", 0),
                "risk_dollar": qm_result.get("risk_dollar", 0),
                "risk_pct": qm_result.get("risk_pct_acct", 0),
                "allocation_pct": (qm_result.get("position_pct_min", 0)
                                   + qm_result.get("position_pct_max", 0)) / 2,
                "max_position_pct": qm_result.get("position_pct_max", 25),
            }
        elif strategy.upper() == "ML":
            from modules.ml_position_rules import calc_ml_position_size
            ml_result = calc_ml_position_size(entry_price, stop_price, account_size)
            result = {
                "position_size": ml_result.get("position_value", 0),
                "shares": ml_result.get("shares", 0),
                "risk_dollar": ml_result.get("risk_dollars", 0),
                "risk_pct": ml_result.get("risk_pct_account", 0),
                "allocation_pct": ml_result.get("position_pct", 0),
                "max_position_pct": getattr(C, "ML_MAX_SINGLE_POSITION_PCT", 25),
            }
        else:
            return jsonify({"ok": False, "error": f"未知策略: {strategy}"}), 400

        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.error("[calc-position-size] %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Admin — Server restart & YF session reset
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/admin/restart", methods=["POST"])
def api_restart_server():
    """Gracefully restart the Flask development server (Windows-compatible)."""
    logger.info("[RESTART] Server restart requested from web interface")

    def _restart():
        import time
        logger.info("[RESTART] Restarting server in 1.5 seconds...")
        time.sleep(1.5)
        logger.info("[RESTART] Creating new Flask process...")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(
                [sys.executable, str(ROOT / "app.py")],
                cwd=str(ROOT),
                creationflags=creationflags,
                start_new_session=(sys.platform != "win32"),
            )
            logger.info("[RESTART] New Flask process started successfully")
        except Exception as exc:
            logger.error("[RESTART] Failed to start new process: %s", exc)
        time.sleep(0.2)
        logger.info("[RESTART] Terminating old Flask process...")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    try:
        threading.Thread(target=_restart, daemon=False).start()
        return jsonify({"ok": True, "message": "Server restarting..."}), 200
    except Exception as exc:
        logger.error("[RESTART] Error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/admin/reset-yf-session", methods=["POST"])
def api_reset_yf_session():
    """Reset the yfinance cookie/crumb to force re-authentication."""
    try:
        from modules.data_pipeline import _reset_yf_crumb
        ok = _reset_yf_crumb()
        return jsonify({
            "ok": ok,
            "message": ("yfinance session reset — next request will re-authenticate"
                        if ok else "Session reset failed — check server logs")
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram Bot Control
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/telegram/status", methods=["GET"])
def api_telegram_status():
    """Get current Telegram Bot polling status."""
    import routes.helpers as H
    try:
        is_running = (H._tg_thread is not None and H._tg_thread.is_alive()
                      if H._tg_thread else False)
        return jsonify({"ok": True, "enabled": H._tg_enabled,
                        "running": is_running,
                        "config_enabled": C.TG_ENABLED}), 200
    except Exception as exc:
        logger.error("Failed to get Telegram status: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/telegram/toggle", methods=["POST"])
def api_telegram_toggle():
    """Start or stop Telegram Bot polling."""
    import routes.helpers as H
    if not C.TG_ENABLED:
        return jsonify({"ok": False,
                        "error": "Telegram Bot not enabled in config (TG_ENABLED=False)"}), 400

    try:
        if H._tg_thread and H._tg_thread.is_alive():
            logger.info("Stopping Telegram Bot polling...")
            from modules.telegram_bot import stop_polling
            stop_polling()
            H._tg_thread.join(timeout=2)
            H._tg_enabled = False
            logger.info("✅ Telegram Bot polling stopped")
            return jsonify({"ok": True, "action": "stop",
                            "message": "Telegram Bot polling stopped"}), 200
        else:
            logger.info("Starting Telegram Bot polling...")
            from modules.telegram_bot import start_polling
            H._tg_thread = threading.Thread(target=start_polling, daemon=True)
            H._tg_thread.start()
            H._tg_enabled = True
            logger.info("✅ Telegram Bot polling started")
            return jsonify({"ok": True, "action": "start",
                            "message": "Telegram Bot polling started"}), 200
    except Exception as exc:
        logger.error("Failed to toggle Telegram: %s", exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
