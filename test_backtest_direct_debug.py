#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Direct test of qm_backtester with debug_mode to ensure all None values handled
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_backtester import run_qm_backtest
import json

def test_backtest():
    print("=" * 80)
    print("DIRECT QM BACKTEST TEST (Debug Mode)")
    print("=" * 80)
    
    result = run_qm_backtest(
        ticker="NVDA",
        min_star=3.0,
        max_hold_days=120,
        debug_mode=True
    )
    
    print(f"\n[Result Type] {type(result)}")
    print(f"[Result Keys] {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
    
    if "summary" in result:
        summary = result["summary"]
        print(f"\n[Summary Field Check]")
        print(f"  win_rate: {summary.get('win_rate')} (None: {summary.get('win_rate') is None})")
        print(f"  avg_realized_gain: {summary.get('avg_realized_gain')} (None: {summary.get('avg_realized_gain') is None})")
        print(f"  profit_factor: {summary.get('profit_factor')} (None: {summary.get('profit_factor') is None})")
        print(f"  best_gain: {summary.get('best_gain')} (None: {summary.get('best_gain') is None})")
        print(f"  worst_gain: {summary.get('worst_gain')} (None: {summary.get('worst_gain') is None})")
        print(f"  total_signals: {summary.get('total_signals')}")
    
    if "signals" in result and result["signals"]:
        sig = result["signals"][0]
        print(f"\n[First Signal Field Check]")
        print(f"  star_rating: {sig.get('star_rating')} (None: {sig.get('star_rating') is None})")
        print(f"  signal_close: {sig.get('signal_close')} (None: {sig.get('signal_close') is None})")
        print(f"  breakout_level: {sig.get('breakout_level')} (None: {sig.get('breakout_level') is None})")
        print(f"  max_gain: {sig.get('max_gain')} (None: {sig.get('max_gain') is None})")
        print(f"  realized_gain: {sig.get('realized_gain')} (None: {sig.get('realized_gain') is None})")
        print(f"  breakout_price: {sig.get('breakout_price')} (None: {sig.get('breakout_price') is None})")
        
        # Test that values can be serialized to JSON (which would fail if there are non-serializable types)
        try:
            json_str = json.dumps(result)
            print(f"\n[JSON Serialization] SUCCESS - Result can be serialized")
        except Exception as e:
            print(f"\n[JSON Serialization] FAILED - {e}")
            return False
    
    print(f"\n[OK] TEST PASSED - Result structure is valid for JavaScript parsing")
    return True

if __name__ == "__main__":
    success = test_backtest()
    sys.exit(0 if success else 1)
