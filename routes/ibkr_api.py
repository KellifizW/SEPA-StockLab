"""Interactive Brokers (IBKR) API routes.

All routes are gated behind ``C.IBKR_ENABLED``.  The IBKRClient is
lazily imported at first use so the module loads even when ``ib_insync``
is not installed.
"""

import logging
from datetime import datetime
from typing import Optional
from flask import Blueprint, request, jsonify

import trader_config as C
from routes.helpers import _load_nav_cache

bp = Blueprint("ibkr_api", __name__)
logger = logging.getLogger(__name__)


def _client():
    """Lazy-import the ibkr_client *module* (module-level functions).
    
    Raises ImportError if ib_insync is not installed.
    """
    try:
        from modules import ibkr_client
        return ibkr_client
    except ImportError as e:
        logger.error(f"Failed to import ibkr_client: {e}")
        raise ImportError(
            f"IBKR module not available. Please install: pip install ib_insync. Error: {e}"
        ) from e


def _check_ib_available() -> tuple[bool, Optional[str]]:
    """Check if IBKR is available and enabled.
    
    Returns:
        (success: bool, error_message: Optional[str])
    """
    if not C.IBKR_ENABLED:
        return False, "IBKR integration is disabled (IBKR_ENABLED=False)"
    
    try:
        _client()
        return True, None
    except (ImportError, RuntimeError) as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# Status / Connection
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ibkr/status", methods=["GET"])
def api_ibkr_status():
    """Get IBKR connection status and account summary."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503  # Service Unavailable
    try:
        status = _client().get_status()
        return jsonify({"ok": True, "data": status})
    except Exception as exc:
        logger.error("api_ibkr_status error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/diagnostics", methods=["GET"])
def api_ibkr_diagnostics():
    """Get detailed IBKR diagnostics including connection, positions, and trades.
    
    Helps debug why positions/trades aren't showing.
    """
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({
            "ok": False,
            "error": error_msg,
            "diagnostics": {"ibkr_enabled": False}
        }), 503
    
    try:
        client_module = _client()
        
        # Get basic connection status
        status = client_module.get_status()
        
        # Try to fetch positions
        positions = client_module.get_positions()
        
        # Try to fetch trades
        trades = client_module.get_executions(days=7)
        
        # Get detailed position cache diagnostics
        position_diagnostic = client_module._diagnose_position_cache()
        
        # Analyze why data might be empty
        diagnosis = {
            "connection_state": status.get("state"),
            "is_connected": status.get("connected"),
            "account": status.get("account"),
            "nav": status.get("nav"),
            "unrealized_pnl": status.get("unrealized_pnl"),
            "last_error": status.get("last_error"),
            "positions_count": len(positions),
            "trades_count": len(trades),
            "position_cache_diagnostic": position_diagnostic,
            "possible_issues": [],
        }
        
        # Detect issues
        if not status.get("connected"):
            diagnosis["possible_issues"].append(
                "❌ IBKR is NOT connected. Click 'Connect' button on web UI."
            )
        elif status.get("state") != "CONNECTED":
            diagnosis["possible_issues"].append(
                f"⚠️ Connection state is {status.get('state')}, not CONNECTED"
            )
        
        # Check if positions API empty but account has positions
        if (status.get("connected") and 
            len(positions) == 0 and 
            position_diagnostic.get("has_positions")):
            diagnosis["possible_issues"].append(
                f"⚠️ ALERT: Positions API returned 0 but account shows: "
                f"GrossPositionValue=${position_diagnostic['diagnostic'].get('GrossPositionValue', 0):.2f}, "
                f"UnrealizedPnL=${position_diagnostic['diagnostic'].get('UnrealizedPnL', 0):.2f}. "
                f"This indicates ib_insync cache may not have synced properly. "
                f"Try: 1) Restart Flask app, 2) Re-connect to IBKR"
            )
        
        if status.get("connected") and len(positions) == 0 and status.get("unrealized_pnl") == 0:
            diagnosis["possible_issues"].append(
                "✓ Connected and no positions. This is OK if you have no open trades."
            )
        
        if status.get("connected") and len(trades) == 0:
            diagnosis["possible_issues"].append(
                "✓ Connected but no recent trades. Check date filter or trading history."
            )
        
        if not diagnosis["possible_issues"]:
            diagnosis["possible_issues"].append("✓ All systems operational!")
        
        return jsonify({
            "ok": True,
            "data": {
                "status": status,
                "positions": positions,
                "trades": trades,
                "diagnostics": diagnosis,
            }
        })
    
    except Exception as exc:
        logger.error("api_ibkr_diagnostics error: %s", exc, exc_info=True)
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": {"error_type": type(exc).__name__}
        }), 500


@bp.route("/api/ibkr/connect", methods=["POST"])
def api_ibkr_connect():
    """Initiate IBKR connection."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503  # Service Unavailable
    try:
        result = _client().connect()
        return jsonify({"ok": result.get("success"), "data": result})
    except Exception as exc:
        logger.error("api_ibkr_connect error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/disconnect", methods=["POST"])
def api_ibkr_disconnect():
    """Disconnect from IBKR."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503  # Service Unavailable
    try:
        result = _client().disconnect()
        return jsonify({"ok": result.get("success"), "data": result})
    except Exception as exc:
        logger.error("api_ibkr_disconnect error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Account Detail / Convert
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/account/detail", methods=["GET"])
def api_account_detail():
    """Get detailed multi-currency account breakdown from IBKR."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        detail = _client().get_account_detail()
        if detail.get("connected"):
            return jsonify({"ok": True, "data": detail, "source": "live"})

        cached = _load_nav_cache()
        if cached and cached.get("nav", 0) > 0:
            return jsonify({
                "ok": True, "source": "cached",
                "data": {
                    "connected": False,
                    "nav": cached.get("nav", 0),
                    "cash_by_currency": {},
                    "stock_value": 0,
                    "total_cash": cached.get("nav", 0),
                    "unrealized_pnl": 0,
                    "account": cached.get("account", ""),
                    "base_currency": C.ACCOUNT_BASE_CURRENCY,
                    "note": "IBKR not connected — showing cached NAV only",
                }})

        return jsonify({"ok": False,
                        "error": detail.get("error", "Not connected")}), 503
    except Exception as exc:
        logger.error("api_account_detail error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/account/convert", methods=["POST"])
def api_account_convert():
    """Convert currency via IBKR IDEALPRO FOREX."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        body = request.get_json(force=True) or {}
        from_currency = body.get("from_currency", "").upper()
        to_currency = body.get("to_currency", "").upper()
        amount = float(body.get("amount", 0))

        if not from_currency or not to_currency:
            return jsonify({"ok": False,
                            "error": "from_currency and to_currency required"}), 400
        if amount <= 0:
            return jsonify({"ok": False, "error": "amount must be positive"}), 400
        if from_currency == to_currency:
            return jsonify({"ok": False,
                            "error": "from and to currency are the same"}), 400

        result = _client().convert_currency(from_currency, to_currency, amount)
        if result.get("success"):
            logger.info("✅ FX conversion: %s %s → %s (Order #%s)",
                        f"{amount:,.2f}", from_currency, to_currency,
                        result.get("order_id"))
            return jsonify({"ok": True, "data": result})
        else:
            return jsonify({"ok": False,
                            "error": result.get("error", "Conversion failed")}), 500
    except Exception as exc:
        logger.error("api_account_convert error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Positions / Orders / Trades
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ibkr/positions", methods=["GET"])
def api_ibkr_positions():
    """Fetch IBKR positions and sync with local positions."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        from modules import position_monitor, db

        # Auto-connect if not already connected
        client = _client()
        if not client.is_ibkr_connected():
            logger.info("IBKR not connected, attempting to reconnect...")
            client.connect()

        ibkr_positions = client.get_positions()
        if not ibkr_positions:
            return jsonify({"ok": True,
                            "data": {"positions": [], "message": "No positions found"}})

        # UPSERT: add new positions AND update existing ones from IBKR data
        local_data = position_monitor._load()
        if "positions" not in local_data:
            local_data["positions"] = {}

        for ibkr_pos in ibkr_positions:
            ticker = ibkr_pos["ticker"]
            if ticker in local_data["positions"]:
                # Update market data for existing position
                local_data["positions"][ticker].update({
                    "market_price": ibkr_pos["market_price"],
                    "unrealized_pnl": ibkr_pos["unrealized_pnl"],
                    "unrealized_pnl_pct": ibkr_pos["unrealized_pnl_pct"],
                    "qty": ibkr_pos["qty"],
                    "market_value": ibkr_pos["market_value"],
                })
            else:
                # Add new position from IBKR (not yet in local store)
                local_data["positions"][ticker] = {
                    # Fields expected by dashboard.html / positions.html
                    "buy_price": ibkr_pos["avg_cost"],
                    "entry_price": ibkr_pos["avg_cost"],
                    "stop_loss": 0,
                    "target": 0,
                    "rr": 0,
                    "shares": ibkr_pos["qty"],
                    "risk_dollar": 0,
                    # IBKR market data
                    "qty": ibkr_pos["qty"],
                    "avg_cost": ibkr_pos["avg_cost"],
                    "market_price": ibkr_pos["market_price"],
                    "market_value": ibkr_pos["market_value"],
                    "unrealized_pnl": ibkr_pos["unrealized_pnl"],
                    "unrealized_pnl_pct": ibkr_pos["unrealized_pnl_pct"],
                    "source": "ibkr_sync",
                }
            logger.info("IBKR sync upserted position: %s qty=%s", ticker, ibkr_pos["qty"])

        position_monitor._save(local_data)

        for pos in ibkr_positions:
            try:
                db.log_position_action(
                    ticker=pos["ticker"], action="SYNC",
                    price=pos["market_price"], shares=pos["qty"],
                    stop_price=None,
                    pnl_pct=pos["unrealized_pnl_pct"],
                    note=f"IBKR sync: {pos['unrealized_pnl_pct']:.2f}% unrealized PnL",
                )
            except Exception as db_exc:
                logger.warning("db.log_position_action failed for %s: %s", pos["ticker"], db_exc)

        return jsonify({"ok": True,
                        "data": {"positions": ibkr_positions,
                                 "synced_count": len(ibkr_positions)}})
    except Exception as exc:
        logger.error("api_ibkr_positions error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/orders", methods=["GET"])
def api_ibkr_orders():
    """Fetch pending (open) orders from IBKR."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        orders = _client().get_open_orders()
        return jsonify({"ok": True, "data": {"orders": orders}})
    except Exception as exc:
        logger.error("api_ibkr_orders error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/trades", methods=["GET"])
def api_ibkr_trades():
    """Fetch recent execution history from IBKR."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        from modules import db
        days = request.args.get("days", default=7, type=int)
        
        # Auto-connect if not already connected
        client = _client()
        if not client.is_ibkr_connected():
            logger.info("IBKR not connected, attempting to reconnect...")
            client.connect()
        
        executions = client.get_executions(days=days)
        # Wrap db call separately so a DB error never blocks the trade history response
        try:
            db_orders = db.query_ibkr_orders(days=days)
        except Exception as db_exc:
            logger.warning("db.query_ibkr_orders failed: %s", db_exc)
            db_orders = []
        return jsonify({"ok": True,
                        "data": {"executions": executions,
                                 "db_orders": db_orders,
                                 "count": len(executions)}})
    except Exception as exc:
        logger.error("api_ibkr_trades error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Place / Cancel Order
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ibkr/order", methods=["POST"])
def api_ibkr_place_order():
    """Place an IBKR order (MKT, LMT, STP, TRAIL)."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        from modules import db

        data = request.get_json() or {}
        ticker = data.get("ticker", "").strip().upper()
        action = data.get("action", "").upper()
        qty = int(data.get("qty", 0))
        order_type = data.get("order_type", "MKT").upper()

        if not ticker or not action or qty <= 0:
            return jsonify({"ok": False,
                            "error": "Missing or invalid ticker, action, or qty"}), 400

        limit_price = aux_price = trail_pct = None
        if order_type == "LMT":
            limit_price = float(data.get("limit_price", 0))
            if limit_price <= 0:
                return jsonify({"ok": False,
                                "error": "limit_price required for LMT"}), 400
        elif order_type == "STP":
            aux_price = float(data.get("aux_price", 0))
            if aux_price <= 0:
                return jsonify({"ok": False,
                                "error": "aux_price required for STP"}), 400
        elif order_type == "TRAIL":
            trail_pct = float(data.get("trail_pct", 0))
            if trail_pct <= 0:
                return jsonify({"ok": False,
                                "error": "trail_pct required for TRAIL"}), 400

        result = _client().place_order(
            ticker=ticker, action=action, qty=qty,
            order_type=order_type, limit_price=limit_price,
            aux_price=aux_price, trail_pct=trail_pct)

        if result.get("success"):
            db.append_ibkr_order({
                "order_id": result.get("order_id"),
                "order_time": datetime.now().isoformat(),
                "ticker": ticker, "action": action,
                "order_type": order_type, "qty": qty,
                "limit_price": limit_price, "aux_price": aux_price,
                "trail_pct": trail_pct, "fill_price": None,
                "status": "Submitted", "commission": None,
                "pnl": None, "note": data.get("note", ""),
            })

        return jsonify({"ok": result.get("success"), "data": result})
    except Exception as exc:
        logger.error("api_ibkr_place_order error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/order/<int:order_id>", methods=["DELETE"])
def api_ibkr_cancel_order(order_id: int):
    """Cancel an IBKR order by ID."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        result = _client().cancel_order(order_id)
        return jsonify({"ok": result.get("success"), "data": result})
    except Exception as exc:
        logger.error("api_ibkr_cancel_order error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/ibkr/quote/<ticker>", methods=["GET"])
def api_ibkr_quote(ticker: str):
    """Get real-time quote snapshot for a ticker."""
    available, error_msg = _check_ib_available()
    if not available:
        return jsonify({"ok": False, "error": error_msg}), 503
    try:
        quote = _client().get_quote(ticker)
        return jsonify({"ok": "error" not in quote, "data": quote})
    except Exception as exc:
        logger.error("api_ibkr_quote error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
