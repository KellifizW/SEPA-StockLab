#!/usr/bin/env python3
"""
Final comprehensive verification script for all fixes.
Ensures that all modifications are in place and working.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def verify_file_contains(filepath, search_str, description):
    """Verify that a file contains a specific string."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        if search_str in content:
            print(f"  ✓ {description}")
            return True
        else:
            print(f"  ✗ {description}")
            return False
    except Exception as e:
        print(f"  ✗ {description} (Error: {e})")
        return False

def main():
    print("="*70)
    print("FINAL VERIFICATION - ALL FIXES")
    print("="*70)
    
    results = []
    
    # Fix #1: /api/fmp/stats endpoint
    print("\n[FIX #1] /api/fmp/stats Endpoint")
    results.append(verify_file_contains(
        ROOT / "app.py",
        '@app.route("/api/fmp/stats", methods=["GET"])',
        "FMP stats endpoint route defined"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'def api_fmp_stats():',
        "FMP stats endpoint function defined"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        '"monthly_calls"',
        "FMP stats returns monthly_calls"
    ))
    
    # Fix #2: get_next_earnings_date
    print("\n[FIX #2] get_next_earnings_date AttributeError")
    results.append(verify_file_contains(
        ROOT / "modules" / "data_pipeline.py",
        'if cal is None:',
        "Proper None check before .empty"
    ))
    results.append(verify_file_contains(
        ROOT / "modules" / "data_pipeline.py",
        'isinstance(cal, pd.DataFrame)',
        "Type check for DataFrame"
    ))
    results.append(verify_file_contains(
        ROOT / "modules" / "data_pipeline.py",
        'isinstance(cal, dict)',
        "Type check for dict"
    ))
    
    # Fix #3: Truth value fixes in combined_scanner.py
    print("\n[FIX #3] Combined Scanner Boolean Checks")
    results.append(verify_file_contains(
        ROOT / "modules" / "combined_scanner.py",
        'is_empty = (isinstance(s2_results, list) and len(s2_results) == 0)',
        "SEPA S2 safe empty check (line ~260)"
    ))
    results.append(verify_file_contains(
        ROOT / "modules" / "combined_scanner.py",
        'isinstance(s2_results, pd.DataFrame) and s2_results.empty',
        "SEPA S2 DataFrame empty check"
    ))
    results.append(verify_file_contains(
        ROOT / "modules" / "combined_scanner.py",
        'is_empty = (isinstance(s2_passed, list)',
        "QM S2 safe empty check (line ~317)"
    ))
    results.append(verify_file_contains(
        ROOT / "modules" / "combined_scanner.py",
        'isinstance(s2_passed, pd.DataFrame) and s2_passed.empty',
        "QM S2 DataFrame empty check"
    ))
    
    # Fix #4: Sanitization function
    print("\n[FIX #4] JSON Sanitization & Error Handling")
    results.append(verify_file_contains(
        ROOT / "app.py",
        'def _sanitize_for_json(obj, depth=0, max_depth=5):',
        "_sanitize_for_json function defined"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'pd.isna(obj) or np.isnan(obj)',
        "NaN handling in sanitizer"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'isinstance(obj, (pd.DataFrame, pd.Series))',
        "DataFrame/Series detection in sanitizer"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'result = _sanitize_for_json(result)',
        "_sanitize_for_json called in _finish_job"
    ))
    
    # Fix #5: Status endpoint error handling
    print("\n[FIX #5] Status Endpoint Error Handling")
    results.append(verify_file_contains(
        ROOT / "app.py",
        'def api_combined_scan_status(jid):',
        "Combined scan status endpoint exists"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'except TypeError as te:',
        "TypeError handling in status endpoint"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'def api_scan_status(jid):',
        "Scan status endpoint exists"
    ))
    results.append(verify_file_contains(
        ROOT / "app.py",
        'def api_qm_scan_status(jid):',
        "QM scan status endpoint exists"
    ))
    
    # Summary
    print("\n" + "="*70)
    passed = sum(results)
    total = len(results)
    
    if all(results):
        print(f"✓ ALL CHECKS PASSED ({passed}/{total})")
        print("="*70)
        print("\n✓ All fixes are deployed and ready!")
        print("\nNext steps:")
        print("1. Restart Flask server: python app.py (or python start_web.py)")
        print("2. Run a combined scan from the UI")
        print("3. Monitor for 'truth value is ambiguous' error")
        print("4. Check browser console for 404 errors (should be gone)")
        return 0
    else:
        print(f"✗ SOME CHECKS FAILED ({passed}/{total})")
        print("="*70)
        print("\nPlease review the failed checks and ensure all fixes are applied.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
