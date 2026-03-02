#!/usr/bin/env python
"""Diagnostic for analyze_qm function."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import trader_config as C
from modules.data_pipeline import get_enriched
from modules.qm_analyzer import analyze_qm
from modules.rs_ranking import get_rs_rank

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
print(f"Testing at bar {bar_idx} ({df.index[bar_idx].date()})")

# Get RS rank
try:
    rs_rank = get_rs_rank(ticker)
    print(f"RS Rank: {rs_rank}")
except Exception as e:
    print(f"RS Rank error: {e}")
    rs_rank = 50.0

# Call analyze_qm
print(f"\nCalling analyze_qm('{ticker}', df_slice, rs_rank={rs_rank}, print_report=False)...")
try:
    result = analyze_qm(ticker, df_slice, rs_rank=rs_rank, print_report=False)
    if result is None:
        print("Result: None")
    else:
        print(f"Result type: {type(result)}")
        print(f"Result keys: {list(result.keys())}")
        for key in ['star_rating', 'setup_type', 'all_types']:
            if key in result:
                print(f"  {key}: {result[key]}")
            else:
                print(f"  {key}: NOT IN RESULT")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\nDone!")
