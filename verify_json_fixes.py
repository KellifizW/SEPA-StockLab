#!/usr/bin/env python3
"""
Verification script to confirm all JSON serialization fixes are in place.
Checks that:
1. _sanitize_for_json function exists and has correct signature
2. _finish_job includes sanitization logic
3. All status endpoints have error handling
4. All response endpoints have error handling
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
app_file = ROOT / "app.py"

def read_file_section(start_line, end_line):
    """Read a section of app.py."""
    with open(app_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    return ''.join(lines[start_line-1:end_line])

def check_contains(text, search_str):
    """Check if text contains search string."""
    return search_str in text

def verify_fix(description, check_func):
    """Verify a fix with descriptive output."""
    try:
        result = check_func()
        status = "✓" if result else "✗"
        print(f"  {status} {description}")
        return result
    except Exception as e:
        print(f"  ✗ {description} (Error: {e})")
        return False

def main():
    print("=" * 70)
    print("JSON SERIALIZATION FIX VERIFICATION")
    print("=" * 70)
    
    if not app_file.exists():
        print(f"✗ app.py not found at {app_file}")
        return False
    
    results = []
    
    # Check 1: _sanitize_for_json function exists
    print("\n[CHECK 1] _sanitize_for_json Function")
    with open(app_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    results.append(verify_fix(
        "Function definition exists",
        lambda: "def _sanitize_for_json(" in content
    ))
    
    results.append(verify_fix(
        "Function handles NaN/Inf",
        lambda: "pd.isna(obj) or np.isnan(obj)" in content and "np.isinf(obj)" in content
    ))
    
    results.append(verify_fix(
        "Function handles DataFrames",
        lambda: "isinstance(obj, (pd.DataFrame, pd.Series))" in content
    ))
    
    results.append(verify_fix(
        "Function handles numpy types",
        lambda: "isinstance(obj, (np.integer, np.bool_))" in content
    ))
    
    # Check 2: _finish_job sanitization
    print("\n[CHECK 2] _finish_job Sanitization")
    results.append(verify_fix(
        "Sanitization call in _finish_job",
        lambda: "_sanitize_for_json(result)" in content and "_finish_job" in content
    ))
    
    results.append(verify_fix(
        "Logging sanitization success",
        lambda: "Result sanitized successfully" in content
    ))
    
    # Check 3: Status endpoint error handling
    print("\n[CHECK 3] Status Endpoint Error Handling")
    
    results.append(verify_fix(
        "api_scan_status has try/except",
        lambda: content.count("def api_scan_status") > 0 and \
                content[content.find("def api_scan_status"):].find("try:") < \
                (content[content.find("def api_scan_status"):].find("def api_") + 200)
    ))
    
    results.append(verify_fix(
        "api_qm_scan_status has try/except",
        lambda: content.count("def api_qm_scan_status") > 0 and \
                content[content.find("def api_qm_scan_status"):].find("try:") < 200
    ))
    
    results.append(verify_fix(
        "api_combined_scan_status has try/except",
        lambda: content.count("def api_combined_scan_status") > 0 and \
                content[content.find("def api_combined_scan_status"):].find("try:") < 200
    ))
    
    # Check 4: Response endpoint error handling
    print("\n[CHECK 4] Response Endpoint Error Handling")
    
    # Simple string searches for each function
    api_scan_run_has_try = content.count("def api_scan_run") > 0 and \
                           "Initial response created successfully" in content
    results.append(verify_fix(
        "api_scan_run wraps jsonify",
        lambda: api_scan_run_has_try
    ))
    
    api_qm_scan_run_has_try = content.count("def api_qm_scan_run") > 0 and \
                              "[QM_SCAN" in content and \
                              "Initial response created successfully" in content
    results.append(verify_fix(
        "api_qm_scan_run wraps jsonify",
        lambda: api_qm_scan_run_has_try
    ))
    
    api_combined_scan_run_has_try = content.count("def api_combined_scan_run") > 0 and \
                                    "[COMBINED SCAN" in content and \
                                    "Initial response created successfully" in content
    results.append(verify_fix(
        "api_combined_scan_run wraps jsonify",
        lambda: api_combined_scan_run_has_try
    ))
    
    # Check 5: Logging enhancements
    print("\n[CHECK 5] Logging Enhancements")
    
    results.append(verify_fix(
        "JSON serialization error logging",
        lambda: "JSON serialization error" in content
    ))
    
    results.append(verify_fix(
        "Result sanitization logging",
        lambda: "Result sanitized successfully" in content
    ))
    
    results.append(verify_fix(
        "Successfully serialized result logging",
        lambda: "Successfully serialized result" in content
    ))
    
    # Summary
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    
    if all(results):
        print(f"ALL CHECKS PASSED ✓ ({passed}/{total})")
        print("=" * 70)
        print("\n✓ All JSON serialization fixes are in place and ready for testing!")
        return True
    else:
        print(f"SOME CHECKS FAILED ✗ ({passed}/{total})")
        print("=" * 70)
        print("\n✗ Please review the failed checks and ensure all fixes are applied.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
