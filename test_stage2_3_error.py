#!/usr/bin/env python3
"""
Precise error capture for combined scan "Stage 2-3" error.
Adds detailed exception info to pinpoint exact line causing "truth value is ambiguous".
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import traceback
import pandas as pd

# Set up very detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s:%(lineno)d | %(message)s'
)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("COMBINED SCAN - TRUTH VALUE AMBIGUITY BUG CAPTURE")
print("="*80)

try:
    from modules.combined_scanner import run_combined_scan
    
    # Use a small test set to isolate the issue quickly
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']
    
    logger.info("="*80)
    logger.info("Starting combined scan with %d test tickers", len(test_tickers))
    logger.info("="*80)
    
    # Patch the Boolean check operations to capture the exact error
    original_bool = bool
    original_not = lambda x: not x
    
    def safe_bool(obj):
        """Wrapper to catch ambiguous DataFrame bool() calls."""
        if isinstance(obj, pd.DataFrame):
            logger.error(f"🔴 CAUGHT: bool({type(obj).__name__} shape={obj.shape}) - This will fail!")
            raise ValueError(f"Cannot convert DataFrame to bool. Use df.empty, df.bool(), etc.")
        if isinstance(obj, pd.Series):
            logger.error(f"🔴 CAUGHT: bool({type(obj).__name__} len={len(obj)}) - This will fail!")
            raise ValueError(f"Cannot convert Series to bool. Use s.empty, s.item(), etc.")
        return original_bool(obj)
    
    # Don't override builtins globally - instead, let's add instrumentation to combined_scanner
    
    logger.info("\nCalling run_combined_scan()...")
    sepa_result, qm_result = run_combined_scan(
        candidates=test_tickers,
        verbose=True
    )
    
    logger.info("✓ Scan completed successfully!")
    logger.info("SEPA passed: %d", len(sepa_result.get("passed", pd.DataFrame())))
    logger.info("QM passed: %d", len(qm_result.get("passed", pd.DataFrame())))
    
except Exception as e:
    logger.error("\n" + "="*80)
    logger.error("❌ ERROR CAUGHT")
    logger.error("="*80)
    logger.error("Error type: %s", type(e).__name__)
    logger.error("Error message: %s", str(e))
    logger.error("\nFull traceback:")
    logger.error(traceback.format_exc())
    
    # Parse the traceback to find the culprit line
    tb = traceback.format_exc()
    lines = tb.split('\n')
    for i, line in enumerate(lines):
        if 'truth value' in line.lower() or 'ambiguous' in line.lower():
            logger.error("\n🔴 FOUND THE PROBLEM:")
            logger.error(lines[max(0, i-2):min(len(lines), i+3)])
    
    print(f"\n❌ ERROR: {e}")
    print("\nFull traceback:")
    print(traceback.format_exc())
    sys.exit(1)

print("\n✓ TEST PASSED - No 'truth value is ambiguous' errors!")
