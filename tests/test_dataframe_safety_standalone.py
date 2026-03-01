#!/usr/bin/env python3
"""
DataFrame Safety Tests - Standalone version (no pytest required)
Demonstrates the key safety issues and correct solutions.
"""

import pandas as pd
import numpy as np

def test_dangerous_operations():
    """Demonstrate why these operations fail."""
    print("\n" + "="*70)
    print("DANGEROUS OPERATIONS (will fail)")
    print("="*70)
    
    df = pd.DataFrame({"a": [1, 2, 3]})
    
    # Test 1: Direct boolean cast
    print("\n[TEST 1] Direct boolean cast: if df:")
    try:
        if df:
            pass
        print("  ❌ Should have failed!")
    except ValueError as e:
        print(f"  ✓ Correctly fails with: {str(e)[:60]}...")
    
    # Test 2: Negated boolean cast
    print("\n[TEST 2] Negated boolean: if not df:")
    try:
        if not df:
            pass
        print("  ❌ Should have failed!")
    except ValueError as e:
        print(f"  ✓ Correctly fails with: {str(e)[:60]}...")
    
    # Test 3: OR operation
    print("\n[TEST 3] OR operation: result = df or {}")
    try:
        result = df or {}
        print("  ❌ Should have failed!")
    except ValueError as e:
        print(f"  ✓ Correctly fails with: {str(e)[:60]}...")
    
    # Test 4: AND operation
    print("\n[TEST 4] AND operation: if True and df:")
    try:
        if True and df:
            pass
        print("  ❌ Should have failed!")
    except ValueError as e:
        print(f"  ✓ Correctly fails with: {str(e)[:60]}...")
    
    # Test 5: Conditional expression
    print("\n[TEST 5] Conditional: result = df if df else {}")
    try:
        result = df if df else {}
        print("  ❌ Should have failed!")
    except ValueError as e:
        print(f"  ✓ Correctly fails with: {str(e)[:60]}...")


def test_safe_operations():
    """Demonstrate correct, safe operations."""
    print("\n" + "="*70)
    print("SAFE OPERATIONS (will succeed)")
    print("="*70)
    
    df_none = None
    df_empty = pd.DataFrame()
    df_nonempty = pd.DataFrame({"a": [1, 2, 3]})
    
    # Test 1: is None check
    print("\n[TEST 1] None check: if df is None")
    if df_none is None:
        print("  ✓ Works: df_none is None")
    
    # Test 2: is not None check
    print("\n[TEST 2] Not None check: if df is not None")
    if df_nonempty is not None:
        print("  ✓ Works: df_nonempty is not None")
    
    # Test 3: Empty checking
    print("\n[TEST 3] Empty check: df.empty")
    print(f"  ✓ df_empty.empty = {df_empty.empty}")
    print(f"  ✓ df_nonempty.empty = {df_nonempty.empty}")
    
    # Test 4: isinstance check
    print("\n[TEST 4] isinstance check")
    if isinstance(df_nonempty, pd.DataFrame):
        print("  ✓ Works: isinstance(df, pd.DataFrame)")
    
    # Test 5: hasattr check
    print("\n[TEST 5] hasattr check")
    if hasattr(df_nonempty, "empty") and df_nonempty.empty == False:
        print("  ✓ Works: hasattr(df, 'empty')")
    
    # Test 6: len() check
    print("\n[TEST 6] len() check")
    if len(df_nonempty) > 0:
        print(f"  ✓ Works: len(df) = {len(df_nonempty)}")
    
    # Test 7: Safe fallback pattern
    print("\n[TEST 7] Safe fallback: explicit if/else")
    data = df_none
    if data is None:
        data = {}
    print(f"  ✓ Works: result is {type(data).__name__}")
    
    # Test 8: Dict.get with safe fallback
    print("\n[TEST 8] Dict.get with safe fallback")
    results = {
        "all_scored": pd.DataFrame({"a": [1]}),
        "all": None
    }
    source = results.get("all_scored")
    if source is None or (isinstance(source, pd.DataFrame) and source.empty):
        source = results.get("all")
    print(f"  ✓ Works: fallback logic executed safely")


def test_problem_solved():
    """Demonstrate THE BUG that was fixed in app.py:988"""
    print("\n" + "="*70)
    print("THE ROOT CAUSE - app.py line 988")
    print("="*70)
    
    print("\n❌ ORIGINAL BUGGY CODE:")
    print("   qm_all_rows = _to_rows(qm_result.get('all_scored') or qm_result.get('all'))")
    print("\nWhy it fails: When qm_result.get('all_scored') returns a non-empty DataFrame,")
    print("Python tries to evaluate its boolean value in the 'or' operation.")
    print("pandas raises: ValueError: The truth value of a DataFrame is ambiguous")
    
    print("\n" + "-"*70)
    print("\n✅ FIXED CODE:")
    print("   qm_all_source = qm_result.get('all_scored')")
    print("   if qm_all_source is None or (isinstance(...) and qm_all_source.empty):")
    print("       qm_all_source = qm_result.get('all')")
    print("   qm_all_rows = _to_rows(qm_all_source)")
    
    print("\nWhy it works: No boolean evaluation of DataFrame.")
    print("Only explicit checks: None check and .empty property check.")
    
    # Demonstrate with actual code
    print("\n" + "-"*70)
    print("Demonstration with sample data:\n")
    
    qm_result = {
        "all_scored": pd.DataFrame({"score": [1, 2, 3]}),
        "all": None
    }
    
    # Show how the buggy version would fail
    print("1. Buggy version (will crash):")
    try:
        qm_all = qm_result.get("all_scored") or qm_result.get("all")
        print("   ERROR: Should have crashed!")
    except ValueError as e:
        print(f"   ✓ Crashes with: ValueError (as expected)")
    
    # Show how the fixed version works
    print("\n2. Fixed version (will work):")
    qm_all_source = qm_result.get("all_scored")
    if qm_all_source is None or (isinstance(qm_all_source, pd.DataFrame) and qm_all_source.empty):
        qm_all_source = qm_result.get("all")
    print(f"   ✓ Works! Result type: {type(qm_all_source).__name__}, shape: {qm_all_source.shape}")


def main():
    print("\n" + "="*70)
    print("DataFrame Truth Value Safety - Comprehensive Test Suite")
    print("="*70)
    
    try:
        test_dangerous_operations()
        test_safe_operations()
        test_problem_solved()
        
        print("\n" + "="*70)
        print("SUMMARY")
        print("="*70)
        print("""
Key Takeaways:
  1. Never use: if df, if not df, df or fallback, df_a if df_b else df_c
  2. Always use: if df is None, if isinstance(df, pd.DataFrame) and df.empty
  3. For fallbacks: Check None first, THEN check .empty, THEN fallback
  4. The root cause of the bug was line 988 in app.py with 'or' operator
  5. Fixed by using explicit if/elif structure instead of Python's 'or'
        """)
        
        return 0
    
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
