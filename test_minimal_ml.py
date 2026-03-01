#!/usr/bin/env python3
"""Test minimal ml_analyze page"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()

print("=" * 70)
print("Testing Minimal ML Analyze Page")
print("=" * 70)

# Test minimal page
print("\n[TEST 1] GET /ml/test-minimal")
resp = client.get('/ml/test-minimal')
print(f"Status: {resp.status_code}")
print(f"Size: {len(resp.data)} bytes")

if resp.status_code == 200:
    html = resp.data.decode('utf-8')
    if '✅ 页面加载成功' in html or 'Flask' in html:
        print("✓ Minimal page renders successfully")
        print("\nContent preview:")
        lines = html.split('\n')
        # Find body section
        for i, line in enumerate(lines):
            if '<h1>' in line:
                print(f"  Found: {line.strip()}")
    else:
        print("✗ Minimal page renders but content missing")
        print(f"Snippet: {html[500:700]}")
else:
    print(f"✗ Error: Status {resp.status_code}")

# Test original page
print("\n[TEST 2] GET /ml/analyze (original)")
resp = client.get('/ml/analyze')
print(f"Status: {resp.status_code}")
print(f"Size: {len(resp.data)} bytes")

if resp.status_code == 200:
    html = resp.data.decode('utf-8')
    # Check for key elements
    has_ticker = 'id="tickerInput"' in html
    has_navbar = '<nav' in html
    has_empty_state = 'id="emptyState"' in html
    
    print(f"  Has navbar: {'✓' if has_navbar else '✗'}")
    print(f"  Has ticker input: {'✓' if has_ticker else '✗'}")
    print(f"  Has empty state: {'✓' if has_empty_state else '✗'}")
else:
    print(f"✗ Error: Status {resp.status_code}")

print("\n" + "=" * 70)
print("Next steps:")
print("1. Open http://127.0.0.1:5000/ml/test-minimal in browser")
print("   (This should show a white page with 'page loaded successfully')")
print("2. Open http://127.0.0.1:5000/ml/analyze in browser")
print("   (If this is blank but #1 shows content, then the issue is CSS/JS)")
print("=" * 70)
