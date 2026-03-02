#!/usr/bin/env python
"""Test Stage 3 with the fixed code."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import trader_config as C
from modules.data_pipeline import get_enriched
from modules.qm_backtester import _qm_stage2_check, _qm_stage3_score

# Fetch PLTR data
ticker = "PLTR"
print(f"Fetching {ticker} data...")
df = get_enriched(ticker, period="2y", use_cache=True)

if df is None or len(df) < 130:
    print(f"ERROR: Not enough data")
    sys.exit(1)

# Test at bar 330
bar_idx = 330
df_slice = df.iloc[:bar_idx+1].copy()
print(f"Testing at bar {bar_idx} ({df.index[bar_idx].date()}), {len(df_slice)} bars")

# Stage 2
print("\n=== Stage 2 ===")
stage2 = _qm_stage2_check(ticker, df_slice, debug_mode=True)
if stage2 is None:
    print("❌ FAILED")
    sys.exit(1)
print(f"✅ PASSED: ADR={stage2.get('adr')}%, DV=${stage2.get('dollar_volume_m')}M")

# Stage 3  
print("\n=== Stage 3 ===")
star_info = _qm_stage3_score(ticker, df_slice, stage2, debug_mode=True)
if star_info is None:
    print("❌ Stage 3 returned None")
else:
    print(f"✅ Stage 3 Success:")
    print(f"   Star Rating: {star_info.get('star_rating')}")
    print(f"   Setup Type: {star_info.get('setup_type')}")
    
    # Check if it passes the 3.0 star threshold
    star_rating = star_info.get('star_rating', 0.0)
    if star_rating >= 3.0:
        print(f"   ✅ PASSES 3.0 star threshold!")
    else:
        print(f"   ❌ FAILS 3.0 star threshold (only {star_rating} stars)")

print("\nDone!")
