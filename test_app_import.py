#!/usr/bin/env python
"""
Test app module import with detailed error reporting
"""
import sys
import traceback

print("=" * 70)
print("TESTING APP MODULE IMPORT")
print("=" * 70)

try:
    print("\nImporting app module...")
    import app
    print("✓ App module imported successfully")
    
    # Try to import ibkr_client separately
    print("\nImporting ibkr_client...")
    from modules import ibkr_client
    print("✓ ibkr_client imported successfully")
    
    # Check if the IBKR routes work
    print("\nTesting IBKR route functions...")
    
    # Test api_ibkr_status
    if hasattr(app, 'api_ibkr_status'):
        print("✓ api_ibkr_status function exists")
    else:
        print("✗ api_ibkr_status function NOT found")
        
    # Test api_ibkr_connect
    if hasattr(app, 'api_ibkr_connect'):
        print("✓ api_ibkr_connect function exists")
    else:
        print("✗ api_ibkr_connect function NOT found")
    
    # List all IBKR-related items in app module
    print("\nAll IBKR-related items in app module:")
    ibkr_items = [name for name in dir(app) if 'ibkr' in name.lower()]
    if ibkr_items:
        for name in ibkr_items:
            print(f"  - {name}")
    else:
        print("  (none found)")
    
    # Check Flask app routes
    print("\nChecking Flask app routes...")
    routes = list(app.app.url_map.iter_rules())
    ibkr_routes = [r for r in routes if 'ibkr' in r.rule]
    print(f"  Total IBKR routes: {len(ibkr_routes)}")
    if ibkr_routes:
        for r in ibkr_routes[:3]:
            print(f"    - {r.rule}")
    else:
        print("    (no IBKR routes registered)")
        
except Exception as e:
    print(f"\n✗ ERROR: {type(e).__name__}: {e}")
    print("\nFull traceback:")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
