#!/usr/bin/env python3
"""Run stages 1-2 and manually call stage 3 to find crash."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import pandas as pd
import trader_config as C

# Setup logging to be very verbose
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Import ML scan functions
from modules.ml_screener import run_ml_stage1, run_ml_stage2, run_ml_stage3

print("[DEBUG] Starting Stage 1 + Stage 2 only")

try:
    # Stage 1
    print("\n[1] Running Stage 1...")
    candidates = run_ml_stage1()
    print(f"✓ Stage 1: {len(candidates)} candidates")
    
    # Stage 2
    print("\n[2] Running Stage 2...")
    s2_rows = run_ml_stage2(candidates)
    print(f"✓ Stage 2: {len(s2_rows)} passed")
    
    if len(s2_rows) == 0:
        print("[ERROR] No Stage 2 candidates, cannot test Stage 3")
        sys.exit(1)
    
    # Limit to just 5 for testing
    s2_test = s2_rows[:5]
    print(f"\n[3] Testing Stage 3 with just {len(s2_test)} candidates...")
    
    try:
        df_all = run_ml_stage3(s2_test)
        print(f"✓ Stage 3: {len(df_all)} results")
        if not df_all.empty:
            print(f"  Columns: {list(df_all.columns)}")
            print(f"  First row:\n{df_all.iloc[0]}")
    except Exception as e:
        print(f"✗ Stage 3 CRASHED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n[SUCCESS] All stages worked!")
