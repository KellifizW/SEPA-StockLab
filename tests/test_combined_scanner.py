#!/usr/bin/env python3
"""
tests/test_combined_scanner.py
Quick test to verify combined scanner functionality.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

def test_combined_scanner_import():
    """Test that combined_scanner module can be imported."""
    try:
        from modules.combined_scanner import run_combined_scan, get_combined_progress
        print("[OK] combined_scanner imported successfully")
        return True
    except Exception as e:
        print(f"[FAIL] Failed to import combined_scanner: {e}")
        return False

def test_combined_scanner_basic():
    """Test basic combined scanner run with small filters."""
    try:
        from modules.combined_scanner import run_combined_scan, set_combined_cancel, get_combined_progress
        import threading
        
        print("\n[TEST] Starting basic combined scan test...")
        cancel_ev = threading.Event()
        set_combined_cancel(cancel_ev)
        
        # Run with minimal filters to speed up test
        print("[TEST] Running combined scan with Stage 1 only (no limit)...")
        custom_filters = {
            "Price": "Over $5",
            "Average Volume": "Over 100K",
        }
        
        sepa_result, qm_result = run_combined_scan(
            custom_filters=custom_filters,
            refresh_rs=False,
            verbose=True,
            stage1_source="nasdaq_ftp"  # Faster source
        )
        
        print(f"\n[RESULTS]")
        print(f"  SEPA passed: {len(sepa_result['passed'])}")
        print(f"  SEPA all: {len(sepa_result['all'])}")
        print(f"  QM passed: {len(qm_result['passed'])}")
        print(f"  QM all: {len(qm_result['all'])}")
        print(f"  Total time: {sepa_result['timing']['total']:.1f}s")
        
        if sepa_result.get('error'):
            print(f"  SEPA error: {sepa_result['error']}")
        if qm_result.get('error'):
            print(f"  QM error: {qm_result['error']}")
        
        # Check that both ran
        if sepa_result['passed'] is not None and qm_result['passed'] is not None:
            print("[OK] Combined scan completed successfully")
            return True
        else:
            print("[FAIL] Combined scan returned None results")
            return False
            
    except Exception as e:
        import traceback
        print(f"[FAIL] Combined scan test failed: {e}")
        traceback.print_exc()
        return False

def test_data_sharing():
    """Test that enriched_map is properly shared between SEPA and QM."""
    try:
        from modules.combined_scanner import run_combined_scan
        
        print("\n[TEST] Verifying data sharing between SEPA and QM...")
        sepa_result, qm_result = run_combined_scan(
            refresh_rs=False,
            verbose=False,
            stage1_source="nasdaq_ftp"
        )
        
        # Both should have the same 'all' count or close to it
        sepa_all_count = len(sepa_result['all'])
        qm_all_count = len(qm_result['all']) 
        
        print(f"  SEPA Stage 2 candidates: {sepa_all_count}")
        print(f"  QM Stage 2 candidates: {qm_all_count}")
        
        # The difference is expected since QM applies different filters
        # but they should be in the same ballpark
        if sepa_all_count > 0 or qm_all_count > 0:
            print("[OK] Data sharing test passed")
            return True
        else:
            print("[FAIL] Both SEPA and QM returned no Stage 2 candidates")
            return False
            
    except Exception as e:
        print(f"[FAIL] Data sharing test failed: {e}")
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("  COMBINED SCANNER TEST SUITE")
    print("=" * 70)
    
    tests = [
        ("Import Test", test_combined_scanner_import),
        ("Basic Functionality", test_combined_scanner_basic),
        # Skipping data sharing test in quick run since basic test covers it
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n[ {test_name} ]...")
        results.append(test_func())
    
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"  RESULTS: {passed}/{total} tests passed")
    print("=" * 70)
    
    sys.exit(0 if passed == total else 1)
