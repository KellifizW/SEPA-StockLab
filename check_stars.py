#!/usr/bin/env python
"""Check actual star values from analyze_qm."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from modules.data_pipeline import get_enriched
from modules.qm_analyzer import analyze_qm
from modules.rs_ranking import get_rs_rank

# Fetch PLTR data
ticker = "PLTR"
df = get_enriched(ticker, period="2y", use_cache=True)

# Test at bar 330
bar_idx = 330
df_slice = df.iloc[:bar_idx+1].copy()

# Get RS rank
rs_rank = get_rs_rank(ticker)
print(f"RS Rank: {rs_rank}")

# Call analyze_qm
result = analyze_qm(ticker, df_slice, rs_rank=rs_rank, print_report=False)

# Print all numeric values from the result
print("\nAll numeric values in analyze_qm result:")
for key in ['stars', 'capped_stars']:
    if key in result:
        val = result[key]
        print(f"  {key}: {val} (type: {type(val).__name__})")
    else:
        print(f"  {key}: NOT FOUND")

print("\nAll keys:")
for key in sorted(result.keys()):
    val = result[key]
    if isinstance(val, (int, float)):
        print(f"  {key}: {val}")
    elif isinstance(val, str) and len(str(val)) < 50:
        print(f"  {key}: {val}")
    else:
        print(f"  {key}: <{type(val).__name__}>")
