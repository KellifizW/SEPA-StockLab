#!/usr/bin/env python
"""Test Flask app import and template loading."""

import sys
import traceback

try:
    print("1. Importing Flask...") 
    from flask import Flask, render_template, request, jsonify
    print("   OK")
    
    print("2. Importing trader_config...")
    import trader_config as C
    print("   OK")
    
    print("3. Creating Flask app...")
    app = Flask(__name__, template_folder='templates')
    print("   OK")
    
    print("4. Testing template loading...")
    with app.app_context():
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader('templates'))
        
        print("   - Loading dashboard.html...")
        template = env.get_template('dashboard.html')
        print("     OK")
        
        print("   - Loading market.html...")
        template = env.get_template('market.html')
        print("     OK")
        
        print("   - Loading analyze.html...")
        template = env.get_template('analyze.html')
        print("     OK")
    
    print("\n✅  All imports and template loads successful!")
    
except Exception as e:
    print(f"\n❌ Error: {type(e).__name__}")
    print(f"   {str(e)}\n")
    traceback.print_exc()
    sys.exit(1)
