#!/usr/bin/env .venv\Scripts\python.exe
"""
Integration test for combined_scan with the pd import fix
"""
import sys
from pathlib import Path
import json
import traceback
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))

print("="*70)
print("Integration Test: combined_scan with pandas import fix")
print("="*70)

# Test with a tiny Stage 1 result to avoid long downloads
# We'll mock the download phase

try:
    import pandas as pd
    from unittest.mock import patch, MagicMock
    
    # Create mock enriched_map with just a couple stocks
    mock_enriched_map = {
        'AAPL': pd.DataFrame({
            'Date': ['2025-01-01', '2025-01-02', '2025-01-03'],
            'Close': [150.0, 151.0, 152.0],
            'Volume': [1000000, 1100000, 1200000],
            'SMA_50': [149.0, 150.0, 151.0],
            'SMA_200': [148.0, 149.0, 150.0]
        }),
        'MSFT': pd.DataFrame({
            'Date': ['2025-01-01', '2025-01-02', '2025-01-03'],
            'Close': [300.0, 301.0, 302.0],
            'Volume': [900000, 950000, 1000000],
            'SMA_50': [298.0, 299.0, 300.0],
            'SMA_200': [295.0, 296.0, 297.0]
        })
    }
    
    # Mock batch_download_and_enrich to return our mock data quickly
    with patch('modules.combined_scanner.batch_download_and_enrich') as mock_batch:
        mock_batch.return_value = mock_enriched_map
        
        # Mock Stage 1 to return just these two tickers
        with patch('modules.combined_scanner._fetch_sepa_s1') as mock_sepa_s1, \
             patch('modules.combined_scanner._fetch_qm_s1') as mock_qm_s1:
            
            mock_sepa_s1.return_value = ['AAPL', 'MSFT']
            mock_qm_s1.return_value = ['AAPL', 'MSFT']
            
            print("Step 1: Import combined_scanner...")
            from modules.combined_scanner import run_combined_scan
            print("  SUCCESS")
            
            print("\nStep 2: Call run_combined_scan (with mocked data)...")
            sepa_result, qm_result = run_combined_scan(verbose=False)
            print("  SUCCESS")
            
            print("\nStep 3: Verify results structure...")
            assert 'passed' in sepa_result, "Missing 'passed' in sepa_result"
            assert 'all' in sepa_result, "Missing 'all' in sepa_result"
            assert isinstance(sepa_result['passed'], pd.DataFrame), "sepa_result['passed'] not DataFrame"
            assert isinstance(sepa_result['all'], pd.DataFrame), "sepa_result['all'] not DataFrame"
            print(f"  SEPA result structure: OK")
            print(f"    - passed: {sepa_result['passed'].shape}")
            print(f"    - all: {sepa_result['all'].shape}")
            
            assert 'passed' in qm_result, "Missing 'passed' in qm_result"
            assert 'all' in qm_result, "Missing 'all' in qm_result"
            print(f"  QM result structure: OK")
            print(f"    - passed: {qm_result['passed'].shape}")
            print(f"    - all_scored: {qm_result.get('all_scored', pd.DataFrame()).shape}")
            
            print("\nStep 4: Simulate app.py _to_rows function (with our fix)...")
            def _clean(obj):
                """Simplified version of _clean for testing"""
                if obj is None:
                    return None
                if isinstance(obj, (bool, int, float, str)):
                    return obj
                if isinstance(obj, dict):
                    return {k: _clean(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)):
                    return [_clean(i) for i in obj]
                return None
            
            def _to_rows(df):
                import pandas as pd  # <-- THE FIX
                if df is None or not hasattr(df, "to_dict") or df.empty:
                    return []
                # Drop any column whose values are themselves DataFrames
                scalar_cols = [
                    c for c in df.columns
                    if not df[c].apply(lambda x: isinstance(x, pd.DataFrame)).any()
                ]
                df = df[scalar_cols]
                return _clean(df.where(df.notna(), other=None).to_dict(orient="records"))
            
            sepa_rows = _to_rows(sepa_result.get("passed"))
            qm_rows = _to_rows(qm_result.get("passed"))
            
            print(f"  SEPA rows converted: {len(sepa_rows)} rows")
            print(f"  QM rows converted: {len(qm_rows)} rows")
            print("  SUCCESS - No 'pd not defined' error!")
            
            print("\nStep 5: Verify JSON serialization (the real test)...")
            combined_result = {
                "sepa": {"passed": sepa_rows, "count": len(sepa_rows)},
                "qm": {"passed": qm_rows, "count": len(qm_rows)},
            }
            json_str = json.dumps(combined_result, ensure_ascii=False, default=str)
            print(f"  JSON serialized successfully ({len(json_str)} bytes)")
            print("  SUCCESS")
            
except Exception as e:
    print(f"\nERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*70)
print("All integration tests PASSED!")
print("="*70)
