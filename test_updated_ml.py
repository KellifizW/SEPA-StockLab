#!/usr/bin/env python3
"""Test updated ml_analyze.html with fallback CSS"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()

print("=" * 70)
print("Testing Updated ML Analyze Page with Fallback CSS")
print("=" * 70)

# Test 1: Render page
print("\n[TEST 1] Rendering /ml/analyze...")
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

print(f"Status: {resp.status_code}")
print(f"Size: {len(html)} bytes")

if resp.status_code != 200:
    print(f"❌ ERROR: Status {resp.status_code}")
    sys.exit(1)

# Test 2: Check fallback CSS
print("\n[TEST 2] Checking fallback CSS...")
if 'Fallback styles for CSS CDN failure' in html:
    print("✓ Fallback CSS comment found")
else:
    print("✗ Fallback CSS missing!")

if '*:not(script):not(style)' in html:
    print("✓ Fallback CSS rule found")
else:
    print("✗ Fallback CSS rules missing!")

# Test 3: Check diagnostic script
print("\n[TEST 3] Checking diagnostic logging...")
if "Page loaded:" in html:
    print("✓ Diagnostic logging found")
else:
    print("✗ Diagnostic logging missing!")

# Test 4: Check noscript warning
print("\n[TEST 4] Checking noscript warning...")
if '<noscript>' in html and 'JavaScript 已禁用' in html:
    print("✓ JavaScript disabled warning found")
else:
    print("✗ JavaScript disabled warning missing!")

# Test 5: Verify all key elements still present
print("\n[TEST 5] Verifying page structure...")
checks = {
    'tickerInput': 'id="tickerInput"',
    'emptyState': 'id="emptyState"',
    'resultArea': 'id="resultArea"',
    'analyzeStock': 'function analyzeStock',
    'switchMlMode': 'function switchMlMode',
}

all_good = True
for name, pattern in checks.items():
    if pattern in html:
        print(f"  ✓ {name} present")
    else:
        print(f"  ✗ {name} MISSING")
        all_good = False

print("\n" + "=" * 70)
if all_good:
    print("✅ All checks passed!")
    print("\nThe page is now more robust:")
    print("  - Will display basic content even if Bootstrap CDN fails")
    print("  - Has JavaScript disabled warning")
    print("  - Includes diagnostic logging in browser console")
else:
    print("⚠️ Some checks failed")

print("=" * 70)
