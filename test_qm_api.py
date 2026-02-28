#!/usr/bin/env python3
"""
Test the /api/qm/analyze endpoint to ensure it returns correct data.
"""
import requests
import json

BASE_URL = "http://localhost:5000"

def test_qm_api():
    print("=" * 70)
    print("Testing /api/qm/analyze API Endpoint")
    print("=" * 70)
    
    # Test ASTI
    ticker = "ASTI"
    print(f"\nTesting ticker: {ticker}")
    
    resp = requests.post(f"{BASE_URL}/api/qm/analyze", 
                        json={"ticker": ticker},
                        timeout=30)
    
    if resp.status_code != 200:
        print(f"ERROR: Status code {resp.status_code}")
        print(f"Response: {resp.text}")
        return False
    
    data = resp.json()
    
    if not data.get("ok"):
        print(f"ERROR: {data.get('error', 'Unknown error')}")
        return False
    
    result = data.get("result", {})
    
    # Check key fields
    print(f"\n[CHECK] Basic Info:")
    print(f"  Ticker: {result.get('ticker')}")
    print(f"  Stars: {result.get('capped_stars')}")
    print(f"  Close: ${result.get('close')}")
    print(f"  ADR: {result.get('adr')}%")
    print(f"  Recommendation: {result.get('recommendation')}")
    
    # Check trade plan
    plan = result.get("trade_plan", {})
    print(f"\n[CHECK] Trade Plan Data Types:")
    print(f"  day1_stop: {plan.get('day1_stop')} (is numeric: {isinstance(plan.get('day1_stop'), (int, float))})")
    print(f"  day2_stop: {plan.get('day2_stop')} (is numeric: {isinstance(plan.get('day2_stop'), (int, float))})")
    print(f"  day3plus_stop: {plan.get('day3plus_stop')} (is numeric: {isinstance(plan.get('day3plus_stop'), (int, float))})")
    print(f"  suggested_shares: {plan.get('suggested_shares')} (is int: {isinstance(plan.get('suggested_shares'), int)})")
    print(f"  suggested_value_usd: {plan.get('suggested_value_usd')} (is numeric: {isinstance(plan.get('suggested_value_usd'), (int, float))})")
    
    # Check position sizing extracted from result
    print(f"\n[CHECK] Position Sizing Info:")
    print(f"  position_pct_min: {result.get('position_pct_min')}")
    print(f"  position_pct_max: {result.get('position_pct_max')}")
    
    # Check dimensions
    dims = result.get("dim_scores", {})
    print(f"\n[CHECK] Dimensions:")
    for k in ['A', 'B', 'C', 'D', 'E', 'F']:
        d = dims.get(k, {})
        print(f"  {k}: score={d.get('score')} (is numeric: {isinstance(d.get('score'), (int, float))})")
    
    # Check setup type
    setup = result.get("setup_type", {})
    print(f"\n[CHECK] Setup Type:")
    print(f"  primary_type: {setup.get('primary_type')}")
    print(f"  description_zh: {setup.get('description_zh')[:50]}..." if setup.get('description_zh') else "  description_zh: MISSING")
    
    print("\n" + "=" * 70)
    if (isinstance(plan.get('day1_stop'), (int, float)) and 
        isinstance(plan.get('day2_stop'), (int, float)) and 
        isinstance(plan.get('day3plus_stop'), (int, float))):
        print("SUCCESS: All critical fields have correct data types!")
        return True
    else:
        print("FAILURE: Some fields have incorrect data types!")
        return False

if __name__ == "__main__":
    try:
        success = test_qm_api()
        exit(0 if success else 1)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
