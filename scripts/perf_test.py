#!/usr/bin/env python
"""Test position add/close performance after JSON-only optimization."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

print("\n" + "=" * 70)
print("Position Performance Test (JSON-only)")
print("=" * 70)

# Test 1: Import
print("\n[1] Importing modules...")
try:
    from modules.position_monitor import add_position, close_position, _load
    print("  ✓ Modules imported")
except Exception as e:
    print(f"  ✗ Import failed: {e}")
    sys.exit(1)

# Test 2: Add position
print("\n[2] Testing add_position performance...")
ticker = "PERFTEST"
try:
    start = time.time()
    add_position(ticker, 100.00, 5, 95.00, 110.00, "Perf test")
    elapsed = time.time() - start
    status = "✓" if elapsed < 1 else "⚠"
    print(f"  {status} add_position: {elapsed*1000:.1f}ms (target: <1000ms)")
except Exception as e:
    print(f"  ✗ Failed: {e}")
    sys.exit(1)

# Test 3: Load
print("\n[3] Testing _load performance...")
try:
    start = time.time()
    data = _load()
    elapsed = time.time() - start
    status = "✓" if elapsed < 100 else "⚠"
    print(f"  {status} _load: {elapsed*1000:.1f}ms (target: <100ms)")
    
    if ticker in data.get("positions", {}):
        print(f"  ✓ Position {ticker} found in data")
    else:
        print(f"  ✗ Position {ticker} NOT found!")
except Exception as e:
    print(f"  ✗ Failed: {e}")
    sys.exit(1)

# Test 4: Close position
print("\n[4] Testing close_position performance...")
try:
    start = time.time()
    close_position(ticker, 105.00, "Test close")
    elapsed = time.time() - start
    status = "✓" if elapsed < 1 else "⚠"
    print(f"  {status} close_position: {elapsed*1000:.1f}ms (target: <1000ms)")
except Exception as e:
    print(f"  ✗ Failed: {e}")
    sys.exit(1)

# Summary
print("\n" + "=" * 70)
print("✓ Performance test complete!")
print("  Expected API response time: <100ms")
print("=" * 70 + "\n")
