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
from ib_insync import IB, Contract, Order, OrderStatus, Execution, Trade  # type: ignore[import-not-found]
from ib_insync.order import OrderComboLeg  # type: ignore[import-not-found]
from ib_insync.objects import Position  # type: ignore[import-not-found]

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
_loop_lock = threading.Lock()  # Protect event loop creation
_loop_ready = threading.Event()  # Signal when event loop is ready
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
    Thread-safe: waits for background thread to be fully ready.
    """
    global _loop, _ib_thread, _loop_ready
    
    with _loop_lock:
        # Check if loop already exists and is running
        if _ib_thread and _ib_thread.is_alive() and _loop:
            # Ensure the loop is set and ready
            if _loop_ready.is_set():
                return _loop
            # Wait if not ready yet
            if not _loop_ready.wait(timeout=2.0):
                raise RuntimeError("Existing event loop failed to ready")
            return _loop
        
        # Clear the ready flag for a fresh start
        _loop_ready.clear()
        
        def _run_loop(loop: asyncio.AbstractEventLoop):
            """Run asyncio event loop in background thread."""
            try:
                asyncio.set_event_loop(loop)
                _loop_ready.set()  # Signal that loop is ready
                _logger.debug(f"{_GREEN}Event loop thread ready{_RESET}")
                loop.run_forever()
            except Exception as e:
                _logger.error(f"{_RED}Event loop error: {e}{_RESET}")
        
        _loop = asyncio.new_event_loop()
        _ib_thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
        _ib_thread.start()
        
        # Wait for loop to be ready (with timeout)
        if not _loop_ready.wait(timeout=5.0):
            raise RuntimeError("Event loop failed to start within 5 seconds")
        
        _logger.info(f"{_GREEN}Background asyncio event loop started{_RESET}")
        return _loop


def _run_async(coro):
    """
    Execute an async coroutine from sync code (Flask route).
    Schedules coroutine on the background event loop and waits for it.
    Thread-safe with error handling.
    """
    try:
        if not _loop or not _ib_thread or not _ib_thread.is_alive():
            raise RuntimeError("Event loop not running")
        
        _logger.debug(f"{_YELLOW}Scheduling coroutine on event loop{_RESET}")
        future = asyncio.run_coroutine_threadsafe(coro, _loop)
        _logger.debug(f"{_YELLOW}Waiting for coroutine result (timeout={C.IBKR_TIMEOUT_SEC}s){_RESET}")
        result = future.result(timeout=C.IBKR_TIMEOUT_SEC)
        _logger.debug(f"{_GREEN}Coroutine completed successfully{_RESET}")
        return result
    except asyncio.TimeoutError as e:
        _logger.error(f"{_RED}TIMEOUT in _run_async: {e}{_RESET}")
        raise RuntimeError(f"IBKR operation timed out after {C.IBKR_TIMEOUT_SEC}s")
    except Exception as e:
        _logger.error(f"{_RED}EXCEPTION in _run_async: {type(e).__name__}: {e}{_RESET}", exc_info=True)
        raise RuntimeError(f"Failed to execute async operation: {str(e)}")


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
                "account": (_ib.managedAccounts()[0] if _ib and _ib.managedAccounts() else "UNKNOWN") if _ib else "",
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
            
            # Get account - managedAccounts() may return empty list if API is in read-only mode
            managed_accts = _ib.managedAccounts() if _ib else []
            account = managed_accts[0] if managed_accts else "UNKNOWN"
            
            # Get NAV - safer to check both length and value
            acct_vals = _ib.accountValues("NetLiquidation") if _ib else []
            nav = 0
            if acct_vals and len(acct_vals) > 0:
                try:
                    nav = float(acct_vals[0].value)
                except (ValueError, IndexError, AttributeError):
                    nav = 0
            
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
                _disconnect_sync()
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


def _disconnect_sync():
    """Synchronous disconnection helper."""
    global _ib
    if _ib:
        _ib.disconnect()


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
            "account_currency": str,  # NEW: "USD", "HKD", etc.
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
                "account_currency": "USD",  # Default fallback
                "last_error": _last_error,
            }
        
        try:
            # Get account safely
            managed_accts = _ib.managedAccounts() if _ib else []
            account = managed_accts[0] if managed_accts else "UNKNOWN"
            
            # Get ALL account values with full object (not just dict)
            all_acct_vals = _ib.accountValues()
            acct_vals = {
                av.tag: av.value for av in all_acct_vals
            }
            
            # Try to detect account currency from various possible fields
            account_currency = "USD"  # default
            
            # Method 1: Direct Currency field
            if "Currency" in acct_vals:
                account_currency = acct_vals["Currency"].upper()
            # Method 2: BaseCurrency field
            elif "BaseCurrency" in acct_vals:
                account_currency = acct_vals["BaseCurrency"].upper()
            # Method 3: Check AccountCurrency field (might exist)
            elif "AccountCurrency" in acct_vals:
                account_currency = acct_vals["AccountCurrency"].upper()
            # Method 4: Look for currency in the raw AccountValue objects
            else:
                for av in all_acct_vals:
                    if hasattr(av, 'currency') and av.currency:
                        account_currency = av.currency.upper()
                        break
                    # Check if tag contains currency info
                    if av.tag == "Currency":
                        account_currency = av.value.upper()
                        break
            
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
                "account_currency": account_currency,  # NEW: Detected or defaulted currency
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
                "account_currency": "USD",  # Default fallback
                "last_error": str(e),
            }


def get_account_detail() -> Dict[str, Any]:
    """
    Get detailed multi-currency account breakdown from IBKR.

    Returns:
        {
            "connected": bool,
            "nav": float,               # Net Liquidation Value in base currency
            "cash_by_currency": {       # Cash per currency, e.g. {"USD": 5000, "HKD": 50000}
                "USD": float,
                "HKD": float,
                ...
            },
            "stock_value": float,       # Total stock market value in base currency
            "total_cash": float,        # Total cash in base currency
            "unrealized_pnl": float,
            "account": str,
            "base_currency": str,       # Account base currency
        }
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib or not _ib.isConnected():
            return {"connected": False, "error": "Not connected to IBKR"}

        try:
            managed_accts = _ib.managedAccounts() if _ib else []
            account = managed_accts[0] if managed_accts else "UNKNOWN"

            all_acct_vals = _ib.accountValues()

            cash_by_currency: Dict[str, float] = {}
            stock_value = 0.0
            total_cash_base = 0.0
            nav = 0.0
            unrealized_pnl = 0.0
            realized_pnl = 0.0
            buying_power = 0.0
            available_funds = 0.0
            excess_liquidity = 0.0
            maint_margin = 0.0
            init_margin = 0.0
            gross_position = 0.0
            base_currency = "HKD"  # default; will be overridden

            for av in all_acct_vals:
                try:
                    val = float(av.value)
                except (ValueError, TypeError):
                    continue

                # Cash balance per individual currency (exclude BASE summary)
                if av.tag == "CashBalance" and av.currency and av.currency != "BASE":
                    if av.currency not in cash_by_currency:
                        cash_by_currency[av.currency] = 0.0
                    cash_by_currency[av.currency] += val

                # Total cash in base currency
                elif av.tag == "TotalCashBalance" and av.currency == "BASE":
                    total_cash_base = val

                # Stock market value in base currency
                elif av.tag == "StockMarketValue" and av.currency == "BASE":
                    stock_value = val

                # Net Liquidation in base currency
                elif av.tag == "NetLiquidation" and av.currency == "BASE":
                    nav = val

                # Unrealized PnL
                elif av.tag == "UnrealizedPnL" and av.currency == "BASE":
                    unrealized_pnl = val

                # Realized PnL (today's closed trades)
                elif av.tag == "RealizedPnL" and av.currency == "BASE":
                    realized_pnl = val

                # Buying power (max purchasable including margin)
                elif av.tag == "BuyingPower" and av.currency == "BASE":
                    buying_power = val

                # Available funds (cash after margin requirements)
                elif av.tag == "AvailableFunds" and av.currency == "BASE":
                    available_funds = val

                # Excess liquidity (margin safety buffer to avoid liquidation)
                elif av.tag == "ExcessLiquidity" and av.currency == "BASE":
                    excess_liquidity = val

                # Maintenance margin requirement
                elif av.tag == "MaintMarginReq" and av.currency == "BASE":
                    maint_margin = val

                # Initial margin requirement for new positions
                elif av.tag == "InitMarginReq" and av.currency == "BASE":
                    init_margin = val

                # Gross position value (total market value of all positions)
                elif av.tag == "GrossPositionValue" and av.currency == "BASE":
                    gross_position = val

                # Detect base currency
                elif av.tag == "ExchangeRate" and av.currency:
                    base_currency = av.currency  # ExchangeRate currency = base currency

            # Fallback: use config
            if base_currency == "HKD":
                import trader_config as _C
                base_currency = _C.ACCOUNT_BASE_CURRENCY

            return {
                "connected": True,
                "account": account,
                "nav": nav,
                "cash_by_currency": cash_by_currency,
                "stock_value": stock_value,
                "total_cash": total_cash_base,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "buying_power": buying_power,
                "available_funds": available_funds,
                "excess_liquidity": excess_liquidity,
                "maint_margin": maint_margin,
                "init_margin": init_margin,
                "gross_position": gross_position,
                "base_currency": base_currency,
            }

        except Exception as e:
            _logger.error(f"{_RED}Error getting account detail: {e}{_RESET}")
            return {"connected": False, "error": str(e)}


def convert_currency(
    from_currency: str,
    to_currency: str,
    amount: float,
) -> Dict[str, Any]:
    """
    Convert currency via IBKR IDEALPRO FOREX market order.

    IBKR FOREX contract convention:
      - symbol = USD (base), currency = HKD (quote) → pair is USD/HKD
      - BUY  USD.HKD = buy USD,  pay HKD  (HKD → USD)
      - SELL USD.HKD = sell USD, receive HKD (USD → HKD)
      - totalQuantity is always expressed in USD units

    Supported pairs: HKD ↔ USD only (IDEALPRO).
    For US stocks, IBKR auto-converts via IB Smart Routing — explicit
    conversion via this function gives better IDEALPRO rates.

    Args:
        from_currency: Source currency, e.g. "HKD"
        to_currency:   Target currency, e.g. "USD"
        amount:        Amount in from_currency to convert

    Returns:
        {"success": bool, "order_id": int, "qty_usd": int, "status": str, ...}
    """
    with _ib_lock:
        if _connection_state != "CONNECTED" or not _ib or not _ib.isConnected():
            return {"success": False, "error": "Not connected to IBKR"}

        try:
            from_currency = from_currency.upper()
            to_currency = to_currency.upper()

            # Only support USD ↔ HKD for now
            supported = {("HKD", "USD"), ("USD", "HKD")}
            if (from_currency, to_currency) not in supported:
                return {
                    "success": False,
                    "error": f"Unsupported pair: {from_currency}→{to_currency}. Supported: HKD↔USD",
                }

            # IDEALPRO pair: USD.HKD
            # totalQuantity is always in USD (the major currency)
            import trader_config as _C
            rate = _C.USD_TO_HKD_RATE

            if from_currency == "HKD" and to_currency == "USD":
                action = "BUY"
                qty_usd = round(amount / rate)  # HKD ÷ rate = USD to buy
            else:  # USD → HKD
                action = "SELL"
                qty_usd = round(amount)  # sell this many USD

            if qty_usd <= 0:
                return {"success": False, "error": "Amount too small (< 1 USD)"}

            contract = Contract(
                symbol="USD",
                secType="CASH",
                currency="HKD",
                exchange="IDEALPRO",
            )
            _run_async(_ib.qualifyContractsAsync(contract))

            order = Order()
            order.action = action
            order.orderType = "MKT"
            order.totalQuantity = qty_usd

            trade = _run_async(_place_order_async(contract, order))

            _logger.info(
                f"{_GREEN}✓ FX Order: {action} {qty_usd} USD.HKD"
                f" (Order ID: {trade.order.orderId}){_RESET}"
            )

            return {
                "success": True,
                "order_id": trade.order.orderId,
                "action": action,
                "pair": "USD.HKD",
                "qty_usd": qty_usd,
                "from_currency": from_currency,
                "to_currency": to_currency,
                "approx_amount": amount,
                "status": trade.orderStatus.status if trade.orderStatus else "Submitted",
                "message": (
                    f"兌換訂單已提交 / FX order submitted: "
                    f"{action} {qty_usd:,} USD.HKD (Order #{trade.order.orderId})"
                ),
            }

        except Exception as e:
            _logger.error(f"{_RED}FX conversion error: {e}{_RESET}")
            return {"success": False, "error": str(e)}


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
    trade = _ib.placeOrder(contract, order)  # type: ignore[union-attr]
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
    ticker = _ib.reqMktData(contract, "", False, False)  # type: ignore[union-attr]
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
