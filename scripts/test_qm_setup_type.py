#!/usr/bin/env python3
"""
Quick test script to verify QM scan results include setup_type field.
This tests the fix for issue: "Setup Type 欄位都無顯示"
"""

import sys
import json
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules.qm_screener import run_qm_scan

def test_setup_type_in_results():
    """Run quick QM scan and check if setup_type is populated."""
    
    print("\n" + "="*70)
    print("TEST: QM Scan Results Include setup_type Field")
    print("="*70)
    
    # Run a quick QM scan with small parameters
    print("\n📊 Running QM scan (this may take 30-60 seconds)...")
    print(f"   Minimum Star: {C.QM_SCAN_MIN_STAR}")
    print(f"   Max Results: 20 stocks (top-n)")
    
    try:
        results = run_qm_scan(
            min_star=4.0,
            top_n=20,
            verbose=False
        )
        
        if results is None or len(results) == 0:
            print("\n❌ FAIL: QM scan returned no results")
            return False
        
        print(f"\n✅ Scan completed with {len(results)} results")
        
        # Check first result
        first = results[0]
        
        print("\n" + "-"*70)
        print("SAMPLE RESULT (First Stock):")
        print("-"*70)
        
        # Show key fields
        sample_fields = [
            'ticker', 'qm_star', 'setup_type', 'setup_code',
            'adr', 'mom_1m', 'is_tight', 'has_higher_lows'
        ]
        
        for field in sample_fields:
            value = first.get(field, "⚠️ MISSING")
            print(f"  {field:20s}: {value}")
        
        # Check if setup_type field exists and is populated
        print("\n" + "-"*70)
        print("VERDICT:")
        print("-"*70)
        
        setup_type_present = 'setup_type' in first
        setup_type_value = first.get('setup_type', '')
        setup_type_populated = bool(setup_type_value and setup_type_value.strip())
        
        if not setup_type_present:
            print("❌ FAIL: 'setup_type' field MISSING from result")
            return False
        
        if not setup_type_populated:
            print(f"⚠️  WARNING: 'setup_type' field exists but EMPTY: '{setup_type_value}'")
        else:
            print(f"✅ PASS: 'setup_type' field POPULATED: '{setup_type_value}'")
        
        # Count populated setup_types
        populated_count = sum(
            1 for r in results 
            if r.get('setup_type', '').strip()
        )
        
        print(f"\n📈 Statistics:")
        print(f"   Total results: {len(results)}")
        print(f"   setup_type populated: {populated_count}/{len(results)}")
        print(f"   Coverage: {100*populated_count/len(results):.1f}%")
        
        # Show all results in table format
        print("\n" + "-"*70)
        print("ALL RESULTS:")
        print("-"*70)
        
        df_display = results[[
            'ticker', 'qm_star', 'setup_type', 'adr', 'mom_1m', 'is_tight'
        ]].copy()
        
        print(df_display.to_string(index=False))
        
        # Final verdict
        print("\n" + "="*70)
        if populated_count > 0:
            print("✅ TEST PASSED: setup_type field is working")
            print("="*70)
            return True
        else:
            print("❌ TEST FAILED: setup_type field is empty for all results")
            print("="*70)
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR during scan: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_setup_type_in_results()
    sys.exit(0 if success else 1)
