#!/usr/bin/env python3
"""Run a quick ML stage1+stage2 and then try stage3."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
from datetime import datetime
from modules.ml_screener import run_ml_scan

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s [%(levelname)s] %(message)s'
)

print("[DEBUG] Starting mini ML scan test...")
print(f"[DEBUG] Start time: {datetime.now().isoformat()}\n")

try:
    df_passed, df_all = run_ml_scan(
        verbose=True,
        min_star=None,
        top_n=None,
        scanner_mode="standard"
    )
    print(f"\n[DEBUG] SUCCESS: {len(df_passed)} passed, {len(df_all)} total")
except Exception as e:
    print(f"\n[DEBUG] ERROR: {e}")
    import traceback
    traceback.print_exc()
