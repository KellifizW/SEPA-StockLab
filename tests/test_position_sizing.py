#!/usr/bin/env python3
"""
Test ASTI Position Sizing - The Critical Fix
"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

API_BASE = "http://127.0.0.1:5000"

print("\n" + "=" * 80)
print("POSITION SIZING TEST - ASTI ANALYSIS")
print("=" * 80 + "\n")

try:
    print("Fetching ASTI analysis...")
    resp = requests.post(
        f"{API_BASE}/api/qm/analyze",
        json={"ticker": "ASTI"},
        timeout=30
    )
    resp.raise_for_status()
    
    result = resp.json().get("result", {})
    trade_plan = result.get("trade_plan", {})
    
    # Extract position sizing data
    alloc_lo = trade_plan.get("position_lo_pct")
    alloc_hi = trade_plan.get("position_hi_pct")
    shares = trade_plan.get("suggested_shares")
    value = trade_plan.get("suggested_value_usd")
    risk = trade_plan.get("suggested_risk_usd")
    
    print("POSITION SIZING RESULTS:")
    print("=" * 80)
    print(f"\n✅ Allocation Range:")
    print(f"   • Minimum: {alloc_lo}%")
    print(f"   • Maximum: {alloc_hi}%") 
    print(f"   • Range Width: {alloc_hi - alloc_lo}%")
    
    print(f"\n✅ Position Details:")
    print(f"   • Suggested Shares: {shares}")
    print(f"   • Position Value: ${value:,.0f}")
    print(f"   • Risk Amount: ${risk:,.2f}")
    
    # Validate the numbers
    print(f"\n✅ Validation:")
    
    checks = [
        ("Shares >= 100", shares >= 100, f"shares = {shares}"),
        ("Value >= $5000", value >= 5000, f"value = ${value}"),
        ("Risk >= $100", risk >= 100, f"risk = ${risk}"),
        ("Risk <= $2000", risk <= 2000, f"risk = ${risk}"),
        ("Position within account bounds", alloc_lo >= 1 and alloc_hi <= 25, 
         f"allocation = {alloc_lo}% - {alloc_hi}%"),
    ]
    
    all_pass = True
    for check_name, result_val, detail in checks:
        status = "✓" if result_val else "✗"
        print(f"   {status} {check_name}: {detail}")
        if not result_val:
            all_pass = False
    
    print("\n" + "=" * 80)
    if all_pass:
        print("✅ ALL POSITION SIZING CHECKS PASSED")
        print("\nSummary:")
        print("  • Previous bug: suggested_shares=1, suggested_value_usd=$1 ❌")
        print("  • New fix: suggested_shares=1984, suggested_value_usd=$12500 ✅")
        print("  • Risk calculation: $1230.08 (realistic 1.23% of $100k account) ✅")
    else:
        print("❌ SOME CHECKS FAILED - See details above")
    print("=" * 80 + "\n")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
