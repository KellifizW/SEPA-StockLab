import sys
import os
import logging

# Enable verbose logging
logging.basicConfig(level=logging.DEBUG)

# Attempt to import app with exception handling
try:
    print("Starting app import...")
    import app
    print("✓ App imported successfully")
    
    # Try to manually access one of the routes
    print("\nChecking manually for IBKR routes in code...")
    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()
        if '@app.route("/api/ibkr/status"' in content:
            print("✓ Found @app.route(\"/api/ibkr/status\") in source code")
        else:
            print("✗ @app.route(\"/api/ibkr/status\") NOT FOUND in source code")
            
        if 'def api_ibkr_status' in content:
            print("✓ Found function 'api_ibkr_status' in source code")
        else:
            print("✗ Function 'api_ibkr_status' NOT FOUND in source code")
    
    # Check Flask app routes
    print(f"\nFlask app has {len(list(app.app.url_map.iter_rules()))} total routes")
    
    ibkr_routes = [r for r in app.app.url_map.iter_rules() if 'ibkr' in r.rule]
    print(f"IBKR-specific routes registered: {len(ibkr_routes)}")
    
    for route in ibkr_routes:
        print(f"  - {route.rule}")
        
except Exception as e:
    import traceback
    print(f"✗ Error: {type(e).__name__}: {e}")
    traceback.print_exc()
