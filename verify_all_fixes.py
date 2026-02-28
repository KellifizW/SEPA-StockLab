#!/usr/bin/env python3
"""Final verification that all QM fixes are working."""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_analyzer import analyze_qm
from app import _clean

print('='*70)
print('QM ANALYZER - COMPLETE VERIFICATION')
print('='*70)

result = analyze_qm('ASTI', print_report=False)
clean = _clean(result) if result else {}

print('\nMOMENTUM DATA (1M%, 3M%, 6M%):')
print(f'  mom_1m: {clean.get("mom_1m")}')
print(f'  mom_3m: {clean.get("mom_3m")}')
print(f'  mom_6m: {clean.get("mom_6m")}')

print('\nDIMENSION SCORES (A-F):')
dims = clean.get('dim_scores', {})
for key in ['A', 'B', 'C', 'D', 'E', 'F']:
    d = dims.get(key, {})
    score = d.get('score', 'MISSING')
    detail = d.get('detail', {})
    print(f'  {key}: score={score}, has_detail={bool(detail)}')

print('\nTRADE PLAN (Stops, Targets, Position):')
plan = clean.get('trade_plan', {})
print(f'  day1_stop: {plan.get("day1_stop")}')
print(f'  day2_stop: {plan.get("day2_stop")}')
print(f'  day3plus_stop: {plan.get("day3plus_stop")}')
print(f'  profit_target_px: {plan.get("profit_target_px")}')
print(f'  suggested_shares: {plan.get("suggested_shares")}')
print(f'  suggested_value_usd: {plan.get("suggested_value_usd")}')
print(f'  suggested_risk_usd: {plan.get("suggested_risk_usd")}')

print('\nSTAR RATING:')
print(f'  capped_stars: {clean.get("capped_stars")} (should be 4.5)')
print(f'  recommendation: {clean.get("recommendation")}')
print(f'  recommendation_zh: {clean.get("recommendation_zh")}')

print('\nJSON SERIALIZATION TEST:')
try:
    json_str = json.dumps(clean)
    print(f'  ✅ SUCCESS - {len(json_str)} chars')
except Exception as e:
    print(f'  ❌ FAILED: {e}')

print('\n' + '='*70)
mom_ok = clean.get('mom_1m') is not None
dims_ok = len(dims) == 6
plan_ok = plan.get('day1_stop') and plan.get('suggested_shares')
stars_ok = clean.get('capped_stars') == 4.5

if mom_ok and dims_ok and plan_ok and stars_ok:
    print('✅ ALL FIXES VERIFIED - READY FOR PRODUCTION!')
    print('   - Momentum data: ✅')
    print('   - Dimension scores: ✅')
    print('   - Trade plan: ✅')
    print('   - Star rating: ✅')
else:
    print('⚠️ SOME ISSUES DETECTED:')
    if not mom_ok:
        print('   - Momentum data missing')
    if not dims_ok:
        print(f'   - Dimension scores incomplete ({len(dims)}/6)')
    if not plan_ok:
        print('   - Trade plan incomplete')
    if not stars_ok:
        print('   - Star rating mismatch')
print('='*70)
