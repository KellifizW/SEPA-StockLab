#!/usr/bin/env python
"""Test DuckDB position operations."""

import sys
sys.path.insert(0, '.')

from datetime import date
from pathlib import Path
import json

print("=" * 60)
print("Testing Position Monitor DuckDB Operations")
print("=" * 60 + "\n")

# Test 1: Add a test position
print("1️⃣ Adding a test position...")
try:
    from modules.position_monitor import add_position, _load
    
    add_position(
        ticker='TEST_NEW',
        buy_price=100.0,
        shares=10,
        stop_loss=95.0,
        target=120.0,
        note='Test position'
    )
    print("✅ Position added via add_position()\n")
    
    # Check what was saved to JSON
    data = _load()
    if 'TEST_NEW' in data.get('positions', {}):
        pos = data['positions']['TEST_NEW']
        print("2️⃣ Data saved to JSON/DuckDB:")
        print(f"   Keys: {list(pos.keys())}")
        for k, v in pos.items():
            print(f"   {k}: {v}")
    else:
        print("❌ Position not found in _load() result!")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
