#!/usr/bin/env python3
"""
Comprehensive test and fix for dashboard.html JavaScript functionality
"""

import re
import json
from pathlib import Path

# Read the rendered dashboard.html
html_file = Path('test_page.html')  # The file we downloaded from Flask
if not html_file.exists():
    print("ERROR: test_page.html not found. Please run: curl -s http://127.0.0.1:5000/ -o test_page.html")
    exit(1)

with open(html_file, 'r', encoding='utf-8') as f:
    html_content = f.read()

print("="*70)
print("DASHBOARD.HTML JAVASCRIPT VALIDATION REPORT")
print("="*70)

# 1. Extract script
script_match = re.search(r'<script>(.*?)</script>', html_content, re.DOTALL)
if not script_match:
    print("\n❌ CRITICAL: No <script> tag found!")
    exit(1)

script = script_match.group(1)
print(f"\n✓ Found script block ({len(script)} characters, {len(script.splitlines())} lines)")

# 2. Find function definitions
functions = re.findall(r'((?:async\s+)?function\s+(\w+)\s*\()', script)
print(f"\n✓ Found {len(functions)} functions:")
for func_def, func_name in functions:
    print(f"     {func_def.strip()}")

#3. Check required functions
required = ['toggleCurrency', 'editAccountSize', 'saveAccountSize']
for func_name in required:
    if f'function {func_name}' in script or f'function {func_name}' in script:
        print(f"  ✓ {func_name} defined")
    else:
        print(f"  ❌ {func_name} NOT defined")

# 4. Check onclick attributes
print(f"\n✓ Checking onclick attributes:")
onclick_matches = re.findall(r'onclick="([^"]+)"', html_content)
for i, onclick in enumerate(set(onclick_matches), 1):
    print(f"     {onclick}")

# 5. Character balance
print(f"\n✓ Character balance in script:")
open_parens = script.count('(')
close_parens = script.count(')')
open_braces = script.count('{')
close_braces = script.count('}')

print(f"     Parentheses: {open_parens} open, {close_parens} close - {'✓' if open_parens == close_parens else '❌'}")
print(f"     Braces: {open_braces} open, {close_braces} close - {'✓' if open_braces == close_braces else '❌'}")

# 6. Check for common errors
print(f"\n✓ Checking for common JavaScript errors:")

# Double checks
if '}}' in script:
    print(f"     ⚠️  Found '}}' - may be Jinja template variable")
    
# Unmatched quotes
single_quotes = script.count("'") - script.count("\\'")
double_quotes = script.count('"') - script.count('\\"')
print(f"     Single quotes: {single_quotes} ({'✓ balanced' if single_quotes % 2 == 0 else '❌ unbalanced'})")
print(f"     Double quotes: {double_quotes} ({'✓ balanced' if double_quotes % 2 == 0 else '❌ unbalanced'})")

# 7. Test functions existence in onclick handlers
print(f"\n✓ Validating onclick handlers:")
handlers_ok = True
for handler in set(onclick_matches):
    # Extract function name
    func_name = handler.split('(')[0].strip()
    if f'function {func_name}' in script or f' {func_name} ' in script:
        print(f"     ✓ {func_name} is defined")
    else:
        print(f"     ❌ {func_name} is NOT defined")
        handlers_ok = False

print(f"\n{'='*70}")
if handlers_ok and open_parens == close_parens and open_braces == close_braces:
    print("✅ HTML/JS VALIDATION PASSED")
else:
    print("❌ HTML/JS VALIDATION FAILED - See above for issues")
print(f"{'='*70}")
