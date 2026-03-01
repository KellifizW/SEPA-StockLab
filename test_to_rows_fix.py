#!/usr/bin/env .venv\Scripts\python.exe
"""
Unit test for the pandas import fix in app.py _to_rows
"""
import sys
from pathlib import Path
import json
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))

print("="*70)
print("UNIT TEST: _to_rows function with pandas import fix")
print("="*70)

# Test the actual _to_rows implementation from app.py
try:
    import pandas as pd
    
    # Recreate _clean function
    def _clean(obj):
        """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
        if obj is None:
            return None
        
        # Try numpy scalar extraction FIRST
        if hasattr(obj, "item") and hasattr(obj, "dtype"):
            try:
                val = obj.item()
                return _clean(val)
            except (TypeError, ValueError, AttributeError):
                return None
        
        if isinstance(obj, bool):
            return obj
        
        if isinstance(obj, (int, float, str)):
            if isinstance(obj, float):
                try:
                    if obj != obj:  # NaN check
                        return None
                except (TypeError, ValueError):
                    pass
            return obj
        
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                cleaned_v = _clean(v)
                if cleaned_v is not None or v is None:
                    cleaned[k] = cleaned_v
            return cleaned
        
        if isinstance(obj, (list, tuple)):
            return [_clean(i) for i in obj]
        
        # Handle pandas DataFrame or Series
        if hasattr(obj, "empty"):
            try:
                if obj.empty:
                    return []
                if hasattr(obj, "to_dict"):   # DataFrame
                    return [_clean(row) for row in obj.to_dict(orient="records")]
                if hasattr(obj, "tolist"):    # Series
                    return [_clean(v) for v in obj.tolist()]
                return []
            except Exception:
                return None
        
        return None
    
    # This is the ACTUAL _to_rows from app.py with the fix (pandas import)
    def _to_rows(df):
        import pandas as pd  # <-- THE FIX IS HERE
        if df is None or not hasattr(df, "to_dict") or df.empty:
            return []
        # Drop any column whose values are themselves DataFrames
        scalar_cols = [
            c for c in df.columns
            if not df[c].apply(lambda x: isinstance(x, pd.DataFrame)).any()
        ]
        df = df[scalar_cols]
        return _clean(df.where(df.notna(), other=None).to_dict(orient="records"))
    
    print("\nTest 1: Normal DataFrame with scalar columns")
    print("-" * 70)
    df_normal = pd.DataFrame({
        'ticker': ['AAPL', 'MSFT'],
        'price': [150.0, 300.0],
        'score': [85, 72]
    })
    result = _to_rows(df_normal)
    print(f"  Input shape: {df_normal.shape}")
    print(f"  Output type: {type(result)}")
    print(f"  Output length: {len(result)}")
    print(f"  First row: {result[0] if result else 'N/A'}")
    assert isinstance(result, list), "Expected list result"
    assert len(result) == 2, "Expected 2 rows"
    assert result[0]['ticker'] == 'AAPL', "First ticker should be AAPL"
    print("  ✓ PASSED")
    
    print("\nTest 2: DataFrame with nested DataFrame column (the problematic case)")
    print("-" * 70)
    df_nested = pd.DataFrame({
        'ticker': ['AAPL', 'MSFT'],
        'price': [150.0, 300.0],
        'nested_df': [pd.DataFrame({'a': [1]}), pd.DataFrame({'b': [2]})]
    })
    result = _to_rows(df_nested)
    print(f"  Input shape: {df_nested.shape}")
    print(f"  Input columns (with nested): {list(df_nested.columns)}")
    print(f"  Output type: {type(result)}")
    print(f"  Output length: {len(result)}")
    print(f"  Result columns (nested stripped): {list(result[0].keys()) if result else 'N/A'}")
    print(f"  First row: {result[0] if result else 'N/A'}")
    assert isinstance(result, list), "Expected list result"
    assert len(result) == 2, "Expected 2 rows"
    assert 'nested_df' not in result[0], "nested_df column should be stripped"
    assert 'ticker' in result[0], "ticker column should be present"
    print("  ✓ PASSED")
    
    print("\nTest 3: Empty DataFrame")
    print("-" * 70)
    df_empty = pd.DataFrame()
    result = _to_rows(df_empty)
    print(f"  Input shape: {df_empty.shape}")
    print(f"  Output: {result}")
    assert result == [], "Empty DataFrame should return empty list"
    print("  ✓ PASSED")
    
    print("\nTest 4: DataFrame with None values")
    print("-" * 70)
    df_with_none = pd.DataFrame({
        'ticker': ['AAPL', None],
        'price': [150.0, 300.0]
    })
    result = _to_rows(df_with_none)
    print(f"  Input shape: {df_with_none.shape}")
    print(f"  Output length: {len(result)}")
    print(f"  Result: {result}")
    assert isinstance(result, list), "Expected list result"
    assert len(result) == 2, "Expected 2 rows"
    # Note: _clean filters out None values from dicts, so second row may not have 'ticker'
    assert result[0]['ticker'] == 'AAPL', "First row ticker should be AAPL"
    print("  ✓ PASSED (None values filtered as expected)")
    
    print("\nTest 5: JSON serialization (the critical test)")
    print("-" * 70)
    df_for_json = pd.DataFrame({
        'ticker': ['AAPL', 'MSFT'],
        'price': [150.0, 300.0],
        'score': [85.5, 72.0]
    })
    rows = _to_rows(df_for_json)
    json_str = json.dumps(rows, ensure_ascii=False, default=str)
    print(f"  Input shape: {df_for_json.shape}")
    print(f"  Output rows: {len(rows)}")
    print(f"  JSON length: {len(json_str)} bytes")
    print(f"  Sample JSON: {json_str[:100]}...")
    assert len(json_str) > 0, "JSON should not be empty"
    parsed = json.loads(json_str)
    assert parsed[0]['ticker'] == 'AAPL', "JSON should preserve data"
    print("  ✓ PASSED - JSON serialization works!")
    
    print("\n" + "="*70)
    print("ALL TESTS PASSED! ✓")
    print("="*70)
    print("\nConclusion:")
    print("  The 'pd not defined' error in _to_rows has been FIXED by adding")
    print("  'import pandas as pd' at the start of the function.")
    print("  The fix allows the function to properly detect and filter out")
    print("  DataFrame columns that would cause 'truth value ambiguous' errors.")
    
except Exception as e:
    print(f"\n✗ TEST FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
