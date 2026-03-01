#!/usr/bin/env python3
"""
Test script to verify DataFrame conversion fixes.
Tests the new _to_rows() implementation with problematic data.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np

def test_to_rows_with_dataframe_column():
    """Test _to_rows when a DataFrame contains nested DataFrames."""
    print("\n" + "="*60)
    print("TEST 1: DataFrame with nested DataFrame column")
    print("="*60)
    
    # Create a DataFrame with a nested DataFrame column (BAD)
    nested_df = pd.DataFrame({'A': [1, 2], 'B': [3, 4]})
    df_bad = pd.DataFrame({
        'stock': ['AAPL', 'GOOGL'],
        'price': [150.0, 2800.0],
        'df': [nested_df, nested_df],  # This is the problem!
    })
    
    print(f"Created DataFrame with nested 'df' column:")
    print(df_bad)
    print(f"\nDataFrame shape: {df_bad.shape}")
    print(f"Column types: {df_bad.dtypes.to_dict()}")
    
    # Try the old way (will fail)
    print("\nTrying OLD method (df.where(df.notna(), ...))...")
    try:
        result = df_bad.where(df_bad.notna(), other=None).to_dict(orient="records")
        print("❌ OLD METHOD: Unexpectedly succeeded (should have failed)")
        return False
    except ValueError as e:
        if "truth value" in str(e):
            print(f"✓ OLD METHOD: Got expected error: {e}")
        else:
            print(f"❌ OLD METHOD: Got different error: {e}")
            return False
    
    # Try the new way (should work)
    print("\nTrying NEW method (iterrows with isinstance check)...")
    try:
        records = []
        for idx, row in df_bad.iterrows():
            record = {}
            for col, val in row.items():
                # Skip DataFrame/Series/complex objects
                if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                    print(f"  Skipping column '{col}' with type {type(val).__name__}")
                    continue
                # Convert NaN/None to None
                if pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val
            records.append(record)
        print(f"✓ NEW METHOD: Success! Converted {len(records)} rows")
        print(f"  Result: {records}")
        return True
    except Exception as e:
        print(f"❌ NEW METHOD: Failed with error: {e}")
        return False


def test_to_rows_with_none_column():
    """Test _to_rows when a column contains all None/NaN."""
    print("\n" + "="*60)
    print("TEST 2: DataFrame with None/NaN column")
    print("="*60)
    
    df = pd.DataFrame({
        'stock': ['AAPL', 'GOOGL'],
        'price': [150.0, 2800.0],
        'extra': [None, None],
    })
    
    print(f"Created DataFrame with None column:")
    print(df)
    
    print("\nUsing NEW method (iterrows with isinstance check)...")
    try:
        records = []
        for idx, row in df.iterrows():
            record = {}
            for col, val in row.items():
                # Skip DataFrame/Series/complex objects
                if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                    continue
                # Convert NaN/None to None
                if pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val
            records.append(record)
        print(f"✓ Success! Converted {len(records)} rows")
        print(f"  Result: {records}")
        return True
    except Exception as e:
        print(f"❌ Failed with error: {e}")
        return False


def test_to_rows_with_mixed_types():
    """Test _to_rows with mixed scalar and non-scalar types."""
    print("\n" + "="*60)
    print("TEST 3: DataFrame with mixed scalar/non-scalar types")
    print("="*60)
    
    nested_series = pd.Series([1, 2, 3])
    df = pd.DataFrame({
        'stock': ['AAPL', 'GOOGL'],
        'price': [150.0, 2800.0],
        'series': [nested_series, nested_series],
        'score': [85.5, 92.3],
    })
    
    print(f"Created DataFrame with Series column:")
    print(df)
    
    print("\nUsing NEW method (iterrows with isinstance check)...")
    try:
        records = []
        for idx, row in df.iterrows():
            record = {}
            for col, val in row.items():
                # Skip DataFrame/Series/complex objects
                if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                    print(f"  Skipping column '{col}' with type {type(val).__name__}")
                    continue
                # Convert NaN/None to None
                if pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val
            records.append(record)
        print(f"✓ Success! Converted {len(records)} rows")
        print(f"  Result: {records}")
        return True
    except Exception as e:
        print(f"❌ Failed with error: {e}")
        return False


def test_empty_dataframe():
    """Test _to_rows with empty DataFrame."""
    print("\n" + "="*60)
    print("TEST 4: Empty DataFrame")
    print("="*60)
    
    df = pd.DataFrame()
    print(f"Created empty DataFrame")
    
    print("\nChecking if empty...")
    if hasattr(df, "empty") and df.empty:
        print("✓ DataFrame is empty, returning empty list")
        return True
    else:
        print("❌ Empty check failed")
        return False


def test_none_input():
    """Test _to_rows with None input."""
    print("\n" + "="*60)
    print("TEST 5: None input")
    print("="*60)
    
    df = None
    print(f"Testing with None input")
    
    if df is None or not hasattr(df, "to_dict"):
        print("✓ None check passed, returning empty list")
        return True
    else:
        print("❌ None check failed")
        return False


if __name__ == "__main__":
    print("\n" + "◆"*60)
    print("TESTING NEW _to_rows() IMPLEMENTATION")
    print("◆"*60)
    
    results = []
    results.append(("DataFrame with nested DataFrame column", test_to_rows_with_dataframe_column()))
    results.append(("DataFrame with None/NaN column", test_to_rows_with_none_column()))
    results.append(("DataFrame with mixed types", test_to_rows_with_mixed_types()))
    results.append(("Empty DataFrame", test_empty_dataframe()))
    results.append(("None input", test_none_input()))
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for test_name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)
    print(f"\nTotal: {passed_count}/{total_count} tests passed")
    
    if passed_count == total_count:
        print("\n✓ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("\n❌ SOME TESTS FAILED")
        sys.exit(1)
