#!/usr/bin/env python3
"""
Instrument combined_scanner to catch the exact "truth value" error.
This runs a test combined scan and shows exactly where the error occurs.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import traceback
import pandas as pd
import builtins

# Set up VERY detailed logging
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(
    logging.Formatter('%(asctime)s [%(levelname)s] %(name)s:%(lineno)d | %(message)s')
)
for logger_name in ['modules.combined_scanner', 'modules.screener', 'modules.qm_screener', 
                     'modules.data_pipeline']:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(log_handler)

print("\n" + "="*80)
print("COMBINED SCAN - PRECISE ERROR CAPTURE")
print("="*80 + "\n")

# Patch bool() to catch DataFrame to bool conversions
_orig_bool = builtins.bool
_bool_stack = []

def patched_bool(x):
    """Catch DataFrame/Series bool conversions."""
    if isinstance(x, (pd.DataFrame, pd.Series)):
        tb = ''.join(traceback.format_stack())
        error_msg = f"\n{'='*80}\n🔴 CAUGHT AMBIGUOUS BOOL CONVERSION:\n{'='*80}\n"
        error_msg += f"Type: {type(x).__name__}\n"
        if isinstance(x, pd.DataFrame):
            error_msg += f"Shape: {x.shape}\n"
        else:
            error_msg += f"Length: {len(x)}\n"
        error_msg += f"\nCall Stack:\n{tb}\n"
        error_msg += "="*80 + "\n"
        print(error_msg)
        # Still raise the error so we see the actual exception too
    return _orig_bool(x)

builtins.bool = patched_bool

try:
    print("[*] Importing combined_scanner...")
    from modules.combined_scanner import run_combined_scan
    
    # Test with just 3 tickers to be quick
    test_tickers = ['AAPL', 'MSFT', 'GOOGL']
    
    print(f"[*] Starting combined scan with {test_tickers}...")
    sepa_result, qm_result = run_combined_scan(candidates=test_tickers, verbose=False)
    
    print("\n✓ Scan completed successfully!")
    print(f"  SEPA: {len(sepa_result.get('passed', pd.DataFrame()))} passed")
    print(f"  QM: {len(qm_result.get('passed', pd.DataFrame()))} passed")
    
except Exception as e:
    print("\n" + "="*80)
    print(f"❌ ERROR: {type(e).__name__}")
    print("="*80)
    print(f"Message: {e}\n")
    print("Full traceback:")
    traceback.print_exc()
    
    # Try to extract the exact problematic line
    tb_lines = traceback.format_exc().split('\n')
    for i, line in enumerate(tb_lines):
        if 'truth value' in line.lower() or 'ambiguous' in line.lower():
            print(f"\n🔴 Problem identified at:\n{tb_lines[max(0, i-1):min(len(tb_lines), i+2)]}")
    sys.exit(1)
finally:
    # Restore original bool
    builtins.bool = _orig_bool

print("\n✓ TEST PASSED")
