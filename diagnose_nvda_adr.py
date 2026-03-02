#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic: Check NVDA ADR distribution over 2 years
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.data_pipeline import get_enriched, get_adr
import numpy as np

def diagnose_adr():
    print("=" * 80)
    print("NVDA ADR DISTRIBUTION ANALYSIS")
    print("=" * 80)
    
    try:
        df = get_enriched("NVDA", period="2y")
    except Exception as e:
        print(f"[ERROR] Failed to get NVDA data: {e}")
        return
    
    print(f"\n[Data] Total bars: {len(df)}")
    print(f"[Data] Date range: {df.index[0]} to {df.index[-1]}")
    
    # Calculate ADR for every bar
    adrs = []
    for i in range(14, len(df)):  # Need at least 14 bars for ADR
        df_slice = df.iloc[:i+1]
        adr_val = get_adr(df_slice)
        adrs.append(adr_val)
    
    adrs = np.array(adrs)
    
    print(f"\n[ADR Statistics]")
    print(f"  Min:    {adrs.min():.2f}%")
    print(f"  Max:    {adrs.max():.2f}%")
    print(f"  Mean:   {adrs.mean():.2f}%")
    print(f"  Median: {np.median(adrs):.2f}%")
    print(f"  Std:    {np.std(adrs):.2f}%")
    
    # Count bars passing different ADR thresholds
    pct_above_4 = (adrs >= 4.0).sum() / len(adrs) * 100
    pct_above_5 = (adrs >= 5.0).sum() / len(adrs) * 100
    pct_above_8 = (adrs >= 8.0).sum() / len(adrs) * 100
    
    print(f"\n[ADR Threshold Compliance]")
    print(f"  >= 4.0%: {pct_above_4:.1f}% of bars ({(adrs >= 4.0).sum()} bars)")
    print(f"  >= 5.0%: {pct_above_5:.1f}% of bars ({(adrs >= 5.0).sum()} bars)")
    print(f"  >= 8.0%: {pct_above_8:.1f}% of bars ({(adrs >= 8.0).sum()} bars)")
    
    # When did ADR drop below 5%?
    below_5 = np.where(adrs < 5.0)[0]
    if len(below_5) > 0:
        print(f"\n[ADR < 5% Events]")
        print(f"  Total occurrences: {len(below_5)} ({len(below_5)/len(adrs)*100:.1f}%)")
        print(f"  First occurrence: bar {below_5[0]} ({df.index[below_5[0]+14]})")
        print(f"  Last occurrence: bar {below_5[-1]} ({df.index[below_5[-1]+14]})")
        
        # Count consecutive days below 5%
        if len(below_5) > 0:
            gaps = np.diff(below_5)
            max_consecutive = np.max(np.c_[gaps==1]) if len(gaps) > 0 else 0
            print(f"  Max consecutive days below 5%: ~{max_consecutive}")
    
    # Sample some bars with low ADR
    low_adr_idx = np.argsort(adrs)[:5]
    print(f"\n[Five Lowest ADR Bars]")
    for idx in low_adr_idx:
        bar_idx = idx + 14
        date = df.index[bar_idx]
        adr_val = adrs[idx]
        close = df["Close"].iloc[bar_idx]
        print(f"  {date}: ADR={adr_val:.2f}% Close=${close:.2f}")
    
    # Sample some bars with high ADR
    high_adr_idx = np.argsort(adrs)[-5:]
    print(f"\n[Five Highest ADR Bars]")
    for idx in high_adr_idx[::-1]:
        bar_idx = idx + 14
        date = df.index[bar_idx]
        adr_val = adrs[idx]
        close = df["Close"].iloc[bar_idx]
        print(f"  {date}: ADR={adr_val:.2f}% Close=${close:.2f}")

if __name__ == "__main__":
    diagnose_adr()
