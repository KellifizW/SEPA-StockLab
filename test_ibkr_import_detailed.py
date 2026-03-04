#!/usr/bin/env python
"""
Test IBKR route registration with detailed error handling
"""
import sys
import os

# Suppress Flask startup messages
os.environ['WERKZEUG_RUN_MAIN'] = 'true'

print("Step 1: Importing Flask...")
try:
    from flask import Flask
    print("  ✓ Flask imported")
except Exception as e:
    print(f"  ✗ Flask import failed: {e}")
    sys.exit(1)

print("\nStep 2: Creating Flask app instance...")
try:
    app = Flask(__name__)
    print("  ✓ Flask app created")
    print(f"  App object: {app}")
except Exception as e:
    print(f"  ✗ Flask app creation failed: {e}")
    sys.exit(1)

print("\nStep 3: Importing trader_config...")
try:
    import trader_config as C
    print(f"  ✓ trader_config imported")
    print(f"  IBKR_ENABLED: {C.IBKR_ENABLED}")
except Exception as e:
    print(f"  ✗ trader_config import failed: {e}")
    sys.exit(1)

print("\nStep 4: Testing IBKR module import...")
try:
    from modules import ibkr_client
    print("  ✓ ibkr_client imported")
    print("  Available functions: get_status, connect, disconnect")
except Exception as e:
    print(f"  ✗ ibkr_client import failed: {e}")
    import traceback
    traceback.print_exc()

print("\nStep 5: Now importing full app module...")
try:
    import app as app_module
    print("  ✓ app module imported")
    
    # Count routes
    routes = list(app_module.app.url_map.iter_rules())
    ibkr_routes = [r for r in routes if 'ibkr' in r.rule]
    
    print(f"\n  Total routes: {len(routes)}")
    print(f"  IBKR routes: {len(ibkr_routes)}")
    
    if ibkr_routes:
        print("  IBKR routes registered:")
        for r in ibkr_routes:
            print(f"    - {r.rule} ({','.join(r.methods)})")
    else:
        print("  ✗ No IBKR routes found!")
        
except Exception as e:
    print(f"  ✗ app import failed: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
