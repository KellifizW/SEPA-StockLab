#!/usr/bin/env python
"""Quick diagnostic script to test QM backtest pipeline for PLTR."""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance as yf
import trader_config as C
from modules.data_pipeline import (
    get_adr, get_dollar_volume, get_momentum_returns, get_6day_range_proximity
)
from modules.qm_backtester import _qm_stage2_check, _qm_stage3_score

# Fetch PLTR data
ticker = "PLTR"
print(f"Fetching {ticker} data...")
df = yf.download(ticker, period="2y", progress=False)

if df is None or len(df) < 130:
    print(f"ERROR: Not enough data for {ticker}")
    sys.exit(1)

print(f"Got {len(df)} bars")

# Test at a specific bar (e.g., bar 330, which passed Stage 2)
bar_idx = 330
if bar_idx >= len(df):
    print(f"ERROR: bar {bar_idx} exceeds data length {len(df)}")
    sys.exit(1)

df_slice = df.iloc[:bar_idx+1].copy()
print(f"\nTesting at bar {bar_idx} ({df.index[bar_idx].date()}), slice has {len(df_slice)} bars")

# Test Stage 2 — manually step through each check
print("\n=== DETAILED STAGE 2 CHECKS ===")
if len(df_slice) < 30:
    print("❌ Not enough data (< 30 bars)")
else:
    print("✅ Have enough data (≥ 30 bars)")
    
    # ADR
    try:
        print(f"\nCalling get_adr({ticker}, df_slice)...")
        adr_result = get_adr(df_slice)
        print(f"   Type of result: {type(adr_result)}")
        print(f"   Value: {adr_result}")
        adr = float(adr_result)  # Explicit conversion
        min_adr = 1.0  # debug mode
        print(f"✅ ADR: {adr:.2f}% (min: {min_adr}%)")
        if adr < min_adr:
            print(f"   ❌ ADR too low")
    except Exception as e:
        print(f"❌ ADR calc error: {e}")
        traceback.print_exc()
    
    # Dollar Volume
    try:
        print(f"\nCalling get_dollar_volume({ticker}, df_slice)...")
        dv_result = get_dollar_volume(df_slice)
        print(f"   Type of result: {type(dv_result)}")
        print(f"   Value: {dv_result}")
        dv = float(dv_result)  # Explicit conversion
        min_dv = 100_000  # debug mode
        dv_m = dv / 1_000_000
        print(f"✅ Dollar Volume: ${dv_m:.2f}M (min: ${min_dv/1_000_000:.2f}M)")
        if dv < min_dv:
            print(f"   ❌ DV too low")
    except Exception as e:
        print(f"❌ DV calc error: {e}")
        traceback.print_exc()
    
    # Momentum
    try:
        print(f"\nCalling get_momentum_returns(df_slice)...")
        mom_result = get_momentum_returns(df_slice)
        print(f"   Type of result: {type(mom_result)}")
        print(f"   Value: {mom_result}")
        m1 = mom_result.get("1m", None)
        m3 = mom_result.get("3m", None)
        m6 = mom_result.get("6m", None)
        print(f"✅ Momentum: 1M={m1}, 3M={m3}, 6M={m6}")
        passes_1m = m1 is not None and m1 >= 0
        passes_3m = m3 is not None and m3 >= 0
        passes_6m = m6 is not None and m6 >= 0
        print(f"   1M pass: {passes_1m}, 3M pass: {passes_3m}, 6M pass: {passes_6m}")
        if not (passes_1m or passes_3m or passes_6m):
            print(f"   ❌ No momentum pass")
    except Exception as e:
        print(f"❌ Momentum calc error: {e}")
        traceback.print_exc()
    
    # Range proximity
    try:
        print(f"\nCalling get_6day_range_proximity(df_slice)...")
        rng_result = get_6day_range_proximity(df_slice)
        print(f"   Type of result: {type(rng_result)}")
        print(f"   Value: {rng_result}")
    except Exception as e:
        print(f"❌ Range proximity calc error: {e}")
        traceback.print_exc()

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
    print("⚠️  Stage 2 failed, cannot test Stage 3")
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
