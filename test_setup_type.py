#!/usr/bin/env python3
"""Quick test of setup_type simplification"""
import sys
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_backtester import run_qm_backtest

print("Testing QM backtest setup_type simplification...\n")
result = run_qm_backtest(ticker='NVDA', debug_mode=False, min_star=3.0, max_hold_days=120)

if not result:
    print("ERROR: Backtest returned None")
    sys.exit(1)

signals = result.get('signals', [])
print(f"Total signals: {len(signals)}")

if signals:
    sig = signals[0]
    setup_type = sig.get('setup_type')
    setup_code = sig.get('setup_type_code')
    
    print(f"\nFirst signal structure:")
    print(f"  setup_type:      {repr(setup_type)} (type: {type(setup_type).__name__})")
    print(f"  setup_type_code: {repr(setup_code)} (type: {type(setup_code).__name__})")
    print(f"  star_rating:     {sig.get('star_rating')}")
    print(f"  signal_date:     {sig.get('signal_date')}")
    
    if isinstance(setup_type, dict):
        print(f"\n  ❌ ERROR: setup_type is still a dict!")
        print(f"     Full dict: {json.dumps(setup_type, indent=2, ensure_ascii=False, default=str)}")
    else:
        print(f"\n  ✅ SUCCESS: setup_type is a clean string!")
    
    print(f"\nFirst 3 signals:")
    for i, s in enumerate(signals[:3]):
        print(f"  [{i+1}] {s['signal_date']} | Setup={s['setup_type']} | Stars={s['star_rating']}")
else:
    print("\n⚠️  No signals generated. This might indicate an issue with gates or config.")
    print("Check logs for ADR/momentum gate details.")
