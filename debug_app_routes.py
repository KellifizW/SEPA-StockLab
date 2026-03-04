#!/usr/bin/env python
import os
import sys

os.environ['FLASK_DEBUG'] = '0'

print("Importing app module...")
try:
    import app as app_module
    print("✓ App module imported successfully")
    
    print(f"\nFlask app object: {app_module.app}")
    print(f"Debug mode: {app_module.app.debug}")
    
    # Check all routes
    all_routes = list(app_module.app.url_map.iter_rules())
    print(f"\nTotal routes registered: {len(all_routes)}")
    
    # Show first 10 routes
    print("\nFirst 10 routes:")
    for i, rule in enumerate(all_routes[:10]):
        print(f"  {i+1}. {rule.rule} → {rule.endpoint}")
    
    # Find IBKR routes
    ibkr_routes = [r for r in all_routes if 'ibkr' in r.rule]
    print(f"\nIBKR routes found: {len(ibkr_routes)}")
    if ibkr_routes:
        for rule in ibkr_routes:
            print(f"  ✓ {rule.rule}")
    else:
        print("  ✗ No IBKR routes found!")
        
except Exception as e:
    print(f"✗ Error importing app: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
