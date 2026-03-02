#!/usr/bin/env python
"""Quick diagnostic script to test QM backtest pipeline for PLTR."""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import trader_config as C
from modules.data_pipeline import get_enriched
from modules.qm_backtester import _qm_stage2_check, _qm_stage3_score

# Fetch PLTR data using get_enriched (like the actual backtest does)
ticker = "PLTR"
print(f"Fetching {ticker} data via get_enriched (same as backtest)...")
df = get_enriched(ticker, period="2y", use_cache=True)

if df is None or len(df) < 130:
    print(f"ERROR: Not enough data for {ticker}")
    sys.exit(1)

print(f"Got {len(df)} bars")
print(f"Columns: {df.columns.tolist()}")
print(f"Columns type: {type(df.columns)}")

# Test at a specific bar (e.g., bar 330, which passed Stage 2)
bar_idx = 330
if bar_idx >= len(df):
    print(f"ERROR: bar {bar_idx} exceeds data length {len(df)}")
    sys.exit(1)

df_slice = df.iloc[:bar_idx+1].copy()
print(f"\nTesting at bar {bar_idx} ({df.index[bar_idx].date()}), slice has {len(df_slice)} bars")

# Now test using the function
print("\n=== _qm_stage2_check() RESULT ===")
try:
    stage2 = _qm_stage2_check(ticker, df_slice, debug_mode=True)
    if stage2 is None:
        print("❌ Stage 2 FAILED")
    else:
        print(f"✅ Stage 2 PASSED")
        print(f"   ADR: {stage2.get('adr')}%")
        print(f"   DV: ${stage2.get('dollar_volume_m')}M")
        print(f"   Mom 1M/3M/6M: {stage2.get('mom_1m')}/{stage2.get('mom_3m')}/{stage2.get('mom_6m')}")
except Exception as e:
    print(f"ERROR in _qm_stage2_check: {e}")
    traceback.print_exc()

# Test Stage 3
print("\n=== STAGE 3 ===")
if stage2 is None:
    print("⚠️ Stage 2 failed, cannot test Stage 3")
else:
    try:
        star_info = _qm_stage3_score(ticker, df_slice, stage2, debug_mode=True)
        if star_info is None:
            print("❌ Stage 3 returned NONE")
        else:
            print(f"✅ Stage 3 returned: {star_info}")
    except Exception as e:
        print(f"ERROR in _qm_stage3_score: {e}")
        traceback.print_exc()

print("\nDone!")
