#!/usr/bin/env python3
"""
Verification script: Confirm all fixes have been applied correctly.
"""
import sys
from pathlib import Path

def check_file_for_pattern(filepath, pattern, should_exist=True):
    """Check if a file contains (or doesn't contain) a specific pattern."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        found = pattern in content
        status = "OK" if found == should_exist else "FAIL"
        
        if should_exist:
            msg = f"[{status}] Found: {pattern[:60]}"
        else:
            msg = f"[{status}] NOT found (as expected): {pattern[:60]}"
        
        return status == "OK", msg
    except Exception as e:
        return False, f"[ERROR] Could not read {filepath}: {e}"

print("\n" + "="*80)
print("COMBINED SCAN FIX VERIFICATION")
print("="*80 + "\n")

cwd = Path(".")
checks = []

# CHECK 1: combined_scanner.py has safe S2 checking
print("[CHECK 1] combined_scanner.py - Safe S2 result checking")
ok, msg = check_file_for_pattern(
    "modules/combined_scanner.py",
    "is_empty = (isinstance(s2_results, list)",
    should_exist=True
)
print(f"  {msg}")
checks.append(ok)

# CHECK 2: combined_scanner.py has safe S2_passed checking  
print("[CHECK 2] combined_scanner.py - Safe QM S2 result checking")
ok, msg = check_file_for_pattern(
    "modules/combined_scanner.py",
    "is_empty = (isinstance(s2_passed, list)",
    should_exist=True
)
print(f"  {msg}")
checks.append(ok)

# CHECK 3: app.py has row-by-row _to_rows
print("[CHECK 3] app.py - Row-by-row _to_rows() iteration")
ok, msg = check_file_for_pattern(
    "app.py",
    "for idx, row in df.iterrows():",
    should_exist=True
)
print(f"  {msg}")
checks.append(ok)

# CHECK 4: app.py _clean() skips DataFrame early
print("[CHECK 4] app.py - _clean() skips DataFrame early")
ok, msg = check_file_for_pattern(
    "app.py",
    "if isinstance(obj, (pd.DataFrame, pd.Series)):",
    should_exist=True
)
print(f"  {msg}")
checks.append(ok)

# CHECK 5: Remove the old problematic patterns
print("[CHECK 5] combined_scanner.py - NO 'if _cancelled() or not s2' pattern")
ok, msg = check_file_for_pattern(
    "modules/combined_scanner.py",
    "if _cancelled() or not s2_results:",
    should_exist=False
)
print(f"  {msg}")
checks.append(ok)

print("[CHECK 6] combined_scanner.py - NO 'if _cancelled() or not s2_passed' pattern")
ok, msg = check_file_for_pattern(
    "modules/combined_scanner.py",
    "if _cancelled() or not s2_passed:",
    should_exist=False
)
print(f"  {msg}")
checks.append(ok)

# SUMMARY
print("\n" + "="*80)
passed = sum(checks)
total = len(checks)
print(f"RESULT: {passed}/{total} checks passed")
print("="*80)

if passed == total:
    print("\nSUCCESS: All fixes have been applied correctly!")
    print("\nYou can now try running a combined scan.")
    print("If you still get 'truth value is ambiguous' error:")
    print("  1. Check the logs/combined_scan_*.log file")
    print("  2. Search for 'Traceback' to find exact error location")
    print("  3. Share the log and error message for further debugging")
    sys.exit(0)
else:
    print(f"\nFAILURE: {total - passed} check(s) failed")
    print("Some fixes may not have been applied correctly.")
    print("Please review the failed checks above.")
    sys.exit(1)
