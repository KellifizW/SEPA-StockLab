#!/usr/bin/env python3
"""
Test: Why is /ml/analyze blank?
This script opens the page in the browser and checks what's visible
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Start Flask test client
from flask import Flask
from app import app, render_template

print("=" * 70)
print("ML Analyze Page Blank Test")
print("=" * 70)

client = app.test_client()

# Test 1: Fetch the page
print("\n[TEST 1] Fetching /ml/analyze...")
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')
print(f"Status: {resp.status_code}")
print(f"HTML size: {len(html)} bytes")

# Test 2: Check if key elements are present
print("\n[TEST 2] Checking HTML structure...")
checks = [
    ("<!DOCTYPE html>", "✓ DOCTYPE present"),
    ("<nav", "✓ Navbar present"),
    ("container-fluid", "✓ Container-fluid present"),
    ("id=\"tickerInput\"", "✓ Ticker input present"),
    ("id=\"emptyState\"", "✓ Empty state present"),
    ("id=\"resultArea\"", "✓ Result area present"),
    ("id=\"reviewPanel\"", "✓ Review panel present"),
    ("id=\"watchPanel\"", "✓ Watch panel present"),
    ("analyzeStock", "✓ analyzeStock function present"),
    ("switchMlMode", "✓ switchMlMode function present"),
    ("loadIntradayChart", "✓ loadIntradayChart function present"),
    ("bootstrap.bundle.min.js", "✓ Bootstrap JS CDN present"),
    ("lightweight-charts", "✓ LightweightCharts CDN present"),
]

for check_str, msg in checks:
    if check_str in html:
        print(f"  {msg}")
    else:
        print(f"  ✗ {check_str} MISSING")

# Test 3: Extract and analyze CSS+JS
print("\n[TEST 3] Analyzing CSS and JavaScript...")
import re

# Find all <style> blocks
styles = re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL)
print(f"  Found {len(styles)} <style> blocks")

# Find CSS rules that might hide content
problematic_css = [
    ('display:none', 'display: none rule'),
    ('display: none', 'display: none rule'),
    ('visibility:hidden', 'visibility: hidden rule'),
    ('opacity:0', 'opacity:0 rule'),
]

print("  Checking for CSS rules that hide content...")
style_text = ' '.join(styles)
for css_pattern, desc in problematic_css:
    if css_pattern in style_text:
        print(f"    ⚠️ Found {desc}")

# Test 4: Check if there are any console errors indicated in HTML
print("\n[TEST 4] Checking for error indicators...")
if "console.error" in html:
    print("  ℹ️ Script includes error handlers")

# Test 5: Extract body content (excluding scripts)
print("\n[TEST 5] Checking body content visibility...")
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
if body_match:
    body_content = body_match.group(1)
    # Remove scripts to see pure HTML
    pure_body = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
    
    # Check structure
    nav_count = len(re.findall(r'<nav', pure_body))
    div_count = len(re.findall(r'<div', pure_body))
    heading_count = len(re.findall(r'<h[1-6]', pure_body))
    
    print(f"  Body structure:")
    print(f"    - <nav> tags: {nav_count}")
    print(f"    - <div> tags: {div_count}")
    print(f"    - <h1-h6> tags: {heading_count}")
    
    # Look for visible regions
    if 'tickerInput' in body_content:
        print(f"  ✓ Ticker input region found")
    if 'emptyState' in body_content or 'Empty state' in body_content:
        print(f"  ✓ Empty state content found")

# Test 6: Check for CSS that might affect container-fluid visibility
print("\n[TEST 6] Checking container-fluid CSS...")
container_styles = re.findall(r'\.container-fluid\s*\{([^}]*)\}', style_text)
if container_styles:
    print(f"  Found {len(container_styles)} container-fluid style rules")
    for style in container_styles:
        if 'display:none' in style or 'display: none' in style:
            print(f"  ✗ CRITICAL: container-fluid is hidden!")
        else:
            print(f"  ✓ Style: {style[:60]}...")
else:
    print("  Note: No specific container-fluid CSS found (uses Bootstrap)")

# Test 7: Look for d-none class usage
print("\n[TEST 7] Checking initial visibility (d-none)...")
result_area_match = re.search(r'id="resultArea"[^>]*class="([^"]*)"', html)
empty_state_match = re.search(r'id="emptyState"[^>]*class="([^"]*)"', html)

if result_area_match:
    result_classes = result_area_match.group(1)
    print(f"  resultArea classes: {result_classes}")
    if 'd-none' in result_classes:
        print(f"    ✓ resultArea is initially hidden (d-none)")
    else:
        print(f"    ⚠️ resultArea is initially visible")

if empty_state_match:
    empty_classes = empty_state_match.group(1)
    print(f"  emptyState classes: {empty_classes}")
    if empty_classes == '""' or empty_classes == '':
        print(f"    ✓ emptyState is initially visible (empty class)")
    elif 'd-none' in empty_classes:
        print(f"    ✗ emptyState is initially hidden!")
    else:
        print(f"    Classes: {empty_classes}")

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)

# Save HTML to file for manual inspection
output_file = ROOT / 'ml_analyze_blank_test.html'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"\nFull HTML saved to: {output_file}")
print("\nTo debug further:")
print("1. Open the above HTML file in a browser")
print("2. Press F12 to open DevTools")
print("3. Check Console tab for JavaScript errors")
print("4. Check Elements tab for CSS visibility issues")
print("5. Check Network tab for failed CDN resources")
