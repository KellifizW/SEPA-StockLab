"""
modules/ibkr_client.py  ─  Interactive Brokers API Client

Provides synchronous wrapper around ib_insync for Flask integration.
Manages connection state, orders, positions, and trade execution history.
All methods are thread-safe using synchronous wrappers over async event loop.
"""

import sys
import os
import threading
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

# ── Setup root path and imports ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from ib_insync import IB, Contract, Order, OrderStatus, Execution, Trade
from ib_insync.order import OrderComboLeg
from ib_insync.objects import Position

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Terminal Colors
# ─────────────────────────────────────────────────────────────────────────────
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# ─────────────────────────────────────────────────────────────────────────────
# Global State — Thread-Safe IBKR Client
# ─────────────────────────────────────────────────────────────────────────────
_ib: Optional[IB] = None
_ib_lock = threading.Lock()
_connection_state = "DISCONNECTED"  # DISCONNECTED | CONNECTING | CONNECTED | ERROR
_loop: Optional[asyncio.AbstractEventLoop] = None
_ib_thread: Optional[threading.Thread] = None
_quote_cache: Dict[str, Any] = {}
_last_quote_time: Dict[str, datetime] = {}

# Error tracking for connection failures
_last_error = ""
_connection_failed_count = 0


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Determine active port based on environment
# ─────────────────────────────────────────────────────────────────────────────
def _get_active_port() -> int:
    """
    Determine which port to use (paper or live).
    Currently defaults to paper trading (port 7497 for TWS, 4002 for Gateway).
    TODO: Add market hours detection or runtime toggle.
    """
    return C.IBKR_PORT_PAPER


# ─────────────────────────────────────────────────────────────────────────────
# Async Event Loop Management
# ─────────────────────────────────────────────────────────────────────────────
def _start_event_loop() -> asyncio.AbstractEventLoop:
    """
    Start a dedicated asyncio event loop in a background thread.
    ib_insync requires this loop to maintain connection and handle events.
    """
    global _loop, _ib_thread
    
    if _ib_thread and _ib_thread.is_alive():
        return _loop
    
    def _run_loop(loop: asyncio.AbstractEventLoop):
        """Run asyncio event loop in background thread."""
        asyncio.set_event_loop(loop)
        loop.run_forever()
    
    _loop = asyncio.new_event_loop()
    _ib_thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
    _ib_thread.start()
    
    _logger.info(f"{_GREEN}Background asyncio event loop started{_RESET}")
    return _loop


def _run_async(coro):
    """
    Execute an async coroutine from sync code (Flask route).
    Schedules coroutine on the background event loop and waits for it.
    """
    if not _loop or not _ib_thread or not _ib_thread.is_alive():
        raise RuntimeError("Event loop not running. Call connect() first.")
    
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=C.IBKR_TIMEOUT_SEC)


# ─────────────────────────────────────────────────────────────────────────────
# Public API: Connection Management
# ─────────────────────────────────────────────────────────────────────────────
def connect() -> Dict[str, Any]:
    """
    Connect to IBKR (TWS or IB Gateway).
    
    Returns:
        {
            "success": bool,
            "state": "CONNECTED" | "CONNECTING" | "ERROR",
            "message": str,
            "account": str (if connected),
            "nat_liquidation_value": float (if connected),
        }
    """
    global _ib, _connection_state, _last_error, _connection_failed_count
    
    with _ib_lock:
        if _connection_state == "CONNECTED":
            return {
                "success": True,
                "state": "CONNECTED",
                "message": "Already connected to IBKR",
                "account": _ib.client.account if _ib else "",
            }
        
        if _connection_state == "CONNECTING":
            return {
                "success": False,
                "state": "CONNECTING",
                "message": "Connection in progress...",
            }
        
        try:
            _connection_state = "CONNECTING"
            _start_event_loop()
            
            host = C.IBKR_HOST
            port = _get_active_port()
            client_id = C.IBKR_CLIENT_ID
            
            _logger.info(
                f"{_YELLOW}Connecting to IBKR {C.IBKR_CONNECTION_MODE} "
                f"at {host}:{port} (client_id={client_id}){_RESET}"
            )
            
            _ib = _run_async(_connect_async(host, port, client_id))
            
            account = _ib.client.account if _ib and _ib.isConnected() else ""
            nav = _ib.accountValues("NetLiquidation")[0].value if _ib else 0
            
            _connection_state = "CONNECTED"
            _connection_failed_count = 0
            _last_error = ""
            
            _logger.info(
                f"{_GREEN}✓ Connected to IBKR. Account: {account}, NAV: ${nav:,.2f}{_RESET}"
            )
            
            return {
                "success": True,
                "state": "CONNECTED",
                "message": f"Connected to account {account}",
                "account": account,
                "nav": float(nav),
            }
            
        except Exception as e:
            _connection_state = "ERROR"
            _connection_failed_count += 1
            _last_error = str(e)
            
            _logger.error(f"{_RED}✗ IBKR connection failed: {e}{_RESET}")
            
            return {
                "success": False,
                "state": "ERROR",
                "message": f"Connection failed: {e}",
                "error": str(e),
            }


async def _connect_async(host: str, port: int, client_id: int) -> IB:
    """Async connection helper."""
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id)
    return ib


def disconnect() -> Dict[str, Any]:
    """
    Safely disconnect from IBKR and clean up resources.
    
    Returns: {"success": bool, "message": str}
    """
    global _ib, _connection_state
    
    with _ib_lock:
        try:
            if _ib and _ib.isConnected():
                _run_async(_disconnect_async())
                _ib = None
            
            _connection_state = "DISCONNECTED"
            _logger.info(f"{_GREEN}Disconnected from IBKR{_RESET}")
            
            return {
                "success": True,
                "state": "DISCONNECTED",
                "message": "Disconnected from IBKR",
            }
            
        except Exception as e:
            _logger.error(f"{_RED}Disconnect error: {e}{_RESET}")
            _connection_state = "ERROR"
            
            return {
                "success": False,
                "state": "ERROR",
                "message": f"Disconnect error: {e}",
            }


async def _disconnect_async():
    """Async disconnection helper."""
    if _ib:
        await _ib.disconnectAsync()


def get_status() -> Dict[str, Any]:
    """
    Get current IBKR connection status and account summary.
    
    Returns:
        {
            "state": "CONNECTED" | "CONNECTING" | "ERROR" | "DISCONNECTED",
            "connected": bool,
            "account": str,
            "nav": float,
            "buying_power": float,
            "unrealized_pnl": float,
            "cash": float,
            "last_error": str,
        }
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib or not _ib.isConnected():
            return {
                "state": _connection_state,
                "connected": False,
                "account": "",
                "nav": 0,
                "buying_power": 0,
                "unrealized_pnl": 0,
                "cash": 0,
                "last_error": _last_error,
            }
        
        try:
            account = _ib.client.account
            
            # Get account values (safer than using fields directly)
            acct_vals = {
                av.tag: av.value for av in _ib.accountValues()
            }
            
            nav = float(acct_vals.get("NetLiquidation", 0))
            buying_power = float(acct_vals.get("BuyingPower", 0))
            unrealized_pnl = float(acct_vals.get("UnrealizedPnL", 0))
            cash = float(acct_vals.get("CashBalance", 0))
            
            return {
                "state": _connection_state,
                "connected": True,
                "account": account,
                "nav": nav,
                "buying_power": buying_power,
                "unrealized_pnl": unrealized_pnl,
                "cash": cash,
                "last_error": "",
            }
            
        except Exception as e:
            _logger.error(f"{_RED}Error getting status: {e}{_RESET}")
            return {
                "state": "ERROR",
                "connected": False,
                "account": "",
                "nav": 0,
                "buying_power": 0,
                "unrealized_pnl": 0,
                "cash": 0,
                "last_error": str(e),
            }


# ─────────────────────────────────────────────────────────────────────────────
# Public API: Positions & Account
# ─────────────────────────────────────────────────────────────────────────────
def get_positions() -> List[Dict[str, Any]]:
    """
    Fetch all open positions from IBKR.
    
    Returns:
        [
            {
                "ticker": str,
                "qty": int,
                "avg_cost": float,
                "market_value": float,
                "unrealized_pnl": float,
                "unrealized_pnl_pct": float,
                "market_price": float,
            }
        ]
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            _logger.warning("Not connected to IBKR")
            return []
        
        try:
            positions = []
            for pos in _ib.positions():
                contract = pos.contract
                ticker = contract.symbol
                
                positions.append({
                    "ticker": ticker,
                    "qty": pos.position,
                    "avg_cost": float(pos.avgCost) if pos.avgCost else 0,
                    "market_value": float(pos.marketValue) if pos.marketValue else 0,
                    "unrealized_pnl": float(pos.unrealizedPNL) if pos.unrealizedPNL else 0,
                    "unrealized_pnl_pct": (
                        (float(pos.unrealizedPNL) / float(pos.marketValue) * 100)
                        if pos.marketValue and float(pos.marketValue) != 0
                        else 0
                    ),
                    "market_price": (
                        float(pos.marketValue) / abs(pos.position)
                        if pos.position
                        else 0
                    ),
                })
            
            _logger.info(f"{_GREEN}✓ Fetched {len(positions)} positions from IBKR{_RESET}")
            return positions
            
        except Exception as e:
            _logger.error(f"{_RED}Error fetching positions: {e}{_RESET}")
            return []


def get_executions(days: int = 7) -> List[Dict[str, Any]]:
    """
    Fetch recent execution history (filled orders).
    
    Returns:
        [
            {
                "exec_id": str,
                "time": str (ISO),
                "ticker": str,
                "action": "BUY" | "SELL",
                "qty": int,
                "price": float,
                "commission": float,
            }
        ]
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            return []
        
        try:
            cutoff = datetime.now() - timedelta(days=days)
            executions = []
            
            for trade in _ib.trades():
                for fill in trade.fills:
                    if fill.execution.time < cutoff:
                        continue
                    
                    executions.append({
                        "exec_id": fill.execution.execId,
                        "time": fill.execution.time.isoformat(),
                        "ticker": trade.contract.symbol,
                        "action": fill.execution.side,
                        "qty": fill.execution.shares,
                        "price": float(fill.execution.price),
                        "commission": float(fill.commissionReport.commission)
                        if fill.commissionReport
                        else 0,
                    })
            
            _logger.info(f"{_GREEN}✓ Fetched {len(executions)} executions{_RESET}")
            return sorted(executions, key=lambda x: x["time"], reverse=True)
            
        except Exception as e:
            _logger.error(f"{_RED}Error fetching executions: {e}{_RESET}")
            return []


def get_open_orders() -> List[Dict[str, Any]]:
    """
    Fetch all pending (non-filled) orders.
    
    Returns:
        [
            {
                "order_id": int,
                "ticker": str,
                "action": "BUY" | "SELL",
                "qty": int,
                "order_type": "MKT" | "LMT" | "STP" | "TRAIL",
                "limit_price": float or None,
                "aux_price": float or None,
                "status": "Submitted" | "Accepted" | ...,
                "created_time": str (ISO),
            }
        ]
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            return []
        
        try:
            orders = []
            
            for trade in _ib.trades():
                if trade.orderStatus.status in ("Filled", "Cancelled"):
                    continue
                
                order = trade.order
                contract = trade.contract
                
                orders.append({
                    "order_id": order.orderId,
                    "ticker": contract.symbol,
                    "action": order.action,
                    "qty": order.totalQuantity,
                    "order_type": order.orderType,
                    "limit_price": float(order.lmtPrice) if order.lmtPrice else None,
                    "aux_price": float(order.auxPrice) if order.auxPrice else None,
                    "status": trade.orderStatus.status,
                    "created_time": (
                        trade.orderStatus.whyHeld or "N/A"
                    ),
                })
            
            return orders
            
        except Exception as e:
            _logger.error(f"{_RED}Error fetching open orders: {e}{_RESET}")
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Public API: Order Placement & Management
# ─────────────────────────────────────────────────────────────────────────────
def place_order(
    ticker: str,
    action: str,  # "BUY" or "SELL"
    qty: int,
    order_type: str,  # "MKT" | "LMT" | "STP" | "TRAIL"
    limit_price: Optional[float] = None,
    aux_price: Optional[float] = None,
    trail_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Place an order on IBKR.
    
    Args:
        ticker: Stock symbol (e.g., "AAPL")
        action: "BUY" or "SELL"
        qty: Number of shares
        order_type: "MKT", "LMT", "STP", or "TRAIL"
        limit_price: For LMT orders
        aux_price: For STP orders (trigger price)
        trail_pct: For TRAIL orders (trailing amount as %)
    
    Returns:
        {
            "success": bool,
            "order_id": int or None,
            "ticker": str,
            "action": str,
            "qty": int,
            "order_type": str,
            "message": str,
        }
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            return {
                "success": False,
                "order_id": None,
                "message": "Not connected to IBKR",
            }
        
        if C.IBKR_READONLY:
            return {
                "success": False,
                "order_id": None,
                "message": "IBKR_READONLY mode is enabled. Orders are disabled.",
            }
        
        try:
            # Build contract
            contract = Contract(
                symbol=ticker.upper(),
                secType="STK",
                exchange="SMART",
                currency="USD",
            )
            
            # Build order
            order = Order()
            order.action = action.upper()
            order.totalQuantity = qty
            order.orderType = order_type.upper()
            
            if order_type.upper() == "LMT":
                if limit_price is None:
                    raise ValueError("limit_price required for LMT orders")
                order.lmtPrice = limit_price
            
            elif order_type.upper() == "STP":
                if aux_price is None:
                    raise ValueError("aux_price required for STP orders")
                order.auxPrice = aux_price
            
            elif order_type.upper() == "TRAIL":
                if trail_pct is None:
                    raise ValueError("trail_pct required for TRAIL orders")
                order.trailingPercent = trail_pct
            
            # Place order
            trade = _run_async(_place_order_async(contract, order))
            
            _logger.info(
                f"{_GREEN}✓ Order placed: {action} {qty} {ticker} "
                f"({order_type}) - Order ID: {trade.order.orderId}{_RESET}"
            )
            
            return {
                "success": True,
                "order_id": trade.order.orderId,
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "order_type": order_type,
                "message": f"Order {trade.order.orderId} submitted",
            }
            
        except Exception as e:
            _logger.error(f"{_RED}Error placing order: {e}{_RESET}")
            return {
                "success": False,
                "order_id": None,
                "message": f"Order failed: {e}",
            }


async def _place_order_async(contract: Contract, order: Order) -> Trade:
    """Async order placement helper."""
    trade = _ib.placeOrder(contract, order)
    await trade
    return trade


def cancel_order(order_id: int) -> Dict[str, Any]:
    """
    Cancel an existing order.
    
    Returns: {"success": bool, "message": str}
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            return {
                "success": False,
                "message": "Not connected to IBKR",
            }
        
        try:
            _ib.cancelOrder(_ib.openTrades()[order_id])
            _logger.info(f"{_GREEN}✓ Cancel request sent for order {order_id}{_RESET}")
            
            return {
                "success": True,
                "message": f"Cancel request sent for order {order_id}",
            }
            
        except Exception as e:
            _logger.error(f"{_RED}Error cancelling order: {e}{_RESET}")
            return {
                "success": False,
                "message": f"Cancel failed: {e}",
            }


# ─────────────────────────────────────────────────────────────────────────────
# Public API: Market Data (Quotes)
# ─────────────────────────────────────────────────────────────────────────────
def get_quote(ticker: str) -> Dict[str, Any]:
    """
    Get real-time quote snapshot for a ticker.
    
    Returns:
        {
            "ticker": str,
            "bid": float,
            "ask": float,
            "last": float,
            "volume": int,
            "bid_size": int,
            "ask_size": int,
        }
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib:
            return {
                "ticker": ticker,
                "bid": 0,
                "ask": 0,
                "last": 0,
                "volume": 0,
                "bid_size": 0,
                "ask_size": 0,
                "error": "Not connected",
            }
        
        # Check cache
        now = datetime.now()
        if (ticker in _quote_cache and 
            ticker in _last_quote_time and
            (now - _last_quote_time[ticker]).total_seconds() < C.IBKR_QUOTE_CACHE_SEC):
            return _quote_cache[ticker]
        
        try:
            contract = Contract(
                symbol=ticker.upper(),
                secType="STK",
                exchange="SMART",
                currency="USD",
            )
            
            ticker_data = _run_async(_get_quote_async(contract))
            
            quote = {
                "ticker": ticker.upper(),
                "bid": float(ticker_data.bid) if ticker_data.bid else 0,
                "ask": float(ticker_data.ask) if ticker_data.ask else 0,
                "last": float(ticker_data.last) if ticker_data.last else 0,
                "volume": int(ticker_data.volume) if ticker_data.volume else 0,
                "bid_size": int(ticker_data.bidSize) if ticker_data.bidSize else 0,
                "ask_size": int(ticker_data.askSize) if ticker_data.askSize else 0,
            }
            
            # Cache result
            _quote_cache[ticker.upper()] = quote
            _last_quote_time[ticker.upper()] = now
            
            return quote
            
        except Exception as e:
            _logger.error(f"{_RED}Error fetching quote for {ticker}: {e}{_RESET}")
            return {
                "ticker": ticker.upper(),
                "bid": 0,
                "ask": 0,
                "last": 0,
                "volume": 0,
                "bid_size": 0,
                "ask_size": 0,
                "error": str(e),
            }


async def _get_quote_async(contract: Contract):
    """Async quote fetching helper."""
    ticker = _ib.reqMktData(contract, "", False, False)
    await ticker
    return ticker


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup on module import
# ─────────────────────────────────────────────────────────────────────────────
import atexit

def _cleanup():
    """Clean up resources on exit."""
    try:
        disconnect()
        if _loop:
            _loop.call_soon_threadsafe(_loop.stop)
    except Exception as e:
        _logger.error(f"Error during cleanup: {e}")

atexit.register(_cleanup)
