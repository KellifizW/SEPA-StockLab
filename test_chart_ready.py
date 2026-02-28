#!/usr/bin/env python3
"""Test QM chart data availability."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import json

print("=" * 70)
print("QM CHART DATA AVAILABILITY TEST")
print("=" * 70)

# Check that /api/chart/enriched endpoint code exists
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()
    has_chart = '/api/chart/enriched' in content
    print(f'\n✅ Flask endpoint /api/chart/enriched defined: {has_chart}')

# Test the QM analyzer returns trade plan data
from modules.qm_analyzer import analyze_qm

result = analyze_qm('ASTI', print_report=False)
plan = result.get('trade_plan', {})

print('\n✓ Trade Plan Fields (for chart price lines):')
print(f'  close: {result.get("close")} (entry line)')
print(f'  day1_stop: {plan.get("day1_stop")} (Day 1 stop)')
print(f'  day3plus_stop: {plan.get("day3plus_stop")} (Day 3+ trail)')
print(f'  profit_target_px: {plan.get("profit_target_px")} (profit target)')

all_have_values = all([
    result.get('close'),
    plan.get('day1_stop'),
    plan.get('day3plus_stop'),
    plan.get('profit_target_px'),
])

print('\n' + '=' * 70)
if has_chart and all_have_values:
    print('✅ CHART DATA INFRASTRUCTURE READY!')
    print('   All required data present for chart generation')
    print('   Next: Start Flask server and test in browser')
else:
    print('⚠️ ISSUES DETECTED:')
    if not has_chart:
        print('   - Chart endpoint not found')
    if not all_have_values:
        print('   - Trade plan data incomplete')
print('=' * 70)
