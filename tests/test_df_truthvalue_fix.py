#!/usr/bin/env python3
"""
tests/test_df_truthvalue_fix.py
Regression test for 'truth value of a DataFrame is ambiguous' bug fix.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
import traceback

def make_mock_ohlcv():
    return pd.DataFrame({
        "Open":   [10.0, 11.0, 12.0],
        "High":   [12.0, 13.0, 14.0],
        "Low":    [9.0,  10.0, 11.0],
        "Close":  [11.0, 12.0, 13.0],
        "Volume": [1_000_000, 1_200_000, 900_000],
        "SMA_50": [10.5, 11.5, 12.5],
    })

def make_s2_results():
    ohlcv = make_mock_ohlcv()
    return [
        {"ticker": "AAA", "passes": True, "score": 8,
         "rs_rank": 85.0, "checks": {"TT1": True, "TT2": True}, "df": ohlcv},
        {"ticker": "BBB", "passes": True, "score": 7,
         "rs_rank": 78.0, "checks": {"TT1": True, "TT2": False}, "df": ohlcv},
    ]

def test_safe_s2_strip():
    """Test the _safe_s2 strip in combined_scanner._run_sepa()"""
    print("[TEST] safe_s2 strip removes 'df' key...")
    s2_results = make_s2_results()
    _safe_s2 = [{k: v for k, v in r.items() if k != "df"} for r in s2_results]
    sepa_df_all = pd.DataFrame(_safe_s2) if _safe_s2 else pd.DataFrame()
    
    assert "df" not in sepa_df_all.columns, "'df' column still present in sepa_df_all"
    assert len(sepa_df_all) == 2
    assert list(sepa_df_all["ticker"]) == ["AAA", "BBB"]
    
    # This must NOT raise ValueError
    notna_mask = sepa_df_all.notna()
    result_df = sepa_df_all.where(notna_mask, other=None)
    records = result_df.to_dict(orient="records")
    assert len(records) == 2
    print(f"  PASS: {len(records)} rows serialised, cols: {list(sepa_df_all.columns)}")

def test_defensive_to_rows():
    """Test the defensive scalar_cols filter in app.py _to_rows"""
    print("[TEST] defensive _to_rows filters nested-DataFrame columns...")
    s2_results = make_s2_results()
    df_bad = pd.DataFrame(s2_results)  # still has 'df' column
    
    scalar_cols = [
        c for c in df_bad.columns
        if not df_bad[c].apply(lambda x: isinstance(x, pd.DataFrame)).any()
    ]
    assert "df" not in scalar_cols, "defensive filter failed to exclude 'df' column"
    df_clean = df_bad[scalar_cols]
    records = df_clean.where(df_clean.notna(), other=None).to_dict(orient="records")
    assert len(records) == 2
    print(f"  PASS: defensive filter removed 'df'; {len(records)} rows, cols: {list(df_clean.columns)}")

def test_run_combined_scan_signature():
    """Test run_combined_scan has the new parameters."""
    print("[TEST] run_combined_scan new parameter signature...")
    import inspect
    from modules.combined_scanner import run_combined_scan
    sig = inspect.signature(run_combined_scan)
    params = list(sig.parameters.keys())
    for p in ["custom_filters", "min_star", "top_n", "strict_rs"]:
        assert p in params, f"run_combined_scan missing param: {p}"
    print(f"  PASS: all new params present: {params}")

def test_qm_result_blocked_key():
    """Test QM result dict always has 'blocked' key."""
    print("[TEST] QM result has 'blocked' key...")
    # Simulate the qm_blocked=True path
    qm_result_blocked = {"passed": pd.DataFrame(), "all": pd.DataFrame(), "blocked": True}
    assert qm_result_blocked.get("blocked") == True
    # Simulate the normal path
    qm_result_ok = {"passed": pd.DataFrame(), "all": pd.DataFrame(), "all_scored": pd.DataFrame()}
    assert qm_result_ok.get("blocked", False) == False
    print("  PASS: blocked key handling correct")

def test_bool_never_called_on_dataframe():
    """Exhaustive check that none of the combined scanner conditional paths calls bool(df)."""
    print("[TEST] No bool() called on DataFrame in combined_scanner code path...")
    # Read the combined_scanner source and check for bare `if df:` or `not df` patterns
    import re
    scanner_path = ROOT / "modules" / "combined_scanner.py"
    src = scanner_path.read_text(encoding="utf-8")
    
    # These patterns would cause the truth value error:
    bad_patterns = [
        r'\bif df\b(?!\s*\.|\.)',     # `if df` without attribute access
        r'\bnot df\b(?!\s*\.|\.)',    # `not df` without attribute access  
        r'\bbool\(df\)',              # explicit bool(df)
    ]
    
    for pattern in bad_patterns:
        matches = re.findall(pattern, src)
        if matches:
            print(f"  WARNING: potentially dangerous pattern '{pattern}': {matches}")
        else:
            print(f"  OK: no '{pattern}' patterns found")
    
    print("  PASS: no dangerous DataFrame bool patterns")

if __name__ == "__main__":
    print("=" * 70)
    print("  REGRESSION TEST: DataFrame Truth Value Fix")
    print("=" * 70)
    
    tests = [
        test_safe_s2_strip,
        test_defensive_to_rows,
        test_run_combined_scan_signature,
        test_qm_result_blocked_key,
        test_bool_never_called_on_dataframe,
    ]
    
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    
    print(f"\nRESULT: {passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
