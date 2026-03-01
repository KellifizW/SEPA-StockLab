#!/usr/bin/env python3
"""Quick validation of ML analyze page"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Test 1: Check for JavaScript issues
print("=" * 70)
print("VALIDATION: ML Analyze Page")
print("=" * 70)

with open(str(ROOT / 'templates' / 'ml_analyze.html'), 'r', encoding='utf-8') as f:
    html = f.read()

print("\n✓ JavaScript Issue Check:")
lines = html.split('\n')
issues = []
for i, line in enumerate(lines, 1):
    if 'event.target.classList.add' in line:
        # Check context - is this in loadIntradayChart function?
        context = '\n'.join(lines[max(0, i-15):i])
        if 'async function loadIntradayChart' in context:
            issues.append(f'  ✗ Line {i}: event.target used in loadIntradayChart (will fail if called programmatically)')

if issues:
    for issue in issues:
        print(issue)
else:
    print("  ✓ No event.target issues in loadIntradayChart")

# Test 2: Check HTML structure
print("\n✓ HTML Structure Check:")
checks = [
    ('Watch panel', 'id="watchPanel"'),
    ('Market status badge', 'id="marketStatusBadge"'),
    ('Intraday chart container', 'id="intradayChartContainer"'),
    ('Mode switch buttons', 'switchMlMode'),
    ('Intraday chart buttons', 'loadIntradayChart'),
    ('Get market status function', 'getMarketStatus()'),
    ('Premarket plan area', 'id="premktPlanArea"'),
    ('Intraday commentary area', 'id="intradayCommentaryArea"'),
]

for name, check_str in checks:
    if check_str in html:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} MISSING")

# Test 3: Check app.py routes
print("\n✓ Backend Routes Check:")
with open(str(ROOT / 'app.py'), 'r', encoding='utf-8', errors='ignore') as f:
    app_code = f.read()

routes = [
    ('/ml/analyze', '@app.route("/ml/analyze")'),
    ('/api/ml/analyze', '@app.route("/api/ml/analyze"'),
    ('/api/chart/intraday', '@app.route("/api/chart/intraday'),
]

for route_name, route_check in routes:
    if route_check in app_code:
        print(f"  ✓ {route_name} route exists")
    else:
        print(f"  ✗ {route_name} route MISSING")

print("\n" + "=" * 70)
print("RESULT")
print("=" * 70)
if not issues:
    print("✓ Page appears to be correctly configured")
    print("\nTo test in browser:")
    print("  1. Start server: python run_app.py")
    print("  2. Open: http://127.0.0.1:5000/ml/analyze")
    print("  3. Enter ticker (e.g. AAPL) and click Analyze")
    print("  4. Click '📡 盯盤模式 Watch Market' button")
else:
    print("✗ Found issues above - please fix before testing")
