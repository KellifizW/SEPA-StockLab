#!/usr/bin/env python3
"""
Test: Verify improved Stage 1 can fetch finvizfinance data with patient timeout (45s)
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def test_stage1_with_patient_timeout():
    """Test Stage 1 with the new patient timeout mechanism."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-8s %(message)s'
    )
    
    print("\n" + "="*80)
    print("TEST: Stage 1 with Patient Timeout (45s + 20s patience)")
    print("="*80)
    
    from modules.qm_screener import run_qm_stage1
    import time
    
    print("\n▶ Running Stage 1 (may take up to 65 seconds)...\n")
    
    start = time.time()
    tickers = run_qm_stage1(verbose=False)
    elapsed = time.time() - start
    
    print(f"\n✓ Stage 1 completed in {elapsed:.1f} seconds")
    print(f"  Got {len(tickers)} candidates")
    
    if tickers:
        print(f"  First 10 tickers: {tickers[:10]}")
        return True
    else:
        print("  ⚠ No tickers found - may indicate finvizfinance issue")
        return False

if __name__ == "__main__":
    success = test_stage1_with_patient_timeout()
    sys.exit(0 if success else 1)
