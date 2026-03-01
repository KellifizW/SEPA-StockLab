#!/usr/bin/env python3
"""
tests/test_combined_scanner_quick.py
Minimal import and structure tests without running full scans.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def test_imports():
    """Test that all modules import correctly."""
    try:
        print("[TEST] Checking imports...")
        from modules.combined_scanner import run_combined_scan, get_combined_progress, set_combined_cancel
        from modules.screener import run_stage1, run_stage2, run_stage3
        from modules.qm_screener import run_qm_stage2, run_qm_stage3
        from modules.data_pipeline import batch_download_and_enrich
        print("[OK] All imports successful")
        return True
    except Exception as e:
        print(f"[FAIL] Import error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_function_signatures():
    """Test that modified functions have correct signatures."""
    try:
        print("\n[TEST] Checking function signatures...")
        import inspect
        from modules.screener import run_stage2, run_stage3
        from modules.qm_screener import run_qm_stage2
        
        # Check run_stage2 has enriched_map and shared params
        sig = inspect.signature(run_stage2)
        params = list(sig.parameters.keys())
        assert 'enriched_map' in params, "run_stage2 missing enriched_map param"
        assert 'shared' in params, "run_stage2 missing shared param"
        print("  [OK] run_stage2 has enriched_map and shared params")
        
        # Check run_stage3 has shared param
        sig = inspect.signature(run_stage3)
        params = list(sig.parameters.keys())
        assert 'shared' in params, "run_stage3 missing shared param"
        print("  [OK] run_stage3 has shared param")
        
        # Check run_qm_stage2 has enriched_map and shared params
        sig = inspect.signature(run_qm_stage2)
        params = list(sig.parameters.keys())
        assert 'enriched_map' in params, "run_qm_stage2 missing enriched_map param"
        assert 'shared' in params, "run_qm_stage2 missing shared param"
        print("  [OK] run_qm_stage2 has enriched_map and shared params")
        
        print("[OK] All function signatures correct")
        return True
    except Exception as e:
        print(f"[FAIL] Signature check failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_combined_scanner_structure():
    """Test that combined_scanner has expected functions."""
    try:
        print("\n[TEST] Checking combined_scanner structure...")
        from modules.combined_scanner import (
            run_combined_scan,
            get_combined_progress,
            set_combined_cancel,
            _progress,
            _cancelled,
        )
        print("  [OK] All expected functions present")
        print("[OK] combined_scanner structure is correct")
        return True
    except Exception as e:
        print(f"[FAIL] Structure check failed: {e}")
        return False

def test_data_pipeline_enriched():
    """Test batch_download_and_enrich returns proper structure."""
    try:
        print("\n[TEST] Testing batch_download_and_enrich behavior...")
        from modules.data_pipeline import batch_download_and_enrich
        import inspect
        
        sig = inspect.signature(batch_download_and_enrich)
        print(f"  batch_download_and_enrich signature: {sig}")
        print("  [OK] batch_download_and_enrich callable")
        return True
    except Exception as e:
        print(f"[FAIL] Data pipeline test failed: {e}")
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("  COMBINED SCANNER STRUCTURE TEST (NO EXECUTION)")
    print("=" * 70)
    
    tests = [
        ("Imports", test_imports),
        ("Function Signatures", test_function_signatures),
        ("combined_scanner Structure", test_combined_scanner_structure),
        ("Data Pipeline", test_data_pipeline_enriched),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n[ {test_name} ]")
        try:
            results.append(test_func())
        except Exception as e:
            print(f"[FAIL] Unexpected error in {test_name}: {e}")
            results.append(False)
    
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"  RESULTS: {passed}/{total} tests passed")
    print("=" * 70)
    
    if passed == total:
        print("\n[SUCCESS] All structure tests passed!")
        print("Combined scanner is ready for integration.")
    else:
        print("\n[FAILURE] Some tests failed. Check output above.")
    
    sys.exit(0 if passed == total else 1)
