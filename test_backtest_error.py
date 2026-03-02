#!/usr/bin/env python
"""Test backtest with full error capture."""

import sys
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set up logging to see any errors
logging.basicConfig(level=logging.DEBUG)

from modules.qm_backtester import run_qm_backtest

print("Running QM backtest for NVDA...")
try:
    result = run_qm_backtest("NVDA", min_star=3.0, debug_mode=True)
    print(f"✅ Backtest completed successfully!")
    print(f"   Signals: {len(result.get('signals', []))}")
    print(f"   Status: {result.get('ok')}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
