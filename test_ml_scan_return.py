#!/usr/bin/env python3
"""Quick test to verify ml_screener.run_ml_scan returns expected tuple."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import trader_config as C
from modules.ml_screener import run_ml_scan

print("="*70)
print("Testing ML Scan Return Type")
print("="*70)

try:
    print("\n[1] Running ml_scan with stage1_source='nasdaq_ftp'...")
    result = run_ml_scan(
        min_star=3.0, 
        top_n=10,
        stage1_source='nasdaq_ftp',
        scanner_mode='standard',
        verbose=False
    )
    
    print(f"\n[2] Result type: {type(result).__name__}")
    print(f"    Result: {result[:100] if isinstance(result, (list, tuple)) else str(result)[:100]}...")
    
    if isinstance(result, tuple):
        print(f"\n[3] Tuple length: {len(result)}")
        for i, item in enumerate(result):
            print(f"    [{i}] type={type(item).__name__}, is_df={isinstance(item, pd.DataFrame)}")
            if isinstance(item, pd.DataFrame):
                print(f"        shape={item.shape}, empty={item.empty}")
                if not item.empty:
                    print(f"        columns: {list(item.columns)[:5]}")
    else:
        print(f"\n[3] Not a tuple! Actual type: {type(result).__name__}")
    
    print("\n✓ Test completed successfully")
    
except Exception as e:
    print(f"\n✗ ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

