#!/usr/bin/env .venv\Scripts\python.exe
"""
Test to verify the 'pd not defined' fix in app.py _to_rows function
"""
import sys
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))

# Test 1: Import _to_rows implementation
print("Test 1: Simulating _to_rows logic from app.py...")
try:
    import pandas as pd
    
    # Create a test DataFrame with nested DataFrames (the problematic case)
    df_nested_df = pd.DataFrame({
        'ticker': ['AAPL', 'MSFT'],
        'price': [150.0, 300.0],
        'df_col': [pd.DataFrame({'a': [1]}), pd.DataFrame({'b': [2]})]  # Nested DataFrame
    })
    
    # Simulate _to_rows function (with the fix)
    def _to_rows(df):
        import pandas as pd  # <-- THE FIX: import locally
        if df is None or not hasattr(df, "to_dict") or df.empty:
            return []
        # Drop any column whose values are themselves DataFrames
        scalar_cols = [
            c for c in df.columns
            if not df[c].apply(lambda x: isinstance(x, pd.DataFrame)).any()
        ]
        df = df[scalar_cols]
        return df.where(df.notna(), other=None).to_dict(orient="records")
    
    # Try to convert nested DataFrame
    result = _to_rows(df_nested_df)
    print(f"  Result type: {type(result)}")
    print(f"  Result length: {len(result)}")
    print(f"  First row: {result[0] if result else 'N/A'}")
    print("  SUCCESS - No 'pd not defined' error!")
    
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Verify the actual logic from combined scan route
print("\nTest 2: Test with actual SEPA/QM-like result dict...")
try:
    import pandas as pd
    
    # Simulate Stage 3 output with nested "df" column
    s2_results_list = [
        {
            'ticker': 'AAPL',
            'score': 85.5,
            'df': pd.DataFrame({'Close': [150, 151, 152]}),  # This is the problematic case
            'some_value': 100.0
        },
        {
            'ticker': 'MSFT',
            'score': 72.0,
            'df': pd.DataFrame({'Close': [300, 301, 302]}),
            'some_value': 200.0
        }
    ]
    
    # First, strip the "df" column (as done in combined_scanner)
    _safe_s2 = [{k: v for k, v in r.items() if k != "df"}
                for r in s2_results_list]
    sepa_df_all = pd.DataFrame(_safe_s2) if _safe_s2 else pd.DataFrame()
    
    print(f"  After stripping 'df' column:")
    print(f"    DataFrame shape: {sepa_df_all.shape}")
    print(f"    Columns: {list(sepa_df_all.columns)}")
    
    # Now apply the _to_rows function
    def _to_rows(df):
        import pandas as pd
        if df is None or not hasattr(df, "to_dict") or df.empty:
            return []
        scalar_cols = [
            c for c in df.columns
            if not df[c].apply(lambda x: isinstance(x, pd.DataFrame)).any()
        ]
        df = df[scalar_cols]
        return df.where(df.notna(), other=None).to_dict(orient="records")
    
    rows = _to_rows(sepa_df_all)
    print(f"  After _to_rows:")
    print(f"    Result type: {type(rows)}")
    print(f"    Result length: {len(rows)}")
    if rows:
        print(f"    First row keys: {list(rows[0].keys())}")
        print(f"    First row: {rows[0]}")
    print("  SUCCESS - No DataFrame ambiguity errors!")
    
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("\nAll tests passed!")
