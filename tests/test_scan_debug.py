#!/usr/bin/env python3
"""
Debug test - Check if display_stars/capped_stars are being calculated and returned
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from modules.qm_screener import run_qm_scan

print("=" * 80)
print("DEBUG: Running QM Scan directly (not via API)")
print("=" * 80)
print()

# Run the scan directly
print("Calling run_qm_scan()...")
df_passed, df_all = run_qm_scan(min_star=3.0, top_n=50)

print(f"\nResults:")
print(f"  df_passed shape: {df_passed.shape}")
print(f"  df_all shape: {df_all.shape}")
print()

if not df_passed.empty:
    print("Columns in df_passed:")
    for col in df_passed.columns:
        print(f"  • {col}")
    print()
    
    # Check for ASTI
    if "ASTI" in df_passed["ticker"].values:
        asti_row = df_passed[df_passed["ticker"] == "ASTI"].iloc[0]
        print("ASTI Row Data:")
        print(f"  qm_star: {asti_row.get('qm_star')}")
        print(f"  display_stars: {asti_row.get('display_stars') if 'display_stars' in asti_row else 'NOT FOUND'}")
        print(f"  capped_stars: {asti_row.get('capped_stars') if 'capped_stars' in asti_row else 'NOT FOUND'}")
        print()
        
        # Try to convert to dict and see what happens
        asti_dict = asti_row.to_dict()
        print("ASTI as dict:")
        print(f"  qm_star: {asti_dict.get('qm_star')}")
        print(f"  display_stars: {asti_dict.get('display_stars')}")
        print(f"  capped_stars: {asti_dict.get('capped_stars')}")
    else:
        print("ASTI not in scan results")
        print(f"Top stocks: {df_passed['ticker'].head(10).tolist()}")
