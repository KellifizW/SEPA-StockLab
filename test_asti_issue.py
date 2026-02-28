#!/usr/bin/env python3
"""
Diagnostic test for ASTI analyze issue.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules.qm_analyzer import analyze_qm
import json

if __name__ == "__main__":
    print("=" * 70)
    print("Testing ASTI QM Analysis")
    print("=" * 70)
    
    result = analyze_qm("ASTI", print_report=False)
    
    print("\n" + "=" * 70)
    print("Raw Result Data")
    print("=" * 70)
    
    # Print specific problem areas
    print(f"\n[OK] Ticker: {result.get('ticker')}")
    print(f"[OK] Stars: {result.get('capped_stars')}")
    print(f"[OK] Close: {result.get('close')}")
    print(f"[OK] ADR: {result.get('adr')}")
    
    print("\n[CHECK] TRADE PLAN Details:")
    tp = result.get('trade_plan', {})
    print(f"  - Keys: {list(tp.keys())}")
    print(f"  - day1_stop: {tp.get('day1_stop')} (type: {type(tp.get('day1_stop')).__name__})")
    print(f"  - day2_stop: {tp.get('day2_stop')} (type: {type(tp.get('day2_stop')).__name__})")
    print(f"  - day3plus_stop: {tp.get('day3plus_stop')} (type: {type(tp.get('day3plus_stop')).__name__})")
    print(f"  - suggested_shares: {tp.get('suggested_shares')}")
    print(f"  - suggested_value_usd: {tp.get('suggested_value_usd')}")
    print(f"  - sma_10_trail: {tp.get('sma_10_trail')}")
    
    print("\n[CHECK] DIMENSIONS:")
    dims = result.get('dim_scores', {})
    for k, v in dims.items():
        print(f"  {k}: score={v.get('score')}, detail keys={list(v.get('detail',{}).keys())}")
    
    print("\n" + "=" * 70)
    print("JSON Serialization Test (what gets sent to frontend)")
    print("=" * 70)
    
    def clean_for_json(obj):
        """Clean numpy/NaN values for JSON."""
        if isinstance(obj, dict):
            return {k: clean_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [clean_for_json(v) for v in obj]
        elif isinstance(obj, float):
            if obj != obj:  # NaN check
                return None
            return obj
        return obj
    
    cleaned = clean_for_json(result)
    json_str = json.dumps(cleaned, default=str, indent=2)
    
    # Print truncated JSON
    lines = json_str.split('\n')
    for i, line in enumerate(lines[:50]):
        print(line)
    if len(lines) > 50:
        print(f"... ({len(lines) - 50} more lines)")
    
    print("\n✓ Test complete!")
