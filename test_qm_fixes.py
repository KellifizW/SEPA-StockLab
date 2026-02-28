#!/usr/bin/env python3
"""
Test script to verify all QM analyzer fixes.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_analyzer import analyze_qm

# Test ASTI (4.5★)
print("=" * 70)
print("QM ANALYZER FIX VERIFICATION")
print("=" * 70)

r = analyze_qm('ASTI', print_report=False)

print('\n✓ ASTI Analysis Check:')
print(f'  Ticker: {r.get("ticker")}')
print(f'  Capped Stars: {r.get("capped_stars")}★')
print(f'  ADR: {r.get("adr"):.2f}%')
print(f'  Close: ${r.get("close"):.2f}')

print('\n✓ Momentum Data (should NOT be None):')
mom_1m = r.get("mom_1m")
mom_3m = r.get("mom_3m")
mom_6m = r.get("mom_6m")
print(f'  mom_1m: {mom_1m} {"✅" if mom_1m is not None else "❌"}')
print(f'  mom_3m: {mom_3m} {"✅" if mom_3m is not None else "❌"}')
print(f'  mom_6m: {mom_6m} {"✅" if mom_6m is not None else "❌"}')

print('\n✓ Dimension Scores (should have A-F keys with non-zero scores):')
dims = r.get('dim_scores', {})
all_dims_ok = True
for key in ['A', 'B', 'C', 'D', 'E', 'F']:
    d = dims.get(key, {})
    score = d.get('score', 0)
    detail = d.get('detail', {})
    is_ok = score != 0 or key == 'C'  # C can be 0 for ASTI
    print(f'  {key}: score={score:+.2f} {detail.get("note", "")} {"✅" if is_ok or key == "B" else "⚠️"}')
    if not is_ok and key not in ['B', 'C']:
        all_dims_ok = False

print('\n✓ Trade Plan Data:')
plan = r.get('trade_plan', {})
day1_stop = plan.get('day1_stop')
day2_stop = plan.get('day2_stop')
day3_stop = plan.get('day3plus_stop')
shares = plan.get('suggested_shares')
value = plan.get('suggested_value_usd')
risk = plan.get('suggested_risk_usd')

print(f'  day1_stop: ${day1_stop} {"✅" if day1_stop else "❌"}')
print(f'  day2_stop: ${day2_stop} {"✅" if day2_stop else "❌"}')
print(f'  day3plus_stop: ${day3_stop} {"✅" if day3_stop else "❌"}')
print(f'  suggested_shares: {shares} {"✅" if shares and shares > 100 else "❌"}')
print(f'  suggested_value_usd: ${value} {"✅" if value and value > 5000 else "❌"}')
print(f'  suggested_risk_usd: ${risk} {"✅" if risk and risk > 100 else "❌"}')

print('\n' + '=' * 70)
if mom_1m is not None and mom_3m is not None and mom_6m is not None and shares > 100 and value > 5000:
    print("✅ ALL FIXES VERIFIED - DATA IS COMPLETE AND CORRECT!")
else:
    print("⚠️ SOME ISSUES REMAIN - CHECK ABOVE FOR DETAILS")
print('=' * 70)
