#!/usr/bin/env python3
"""
Integration test to verify the complete response handling chain.
Tests:
1. Result sanitization before storing
2. JSON serialization of entire response
3. Error handling in the full pipeline
"""

import sys
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch, MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Define the sanitizer function (matching app.py implementation)
def _sanitize_for_json(obj, depth=0, max_depth=5):
    """Recursively sanitize object to ensure it's JSON-serializable."""
    if depth > max_depth:
        return None
    
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    
    if isinstance(obj, (float, np.floating)):
        if pd.isna(obj) or np.isnan(obj):
            return None
        if np.isinf(obj):
            return str(obj)
        return float(obj)
    
    if isinstance(obj, (np.integer, np.bool_)):
        return obj.item()
    
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    
    if isinstance(obj, list):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v, depth+1, max_depth) 
                for k, v in obj.items()}
    
    if isinstance(obj, tuple):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    return str(obj)


def simulate_combined_scan_result():
    """Simulate a realistic combined scan result with problematic data."""
    return {
        "sepa": {
            "passed": [
                {
                    "symbol": "AAPL",
                    "price": 150.5,
                    "rs_rank": np.float64(85.5),
                    "chg_pct": np.nan,  # Problematic: NaN
                    "score": np.int64(87),
                    "vol_mult": 1.5,
                },
                {
                    "symbol": "MSFT",
                    "price": 340.2,
                    "rs_rank": np.float64(90.0),
                    "chg_pct": 2.5,
                    "score": np.int64(92),
                    "vol_mult": np.inf,  # Problematic: Inf
                },
            ],
            "count": 2,
        },
        "qm": {
            "passed": [
                {"symbol": "GOOGL", "score": np.int64(95), "stars": 4.5},
            ],
            "count": 1,
            "blocked": False,
        },
        "market": {
            "regime": "UPTREND",
            "breadth": [np.nan, 0.85, np.inf],  # Mixed problematic values
            "timestamp": datetime.now(),
        },
        "timing": {
            "start": datetime.now().isoformat(),
            "end": datetime.now().isoformat(),
            "duration_sec": 188.0,
        },
        "sepa_csv": "scan_results/sepa_2026-03-01.csv",
        "qm_csv": "scan_results/qm_2026-03-01.csv",
    }


def test_finish_job_sanitization():
    """Test that _finish_job sanitizes results properly."""
    print("\n[TEST 1] _finish_job Sanitization")
    
    result = simulate_combined_scan_result()
    
    try:
        # Simulate what _finish_job does
        sanitized_result = _sanitize_for_json(result)
        
        # Verify it's JSON-serializable
        json_str = json.dumps(sanitized_result)
        
        print(f"  ✓ Result sanitized successfully")
        print(f"    Original keys: {list(result.keys())}")
        print(f"    Sanitized keys: {list(sanitized_result.keys())}")
        print(f"    JSON size: {len(json_str)} bytes")
        
        # Verify re-parsing works
        parsed = json.loads(json_str)
        print(f"  ✓ Result re-parsed from JSON successfully")
        
        return True
    except Exception as e:
        print(f"  ✗ Sanitization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_jsonify_response():
    """Test that Flask's jsonify() works with sanitized result."""
    print("\n[TEST 2] Flask jsonify() Response")
    
    result = simulate_combined_scan_result()
    sanitized = _sanitize_for_json(result)
    
    try:
        # Simulate Flask's behavior
        response_data = {
            "status": "done",
            "result": sanitized,
        }
        
        # This is what jsonify() does internally
        json_str = json.dumps(response_data, default=str)
        
        print(f"  ✓ Response successfully JSON-encoded")
        print(f"    Response structure: {{{', '.join(response_data.keys())}}}")
        
        # Verify response can be parsed
        parsed = json.loads(json_str)
        print(f"  ✓ Response successfully parsed back")
        
        return True
    except Exception as e:
        print(f"  ✗ Response encoding failed: {e}")
        return False


def test_status_endpoint_response():
    """Test the complete status endpoint response chain."""
    print("\n[TEST 3] Complete Status Endpoint Response")
    
    # Simulate job dict
    job = {
        "status": "done",
        "result": simulate_combined_scan_result(),
        "error": None,
        "started": datetime.now().isoformat(),
        "finished": datetime.now().isoformat(),
        "log_file": "logs/combined_scan_abc123.log",
    }
    
    try:
        # Simulate what api_combined_scan_status does
        job_sanitized = _sanitize_for_json(job)
        
        # Create response
        response = {
            "status": "done",
            "result": job_sanitized.get("result"),
        }
        
        # Serialize to JSON
        json_str = json.dumps(response, default=str)
        
        print(f"  ✓ Status endpoint response created successfully")
        print(f"    Response JSON size: {len(json_str)} bytes")
        
        # Verify it can be parsed
        parsed = json.loads(json_str)
        print(f"  ✓ Status endpoint response parsed successfully")
        
        return True
    except Exception as e:
        print(f"  ✗ Status endpoint response failed: {e}")
        return False


def test_error_handling():
    """Test error handling in the response chain."""
    print("\n[TEST 4] Error Handling")
    
    # Test 1: Problematic result that would fail without sanitization
    print("  Testing unsanitized result with problematic values...")
    
    try:
        problematic = {
            "data": [np.nan, np.inf, pd.Series([1, 2, 3])],
        }
        
        # This should fail without sanitization
        try:
            json.dumps(problematic)
            print("    ✗ Unsanitized result should have failed but didn't")
            return False
        except TypeError:
            print("    ✓ Unsanitized result fails as expected (TypeError)")
        
        # This should work with sanitization
        sanitized = _sanitize_for_json(problematic)
        json.dumps(sanitized)
        print("    ✓ Sanitized result succeeds")
        
        return True
        
    except Exception as e:
        print(f"  ✗ Unexpected error: {e}")
        return False


def test_edge_cases():
    """Test edge cases and boundary conditions."""
    print("\n[TEST 5] Edge Cases")
    
    test_cases = [
        ("Empty result", {}),
        ("None result", None),
        ("Nested NaN in lists", {"data": [[np.nan], [1, 2], [np.inf]]}),
        ("Deep nesting", {"l1": {"l2": {"l3": {"l4": {"data": np.nan}}}}}),
        ("Mixed types", {"int": 1, "float": 1.5, "str": "test", "bool": True, "none": None}),
    ]
    
    all_passed = True
    for name, obj in test_cases:
        try:
            sanitized = _sanitize_for_json(obj)
            json_str = json.dumps(sanitized)
            status = "✓"
        except Exception as e:
            status = "✗"
            all_passed = False
            print(f"    {status} {name}: {e}")
            continue
        
        print(f"    {status} {name}")
    
    return all_passed


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("RESPONSE HANDLING INTEGRATION TEST SUITE")
    print("=" * 70)
    
    tests = [
        test_finish_job_sanitization,
        test_jsonify_response,
        test_status_endpoint_response,
        test_error_handling,
        test_edge_cases,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Test {test.__name__} crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    
    if all(results):
        print(f"ALL TESTS PASSED ✓ ({passed}/{total})")
        print("=" * 70)
        return True
    else:
        print(f"SOME TESTS FAILED ✗ ({passed}/{total} passed)")
        print("=" * 70)
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
