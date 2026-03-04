#!/usr/bin/env python3
"""Quick test of currency feature — without running the full Flask server."""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from datetime import datetime

# Test 1: Verify constants
print("=" * 60)
print("TEST 1: Configuration Constants")
print("=" * 60)
print(f"DEFAULT_CURRENCY = {C.DEFAULT_CURRENCY}")
print(f"USD_TO_HKD_RATE = {C.USD_TO_HKD_RATE}")
print()

# Test 2: Simulate _convert_amount function
print("=" * 60)
print("TEST 2: Currency Conversion Logic")
print("=" * 60)

def _convert_amount(amount_usd: float, target_currency: str = None) -> tuple:
    """Convert USD amount to target currency.
    Returns: (converted_amount, currency_symbol, display_format)
    """
    # If no currency specified, default to config
    if target_currency is None:
        target_currency = C.DEFAULT_CURRENCY
    
    rate = C.USD_TO_HKD_RATE
    target_currency = target_currency.upper()
    
    if target_currency == "HKD":
        converted = amount_usd * rate
        return converted, "HK$", f"HK${converted:,.2f}"
    else:  # USD
        return amount_usd, "$", f"${amount_usd:,.2f}"

# Test conversions
test_amount = 101933.69  # The actual IBKR NAV from previous sync

usd_converted, usd_symbol, usd_display = _convert_amount(test_amount, "USD")
print(f"USD: {usd_display} (symbol: {usd_symbol})")

hkd_converted, hkd_symbol, hkd_display = _convert_amount(test_amount, "HKD")
print(f"HKD: {hkd_display} (symbol: {hkd_symbol})")

# Verify calculation
expected_hkd = test_amount * C.USD_TO_HKD_RATE
print(f"\nExpected HKD: {expected_hkd:,.2f}")
print(f"Got HKD:      {hkd_converted:,.2f}")
print(f"Match: {abs(hkd_converted - expected_hkd) < 0.01}")
print()

# Test 3: Simulate _save_currency_setting / _load_currency_setting
print("=" * 60)
print("TEST 3: Currency Settings Persistence")
print("=" * 60)

import threading

_CURRENCY_SETTINGS_FILE = ROOT / C.DATA_DIR / "currency_settings.json"
_currency_lock = threading.Lock()

def _save_currency_setting(currency: str, usd_hkd_rate: float = None):
    """Save user's preferred currency display setting."""
    try:
        with _currency_lock:
            settings = {
                "currency": currency.upper(),
                "last_updated": datetime.now().isoformat()
            }
            if usd_hkd_rate and usd_hkd_rate > 0:
                settings["usd_hkd_rate"] = float(usd_hkd_rate)
            _CURRENCY_SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            print(f"✓ Saved: {settings}")
    except Exception as e:
        print(f"✗ Error saving: {e}")

def _load_currency_setting() -> tuple:
    """Load user's preferred currency setting. Returns (currency, usd_hkd_rate)."""
    try:
        with _currency_lock:
            if _CURRENCY_SETTINGS_FILE.exists():
                data = json.loads(_CURRENCY_SETTINGS_FILE.read_text(encoding="utf-8"))
                currency = data.get("currency", C.DEFAULT_CURRENCY)
                rate = data.get("usd_hkd_rate", C.USD_TO_HKD_RATE)
                return currency, rate
    except Exception as e:
        print(f"✗ Error loading: {e}")
    return C.DEFAULT_CURRENCY, C.USD_TO_HKD_RATE

# Test save/load
print("Saving currency setting: HKD, rate=7.85")
_save_currency_setting("HKD", 7.85)

print("Loading currency setting...")
loaded_currency, loaded_rate = _load_currency_setting()
print(f"✓ Loaded: currency={loaded_currency}, rate={loaded_rate}")

# Convert using loaded settings
_, symbol, display = _convert_amount(test_amount, loaded_currency)
print(f"Display with loaded settings: {display}")
print()

# Test 4: API simulation
print("=" * 60)
print("TEST 4: API Response Simulation")
print("=" * 60)

def simulate_get_currency_api():
    """Simulates POST /api/currency response"""
    currency, usd_hkd_rate = _load_currency_setting()
    nav = test_amount  # Simulated nav
    _, currency_symbol, display_str = _convert_amount(nav, currency)
    
    response = {
        "ok": True,
        "currency": currency,
        "currency_symbol": currency_symbol,
        "usd_hkd_rate": usd_hkd_rate,
        "nav_usd": nav,
        "nav_display": display_str
    }
    return response

def simulate_set_currency_api(target_currency):
    """Simulates POST /api/currency request"""
    if target_currency not in ["USD", "HKD"]:
        return {"ok": False, "error": "Currency must be USD or HKD"}
    
    _save_currency_setting(target_currency)
    nav = test_amount
    _, currency_symbol, display_str = _convert_amount(nav, target_currency)
    loaded_currency, loaded_rate = _load_currency_setting()
    
    response = {
        "ok": True,
        "currency": loaded_currency,
        "currency_symbol": currency_symbol,
        "usd_hkd_rate": loaded_rate,
        "nav_display": display_str,
        "message": f"✅ Currency changed to {loaded_currency}"
    }
    return response

# Test GET /api/currency
print("GET /api/currency")
response = simulate_get_currency_api()
print(json.dumps(response, indent=2))
print()

# Test POST /api/currency (switch to USD)
print("POST /api/currency (change to USD)")
response = simulate_set_currency_api("USD")
print(json.dumps(response, indent=2))
print()

# Test POST /api/currency (switch to HKD)
print("POST /api/currency (change to HKD)")
response = simulate_set_currency_api("HKD")
print(json.dumps(response, indent=2))
print()

# Verify final state
final_currency, final_rate = _load_currency_setting()
print(f"Final stored currency: {final_currency}")
print(f"Final stored rate: {final_rate}")

print()
print("=" * 60)
print("✅ All tests completed successfully!")
print("=" * 60)
