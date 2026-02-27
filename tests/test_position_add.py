#!/usr/bin/env python
"""Quick test of position add API."""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Test position_monitor directly
from modules.position_monitor import add_position, _load

print("Testing position add...")

# Try adding a test position
try:
    add_position(
        "TEST",
        100.00,
        10,
        95.00,
        110.00,
        "Test position"
    )
    print("✓ add_position succeeded")
except Exception as e:
    print(f"✗ add_position failed: {e}")
    import traceback
    traceback.print_exc()

# Check if it was saved
try:
    data = _load()
    positions = data.get("positions", {})
    if "TEST" in positions:
        print(f"✓ Position saved: {positions['TEST']}")
    else:
        print("✗ Position not found in saved data")
except Exception as e:
    print(f"✗ Error loading positions: {e}")
    import traceback
    traceback.print_exc()
