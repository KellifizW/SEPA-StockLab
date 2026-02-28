#!/usr/bin/env python
"""
Verify that top-ranked stocks satisfy the AND logic (both near_high AND near_low).
"""
import sys
sys.path.insert(0, '.')

import json
from pathlib import Path

# Read the most recent scan results
scan_file = Path('data/qm_last_scan.json')
if scan_file.exists():
    data = json.loads(scan_file.read_text(encoding='utf-8'))
    rows = data.get('rows', [])
    
    print('Verifying top 10 passed stocks satisfy AND logic:')
    print('=' * 80)
    print(f'{"Rank":<5} {"Ticker":<8} {"★":<4} {"High%":<8} {"Low%":<8} {"Result":<10}')
    print('-' * 80)
    
    for i, row in enumerate(rows[:10], 1):
        ticker = row['ticker']
        star = row['qm_star']
        high_pct = row.get('pct_from_6d_high', 0)
        low_pct = row.get('pct_from_6d_low', 0)
        near_high = high_pct <= 15.0
        near_low = low_pct <= 15.0
        passes = near_high and near_low
        
        result = '✅ PASS' if passes else '❌ FAIL'
        print(f'{i:<5} {ticker:<8} {star:<4.1f} {high_pct:<8.1f} {low_pct:<8.1f} {result:<10}')
    
    print()
    print('Summary:')
    all_pass = all(
        (row.get('pct_from_6d_high', 0) <= 15.0) and 
        (row.get('pct_from_6d_low', 0) <= 15.0)
        for row in rows[:10]
    )
    
    if all_pass:
        print('✅ All top 10 stocks satisfy AND logic (6-day consolidation)')
    else:
        print('❌ Some stocks do not satisfy AND logic')
        
else:
    print('No scan results found')
