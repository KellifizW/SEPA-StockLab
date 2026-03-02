#!/usr/bin/env python
"""Quick test - backtest just a few bars."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_backtester import _qm_stage2_check, _qm_stage3_score, _qm_measure_outcome
from modules.data_pipeline import get_enriched

print("Fetching PLTR data...")
df = get_enriched("PLTR", period="2y", use_cache=True)
print(f"Got {len(df)} bars")

# Test bars 130, 230, 330 manually
test_bars = [130, 230, 330]
signals = []

for bar_i in test_bars:
    df_slice = df.iloc[:bar_i+1].copy()
    
    # Stage 2
    stage2 = _qm_stage2_check("PLTR", df_slice, debug_mode=True)
    if stage2 is None:
        print(f"Bar {bar_i}: Stage 2 FAILED")
        continue
    
    print(f"Bar {bar_i}: Stage 2 OK (ADR={stage2['adr']}%)")
    
    # Stage 3
    star_info = _qm_stage3_score("PLTR", df_slice, stage2, debug_mode=True)
    if star_info is None:
        print(f"  Stage 3: FAILED")
        continue
    
    star_rating = star_info.get('star_rating', 0.0)
    print(f"  Stage 3: OK (⭐ {star_rating})")
    
    # Check breakout level
    window_days = 6
    recent_slice = df_slice.iloc[-window_days:]
    breakout_level = float(recent_slice["High"].max())
    sig_close = float(df_slice.iloc[-1]["Close"])
    
    print(f"  Breakout level: ${breakout_level}, Current close: ${sig_close}")
    
    if star_rating >= 3.0:
        print(f"  ✅ Would create signal!")
        signals.append({
            "bar": bar_i,
            "date": df.index[bar_i].date(),
            "close": sig_close,
            "rating": star_rating,
            "breakout": breakout_level
        })
    else:
        print(f"  ❌ Below 3.0 star threshold")

print(f"\n{'='*60}")
print(f"Total signals: {len(signals)}")
if len(signals) > 0:
    print("\nSignals:")
    for sig in signals:
        print(f"  Bar {sig['bar']} ({sig['date']}): {sig['rating']:.1f}⭐ @ ${sig['close']:.2f}")
else:
    print("No signals generated")
