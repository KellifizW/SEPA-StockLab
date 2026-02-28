#!/usr/bin/env python3
"""Debug qm_analyzer module to see what's actually being returned."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Direct import and inspection
from modules.qm_analyzer import _build_trade_plan

# Fake row data
test_row = {
    "adr": 18.55,
    "close": 6.3,
    "sma_10": 6.11,
    "sma_20": 5.5,
    "low": 6.0,
}

result = _build_trade_plan(stars=4.5, row=test_row)

print("Direct function call result:")
print("-" * 50)
for k, v in result.items():
    print(f"{k}: {v!r} (type: {type(v).__name__})")
