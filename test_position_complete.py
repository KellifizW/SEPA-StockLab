#!/usr/bin/env python
"""Complete test of position add and display flow."""

import sys
import json
sys.path.insert(0, '.')

from pathlib import Path
from datetime import date

print("\n" + "="*70)
print("POSITION ADD & DISPLAY TEST")
print("="*70 + "\n")

# Step 1: Clear DuckDB if needed to start fresh
print("Step 1: Initialize database...")
try:
    db_file = Path('data/sepa_stock.duckdb')
    if db_file.exists():
        print(f"  Removing existing database: {db_file.name}")
        db_file.unlink()
except Exception as e:
    print(f"  Warning: {e}")

# Step 2: Test add_position
print("\nStep 2: Adding test position...")
try:
    from modules.position_monitor import add_position, _load
    
    add_position(
        ticker='TEST_POS',
        buy_price=100.0,
        shares=10,
        stop_loss=95.0,
        target=120.0,
        note='Test from script'
    )
    print("  ✅ add_position() completed")
except Exception as e:
    print(f"  ❌ Error in add_position: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 3: Verify JSON persistence
print("\nStep 3: Checking JSON persistence...")
try:
    json_file = Path('data/positions.json')
    if json_file.exists():
        data = json.loads(json_file.read_text())
        if 'TEST_POS' in data.get('positions', {}):
            pos = data['positions']['TEST_POS']
            print(f"  ✅ Position found in JSON:")
            print(f"     Keys: {list(pos.keys())}")
            print(f"     buy_price: {pos.get('buy_price')}")
            print(f"     entry_date: {repr(pos.get('entry_date'))}")
        else:
            print(f"  ❌ TEST_POS not found in positions.json")
            print(f"     Available: {list(data.get('positions', {}).keys())}")
    else:
        print(f"  ❌ positions.json not found")
except Exception as e:
    print(f"  ❌ Error reading JSON: {e}")

# Step 4: Test _load()
print("\nStep 4: Testing _load()...")
try:
    from modules.position_monitor import _load
    data = _load()
    if 'TEST_POS' in data.get('positions', {}):
        pos = data['positions']['TEST_POS']
        print(f"  ✅ Position loaded via _load():")
        for k in ['buy_price', 'shares', 'stop_loss', 'target', 'entry_date', 'note']:
            print(f"     {k}: {repr(pos.get(k))}")
    else:
        print(f"  ❌ TEST_POS not found in _load() result")
except Exception as e:
    print(f"  ❌ Error in _load(): {e}")

# Step 5: Test Flask API response (simulate _load_positions from app.py)  
print("\nStep 5: Simulating Flask API response...")
try:
    from modules.position_monitor import _load
    positions = _load().get('positions', {})
    
    # This is what the API would return
    api_response = {
        "ok": True,
        "positions": positions
    }
    
    json_str = json.dumps(api_response, ensure_ascii=False, default=str)
    parsed = json.loads(json_str)
    
    if 'TEST_POS' in parsed.get('positions', {}):
        print(f"  ✅ Position in API response:")
        pos = parsed['positions']['TEST_POS']
        print(f"     Keys: {list(pos.keys())}")
    else:
        print(f"  ❌ Position missing from API response")
        
except Exception as e:
    print(f"  ❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("TEST COMPLETE")
print("="*70 + "\n")
