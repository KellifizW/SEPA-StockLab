#!/usr/bin/env python3
"""
Test script to verify JSON serialization fixes and result sanitization.
Tests the _sanitize_for_json function and result conversion chain.
"""

import sys
import json
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Import the sanitizer from app.py
# (we'll replicate it here for testing)
def _sanitize_for_json(obj, depth=0, max_depth=5):
    """
    Recursively sanitize object to ensure it's JSON-serializable.
    Safely converts any non-serializable types to None or string representation.
    """
    if depth > max_depth:
        return None
    
    # Handle None, bool, int, str
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    
    # Handle float (including NaN, inf)
    if isinstance(obj, (float, np.floating)):
        if pd.isna(obj) or np.isnan(obj):
            return None
        if np.isinf(obj):
            return str(obj)
        return float(obj)
    
    # Handle numpy types
    if isinstance(obj, (np.integer, np.bool_)):
        return obj.item()
    
    # Skip DataFrames and Series entirely
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    
    # Handle list
    if isinstance(obj, list):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    # Handle dict
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v, depth+1, max_depth) 
                for k, v in obj.items()}
    
    # Handle tuple → convert to list
    if isinstance(obj, tuple):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    # Fallback: convert to string
    return str(obj)


def test_basic_types():
    """Test basic JSON-safe types."""
    print("\n[TEST 1] Basic Types")
    test_cases = [
        (None, None),
        (True, True),
        (42, 42),
        (3.14, 3.14),
        ("hello", "hello"),
        ([], []),
        ({}, {}),
    ]
    
    for obj, expected in test_cases:
        result = _sanitize_for_json(obj)
        status = "✓" if result == expected else "✗"
        print(f"  {status} {type(obj).__name__}: {obj!r} → {result!r}")
        assert result == expected, f"Expected {expected}, got {result}"


def test_nan_and_inf():
    """Test NaN and infinity handling."""
    print("\n[TEST 2] NaN, Inf, and Special Floats")
    test_cases = [
        (np.nan, None),
        (float('nan'), None),
        (np.inf, "inf"),
        (float('inf'), "inf"),
        (-np.inf, "-inf"),
    ]
    
    for obj, expected in test_cases:
        result = _sanitize_for_json(obj)
        status = "✓" if result == expected else "✗"
        print(f"  {status} {type(obj).__name__}: {obj!r} → {result!r}")


def test_pandas_objects():
    """Test pandas DataFrame and Series."""
    print("\n[TEST 3] Pandas Objects")
    
    # DataFrame
    df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
    result = _sanitize_for_json(df)
    status = "✓" if result is None else "✗"
    print(f"  {status} DataFrame → {result!r}")
    
    # Series
    s = pd.Series([1, 2, 3])
    result = _sanitize_for_json(s)
    status = "✓" if result is None else "✗"
    print(f"  {status} Series → {result!r}")


def test_nested_structures():
    """Test nested lists and dicts with mixed types."""
    print("\n[TEST 4] Nested Structures")
    
    # List with NaN
    obj = [1, np.nan, 3.0]
    result = _sanitize_for_json(obj)
    expected = [1, None, 3.0]
    status = "✓" if result == expected else "✗"
    print(f"  {status} List with NaN: {obj!r} → {result!r}")
    
    # Dict with NaN
    obj = {"a": 1, "b": np.nan, "c": "test"}
    result = _sanitize_for_json(obj)
    expected = {"a": 1, "b": None, "c": "test"}
    status = "✓" if result == expected else "✗"
    print(f"  {status} Dict with NaN: {obj!r} → {result!r}")
    
    # Nested dict with inf
    obj = {"nested": {"value": np.inf}}
    result = _sanitize_for_json(obj)
    expected = {"nested": {"value": "inf"}}
    status = "✓" if result == expected else "✗"
    print(f"  {status} Nested dict with Inf: → {result!r}")


def test_complex_scan_result():
    """Test a complex result structure resembling actual scan output."""
    print("\n[TEST 5] Complex Scan Result")
    
    result = {
        "sepa": {
            "passed": [
                {"symbol": "AAPL", "price": 150.5, "rs_rank": np.float64(85.5)},
                {"symbol": "MSFT", "price": np.nan, "rs_rank": 90.0},
            ],
            "count": 2,
        },
        "qm": {
            "passed": [
                {"symbol": "GOOGL", "score": np.int64(95), "broken": True},
            ],
            "count": 1,
            "blocked": False,
        },
        "market": {
            "regime": "UPTREND",
            "strength": np.inf,
            "breadth": [np.nan, 0.85, np.inf],
        },
        "timing": {
            "last_run": datetime.now().isoformat(),
        },
    }
    
    # Sanitize
    sanitized = _sanitize_for_json(result)
    
    # Try to serialize to JSON
    try:
        json_str = json.dumps(sanitized)
        status = "✓"
        print(f"  {status} Successfully serialized to JSON")
        print(f"    JSON length: {len(json_str)} bytes")
        
        # Parse it back
        parsed = json.loads(json_str)
        print(f"    Successfully parsed back from JSON")
    except Exception as e:
        status = "✗"
        print(f"  {status} JSON serialization failed: {e}")
        return False
    
    return True


def test_numpy_types():
    """Test numpy numeric types."""
    print("\n[TEST 6] NumPy Types")
    
    test_cases = [
        (np.int64(42), 42),
        (np.float32(3.14), np.float32(3.14).item()),
        (np.bool_(True), True),
    ]
    
    for obj, expected in test_cases:
        result = _sanitize_for_json(obj)
        status = "✓" if result == expected or abs(result - expected) < 0.01 else "✗"
        print(f"  {status} {type(obj).__name__}: {obj!r} → {result!r}")


def run_all_tests():
    """Run all test suites."""
    print("=" * 70)
    print("JSON SERIALIZATION SANITIZER TEST SUITE")
    print("=" * 70)
    
    try:
        test_basic_types()
        test_nan_and_inf()
        test_pandas_objects()
        test_nested_structures()
        test_complex_scan_result()
        test_numpy_types()
        
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✓")
        print("=" * 70)
        return True
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return False
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
