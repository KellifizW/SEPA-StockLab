#!/usr/bin/env python
"""Diagnostic script to test the backend and database."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

print("=" * 60)
print("SEPA-StockLab Diagnostic Test")
print("=" * 60)

# Test 1: Import modules
print("\n[1] Testing imports...")
try:
    import trader_config as C
    print("  ✓ trader_config imported")
except Exception as e:
    print(f"  ✗ trader_config: {e}")
    sys.exit(1)

try:
    from modules.position_monitor import add_position, _load
    print("  ✓ position_monitor imported")
except Exception as e:
    print(f"  ✗ position_monitor: {e}")
    sys.exit(1)

try:
    if C.DB_ENABLED:
        from modules import db
        print("  ✓ db module imported (DB_ENABLED=True)")
    else:
        print("  ⓘ DB disabled (DB_ENABLED=False)")
except Exception as e:
    print(f"  ✗ db module: {e}")

# Test 2: Load current positions
print("\n[2] Loading current positions...")
try:
    data = _load()
    positions = data.get("positions", {})
    print(f"  ✓ Loaded {len(positions)} positions")
    if positions:
        for ticker, info in list(positions.items())[:3]:  # Show first 3
            print(f"    - {ticker}: {info.get('shares')} shares @ ${info.get('buy_price')}")
except Exception as e:
    print(f"  ✗ Failed to load: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Test add_position
print("\n[3] Testing add_position (adding DIAGTEST)...")
start = time.time()
try:
    add_position(
        "DIAGTEST",
        150.00,
        5,
        140.00,
        165.00,
        "Diagnostic test"
    )
    elapsed = time.time() - start
    print(f"  ✓ add_position succeeded ({elapsed:.2f}s)")
except Exception as e:
    elapsed = time.time() - start
    print(f"  ✗ add_position failed ({elapsed:.2f}s): {e}")
    import traceback
    traceback.print_exc()

# Test 4: Verify save
print("\n[4] Verifying position was saved...")
try:
    data = _load()
    positions = data.get("positions", {})
    if "DIAGTEST" in positions:
        print(f"  ✓ DIAGTEST found in positions")
        print(f"    Data: {positions['DIAGTEST']}")
    else:
        print(f"  ✗ DIAGTEST not found (total: {len(positions)} positions)")
except Exception as e:
    print(f"  ✗ Verification failed: {e}")

# Test 5: Test database directly if enabled
if C.DB_ENABLED:
    print("\n[5] Testing DuckDB...")
    try:
        from modules.db import _get_conn, _ensure_schema
        start = time.time()
        conn = _get_conn()
        elapsed = time.time() - start
        print(f"  ✓ DuckDB connection ({elapsed:.3f}s)")
        
        # Try a simple query
        start = time.time()
        _ensure_schema(conn)
        elapsed = time.time() - start
        print(f"  ✓ Schema check ({elapsed:.3f}s)")
        
        # Check row count
        try:
            result = conn.execute("SELECT COUNT(*) as cnt FROM scan_history").fetchall()
            count = result[0][0] if result else 0
            print(f"  ✓ Scan history: {count} rows")
        except Exception as e:
            print(f"  ⓘ scan_history not available: {e}")
        
        conn.close()
    except Exception as e:
        print(f"  ✗ DuckDB error: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "=" * 60)
print("Test complete!")
print("=" * 60)
