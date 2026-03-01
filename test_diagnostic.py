#!/usr/bin/env python3
"""
Diagnostic script: Patch all boolean operations to find the exact error source.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import traceback
import pandas as pd
import numpy as np

# Enable comprehensive logging
logging.basicConfig(level=logging.DEBUG)

print("\n" + "="*80)
print("DIAG: Finding exact 'truth value is ambiguous' error source")
print("="*80 + "\n")

# First, let's check if issue is in the boolean check itself
print("[*] Test 1: Direct boolean DataFrame checks")
df_test = pd.DataFrame({'A': [1, 2], 'B': [3, 4]})

try:
    # This should fail
    if not df_test:
        print("  FAIL: Unexpected: 'if not df' succeeded")
except ValueError as e:
    if 'truth value' in str(e):
        print(f"  OK: 'if not df' fails as expected")
    else:
        print(f"  FAIL: Different error: {e}")

try:
    # This should also fail
    result = bool(df_test)
    print(f"  FAIL: Unexpected: bool(df) returned {result}")
except ValueError as e:
    if 'truth value' in str(e):
        print(f"  OK: bool(df) fails as expected")
    else:
        print(f"  FAIL: Different error: {e}")

print("\n[*] Test 2: List checks (should be safe)")
list_test = [1, 2, 3]
try:
    if list_test:
        print("  OK: 'if list_test' works fine")
except ValueError as e:
    print(f"  FAIL: Unexpected error on list: {e}")

empty_list = []
try:
    if not empty_list:
        print("  OK: 'if not empty_list' works fine")
except ValueError as e:
    print(f"  FAIL: Unexpected error on empty list: {e}")

print("\n[*] Test 3: Running actual combined scan")
try:
    from modules.combined_scanner import run_combined_scan
    
    # Run combined scan with default settings (will do full screening)
    print("  Calling run_combined_scan()...")
    sepa_result, qm_result = run_combined_scan(verbose=False)
    
    print(f"  SUCCESS: Scan completed!")
    if sepa_result:
        print(f"    SEPA passed: {len(sepa_result.get('passed', pd.DataFrame()))}")
    if qm_result:
        print(f"    QM passed: {len(qm_result.get('passed', pd.DataFrame()))}")
    
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {str(e)[:200]}")
    
    # Check if it's the ambiguous truth value error
    if 'truth value' in str(e).lower() and 'ambiguous' in str(e).lower():
        print("\n  FOUND IT: THIS IS THE 'TRUTH VALUE AMBIGUOUS' ERROR!")
        print("\n  Full traceback:")
        traceback.print_exc()
    else:
        print(f"\n  Different error")
        print("  Traceback (last 30 lines):")
        tb_lines = traceback.format_exc().split('\n')
        for line in tb_lines[-30:]:
            print(line)

print("\n" + "="*80)
