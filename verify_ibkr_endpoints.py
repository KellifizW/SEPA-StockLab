#!/usr/bin/env python3
"""
Verify all 9 IBKR trading endpoints are functioning correctly.
Run after starting Flask: python verify_ibkr_endpoints.py
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://127.0.0.1:5000"

def test_endpoint(method, path, expected_status=200, data=None):
    """Test a single endpoint and report result."""
    url = f"{BASE_URL}{path}"
    
    try:
        if method == "GET":
            response = requests.get(url, timeout=5)
        elif method == "POST":
            response = requests.post(url, json=data or {}, timeout=5)
        elif method == "DELETE":
            response = requests.delete(url, json=data or {}, timeout=5)
        else:
            response = None
        
        status = response.status_code if response else "N/A"
        ok = response.json().get("ok", False) if response else False
        
        # Result indicator
        symbol = "✓" if (status == expected_status and ok) else "❌"
        
        response_preview = json.dumps(response.json())[:80] if response else "{}"
        print(f"{symbol} {method:6} {path:40} {status} {response_preview}")
        return status == expected_status and ok
        
    except Exception as e:
        print(f"❌ {method:6} {path:40} ERROR: {e}")
        return False


def main():
    """Test all IBKR endpoints."""
    print(f"\n  IBKR Trading Endpoints Verification")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Base URL: {BASE_URL}\n")
    print(f"  {'Symbol':^5} {'Method':^8} {'Path':^42} {'Status':^8} {'Response Preview'}")
    print(f"  {'-'*5} {'-'*8} {'-'*42} {'-'*8} {'-'*50}")
    
    results = {
        "status": test_endpoint("GET", "/api/ibkr/status"),
        "connect": test_endpoint("POST", "/api/ibkr/connect"),
        "positions": test_endpoint("GET", "/api/ibkr/positions"),
        "orders": test_endpoint("GET", "/api/ibkr/orders"),
        "trades": test_endpoint("GET", "/api/ibkr/trades"),
        "quote": test_endpoint("GET", "/api/ibkr/quote/SPY"),
        "disconnect": test_endpoint("POST", "/api/ibkr/disconnect"),
    }
    
    print(f"\n  {'-'*5} {'-'*8} {'-'*42} {'-'*8} {'-'*50}")
    
    # Place order endpoint (after reconnect)
    test_endpoint("POST", "/api/ibkr/connect")
    print(f"\n  Testing order placement (POST /api/ibkr/order)...")
    order_data = {
        "ticker": "SPY",
        "action": "BUY",
        "qty": 1,
        "order_type": "MKT"
    }
    results["place_order"] = test_endpoint("POST", "/api/ibkr/order", data=order_data)
    
    # Summary
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"\n  ╔════════════════════════════════════════╗")
    print(f"  ║  Results: {passed}/{total} endpoints functional          ║")
    print(f"  ╚════════════════════════════════════════╝\n")
    
    if passed == total:
        print(f"  ✓ All IBKR endpoints are working correctly!")
        return 0
    else:
        print(f"  ❌ {total - passed} endpoint(s) failed. Check logs above.")
        return 1


if __name__ == "__main__":
    exit(main())
