#!/usr/bin/env python
"""
Test: Verify that ASTI is now filtered out with the AND logic fix.
"""
import sys
sys.path.insert(0, '.')

from modules.data_pipeline import get_enriched, get_6day_range_proximity
import trader_config as C

ticker = 'ASTI'
df = get_enriched(ticker, period='1y')

if not df.empty:
    rng = get_6day_range_proximity(df)
    near_high = rng.get('near_high', False)
    near_low = rng.get('near_low', False)
    pct_high = rng.get('pct_from_high')
    pct_low = rng.get('pct_from_low')
    
    print(f'Test: {ticker}')
    print(f'Distance from 6d high: {pct_high:.1f}% (limit: 15%)')
    print(f'Distance from 6d low: {pct_low:.1f}% (limit: 15%)')
    print()
    print(f'near_high = {near_high}')
    print(f'near_low = {near_low}')
    print()
    result = near_high and near_low
    print(f'With AND logic: near_high AND near_low = {result}')
    print()
    if result:
        print(f'PASS: {ticker} passes filter')
    else:
        print(f'VETO: {ticker} filtered out')
        if not near_high:
            print(f'  Reason: distance from high {pct_high:.1f}% exceeds 15%')
        if not near_low:
            print(f'  Reason: distance from low {pct_low:.1f}% exceeds 15%')
else:
    print(f"No data for {ticker}")
