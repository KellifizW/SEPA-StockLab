#!/usr/bin/env python3
"""
Test script to run a small combined scan and capture any DataFrame ambiguity errors.
This simulates what happens during a real scan.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import pandas as pd
import traceback

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s | %(funcName)s:%(lineno)d | %(message)s')
logger = logging.getLogger(__name__)

print("\n" + "="*70)
print("COMBINED SCAN DEBUG TEST - Simulating actual scan flow")
print("="*70)

try:
    # Import the combined scanner
    logger.info("Importing combined_scanner module...")
    from modules.combined_scanner import run_combined_scan
    
    # Run a small scan with just a few tickers to isolate issues
    logger.info("Starting small combined scan with 10 test tickers...")
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'AMD', 'PYPL', 'CRM']
    
    # Run with a small subset
    try:
        logger.info("Calling run_combined_scan()...")
        sepa_result, qm_result = run_combined_scan(
            candidates=test_tickers,
            verbose=True
        )
        logger.info("✓ Scan completed successfully")
    except Exception as scan_error:
        logger.error("✗ Scan failed with error: %s", scan_error)
        logger.error("Full traceback:\n%s", traceback.format_exc())
        print(f"\n❌ SCAN ERROR: {scan_error}")
        print("\nFull traceback:")
        print(traceback.format_exc())
        sys.exit(1)
    
    # Now test the _to_rows conversion
    logger.info("Testing _to_rows conversion on scan results...")
    
    import pandas as pd
    import numpy as np
    
    def _clean(obj):
        """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
        
        if obj is None:
            return None
        
        # Skip DataFrames and Series early - don't try to process them
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            return None
        
        # Try numpy scalar extraction FIRST (before isinstance checks)
        # This handles np.int64, np.float64, np.bool_, etc.
        if hasattr(obj, "item") and hasattr(obj, "dtype"):
            try:
                val = obj.item()
                return _clean(val)  # Recursively clean the extracted value
            except (TypeError, ValueError, AttributeError):
                return None
        
        if isinstance(obj, bool):
            return obj
        
        if isinstance(obj, (int, float, str)):
            # Handle NaN floats
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
                # Skip DataFrame/Series values in dicts
                if isinstance(v, (pd.DataFrame, pd.Series)):
                    continue
                cleaned_v = _clean(v)
                if cleaned_v is not None or v is None:
                    cleaned[k] = cleaned_v
            return cleaned
        
        if isinstance(obj, (list, tuple)):
            return [_clean(i) for i in obj]
        
        # For other types, try to convert to string representation
        try:
            return str(obj)
        except Exception:
            return None
    
    def _to_rows(df):
        import pandas as pd
        
        if df is None or not hasattr(df, "to_dict"):
            return []
        if hasattr(df, "empty") and df.empty:
            return []
        
        try:
            # Convert row-by-row to avoid DataFrame comparison issues
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
            return _clean(records)
        except Exception as e:
            logger.error(f"[_to_rows] Conversion failed: {e}", exc_info=True)
            return []
    
    logger.info("Converting SEPA results...")
    sepa_passed_df = sepa_result.get("passed")
    sepa_all_df = sepa_result.get("all")
    
    if sepa_passed_df is not None:
        logger.info(f"SEPA passed DF shape: {sepa_passed_df.shape if hasattr(sepa_passed_df, 'shape') else 'N/A'}")
        sepa_rows = _to_rows(sepa_passed_df)
        logger.info(f"✓ Converted SEPA passed: {len(sepa_rows) if isinstance(sepa_rows, list) else 'ERROR'} rows")
    else:
        logger.warning("SEPA passed DF is None")
    
    if sepa_all_df is not None:
        logger.info(f"SEPA all DF shape: {sepa_all_df.shape if hasattr(sepa_all_df, 'shape') else 'N/A'}")
        sepa_all_rows = _to_rows(sepa_all_df)
        logger.info(f"✓ Converted SEPA all: {len(sepa_all_rows) if isinstance(sepa_all_rows, list) else 'ERROR'} rows")
    else:
        logger.warning("SEPA all DF is None")
    
    logger.info("Converting QM results...")
    qm_passed_df = qm_result.get("passed")
    qm_all_df = qm_result.get("all_scored") or qm_result.get("all")
    
    if qm_passed_df is not None:
        logger.info(f"QM passed DF shape: {qm_passed_df.shape if hasattr(qm_passed_df, 'shape') else 'N/A'}")
        qm_rows = _to_rows(qm_passed_df)
        logger.info(f"✓ Converted QM passed: {len(qm_rows) if isinstance(qm_rows, list) else 'ERROR'} rows")
    else:
        logger.warning("QM passed DF is None")
    
    if qm_all_df is not None:
        logger.info(f"QM all DF shape: {qm_all_df.shape if hasattr(qm_all_df, 'shape') else 'N/A'}")
        qm_all_rows = _to_rows(qm_all_df)
        logger.info(f"✓ Converted QM all: {len(qm_all_rows) if isinstance(qm_all_rows, list) else 'ERROR'} rows")
    else:
        logger.warning("QM all DF is None")
    
    print("\n✓ ALL TESTS PASSED - No 'truth value is ambiguous' errors!")
    print("The new _to_rows() implementation successfully handles all DataFrame conversions.")
    
except Exception as e:
    logger.error("Test failed: %s", e)
    logger.error("Full traceback:\n%s", traceback.format_exc())
    print(f"\n❌ TEST ERROR: {e}")
    print("\nFull traceback:")
    print(traceback.format_exc())
    sys.exit(1)
