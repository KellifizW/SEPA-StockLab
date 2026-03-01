"""
Deep diagnostic for ML analyze page loading issue.
Tests actual Flask app startup and page rendering.
"""
import sys
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("=" * 70)
print("DEEP DIAGNOSTIC: ML Analyze Page Loading")
print("=" * 70)

# Test 1: Import Flask app
print("\n[1/5] Testing Flask app import...")
try:
    import app as flask_module
    print("  ✓ Flask app imported successfully")
except Exception as e:
    print(f"  ✗ FAILED to import app: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 2: Test /ml/analyze route
print("\n[2/5] Testing /ml/analyze route exists...")
try:
    flask_app = flask_module.app
    routes = [rule.rule for rule in flask_app.url_map.iter_rules()]
    
    if '/ml/analyze' in routes:
        print("  ✓ /ml/analyze route found")
    else:
        print("  ✗ /ml/analyze route NOT found")
        print(f"  Available routes: {[r for r in routes if 'ml' in r]}")
except Exception as e:
    print(f"  ✗ Error checking routes: {e}")
    traceback.print_exc()

# Test 3: Test /api/ml/analyze route
print("\n[3/5] Testing /api/ml/analyze route exists...")
try:
    if '/api/ml/analyze' in routes:
        print("  ✓ /api/ml/analyze route found")
    else:
        print(f"  ✗ /api/ml/analyze NOT found")
except Exception as e:
    print(f"  ✗ Error: {e}")

# Test 4: Test /api/chart/intraday route
print("\n[4/5] Testing /api/chart/intraday route exists...")
try:
    chart_routes = [r for r in routes if 'intraday' in r.lower()]
    if chart_routes:
        print(f"  ✓ Intraday route found: {chart_routes}")
    else:
        print(f"  ✗ Intraday route NOT found")
except Exception as e:
    print(f"  ✗ Error: {e}")

# Test 5: Test template rendering
print("\n[5/5] Testing ml_analyze.html template rendering...")
try:
    from flask import render_template_string
    
    # Try to load the template through Flask
    template_path = ROOT / 'templates' / 'ml_analyze.html'
    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check file size
        print(f"  ✓ Template file exists ({len(content)} bytes)")
        
        # Check for critical content
        checks = {
            'switchMlMode function': 'function switchMlMode',
            'watchPanel div': 'id="watchPanel"',
            'watchTicker element': 'id="watchTicker"',
            'Base template inheritance': 'extends "base.html"',
        }
        
        missing = []
        for name, pattern in checks.items():
            if pattern not in content:
                missing.append(name)
            else:
                print(f"    ✓ {name}")
        
        if missing:
            print(f"  ✗ MISSING: {', '.join(missing)}")
    else:
        print(f"  ✗ Template file not found: {template_path}")
        
except Exception as e:
    print(f"  ✗ Template check failed: {e}")
    traceback.print_exc()

# Test 6: Check if modules can be imported
print("\n[6/5] Testing required module imports...")
try:
    from modules.ml_analyzer import analyze_ml
    print("  ✓ ml_analyzer module imports OK")
except Exception as e:
    print(f"  ✗ ml_analyzer import FAILED: {e}")

print("\n" + "=" * 70)
print("SUMMARY OF ROUTES")
print("=" * 70)
ml_routes = [r for r in routes if '/ml' in r or '/analyze' in r]
for route in sorted(ml_routes):
    print(f"  {route}")

print("\n" + "=" * 70)
print("NEXT STEPS")
print("=" * 70)
print("""
If you see '✗' failures above, please:

Option A - Test server startup:
  1. Kill any running Python: taskkill /F /IM python.exe
  2. Start the app: python run_app.py
  3. Wait for "Running on http://127.0.0.1:5000"
  4. Open browser: http://127.0.0.1:5000/ml/analyze

Option B - Check browser console:
  1. Open page: http://127.0.0.1:5000/ml/analyze
  2. Press F12 to open Developer Tools
  3. Click "Console" tab
  4. Look for red error messages
  5. Share these errors

Option C - Check specific endpoints:
  curl http://127.0.0.1:5000/ml/analyze
  curl -X POST http://127.0.0.1:5000/api/ml/analyze -H "Content-Type: application/json" -d '{"ticker":"AAPL"}'
""")
