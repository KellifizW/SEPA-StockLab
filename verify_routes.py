#!/usr/bin/env python
"""
Verify Flask routes are registered after cache clear
"""
import sys
import os

# Suppress Flask startup messages
os.environ['FLASK_ENV'] = 'development'

print("=" * 70)
print("VERIFYING FLASK ROUTE REGISTRATION")
print("=" * 70)

try:
    print("\n1. Importing app module (fresh import after cache clear)...")
    import app as app_module
    print("   ✓ App imported successfully")
    
    # Get all routes
    all_routes = list(app_module.app.url_map.iter_rules())
    print(f"\n2. Total routes registered: {len(all_routes)}")
    
    # Check for IBKR routes
    ibkr_routes = [r for r in all_routes if 'ibkr' in r.rule]
    print(f"\n3. IBKR routes found: {len(ibkr_routes)}")
    if ibkr_routes:
        print("   IBKR routes:")
        for route in ibkr_routes:
            print(f"     ✓ {route.rule} [{','.join(route.methods)}]")
    else:
        print("   ✗ No IBKR routes registered!")
    
    # Check for HTMX dashboard highlights
    htmx_routes = [r for r in all_routes if 'htmx' in r.rule and 'dashboard' in r.rule]
    print(f"\n4. Dashboard HTMX routes found: {len(htmx_routes)}")
    if htmx_routes:
        print("   Dashboard routes:")
        for route in htmx_routes:
            print(f"     ✓ {route.rule}")
    else:
        print("   ✗ No dashboard HTMX routes registered!")
    
    # Test with Flask test client
    print("\n5. Testing routes with Flask test client...")
    client = app_module.app.test_client()
    
    # Test IBKR status endpoint
    print("\n   a) GET /api/ibkr/status:")
    resp = client.get('/api/ibkr/status')
    print(f"      Status: {resp.status_code}")
    if resp.status_code == 200:
        print(f"      Data: {resp.get_json()}")
    
    # Test HTMX dashboard highlights
    print("\n   b) GET /htmx/dashboard/highlights:")
    resp = client.get('/htmx/dashboard/highlights')
    print(f"      Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"      Error: {resp.get_data(as_text=True)[:200]}")
    else:
        print(f"      ✓ Route working (HTML response)")
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    
except Exception as e:
    print(f"\n✗ Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
