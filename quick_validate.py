#!/usr/bin/env python3
import re
from pathlib import Path

html_file = Path('test_page.html')
if not html_file.exists():
    print("ERROR: test_page.html not found")
    exit(1)

with open(html_file, 'r', encoding='utf-8') as f:
    html = f.read()

# Find script
script_match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
if not script_match:
    print("ERROR: No script found")
    exit(1)

script = script_match.group(1)

# Find functions
funcs = re.findall(r'(?:async\s+)?function\s+(\w+)\s*\(', script)
print(f"Functions found: {funcs}")

# Check required
required = {'toggleCurrency', 'editAccountSize', 'saveAccountSize'}
found = set(funcs)
missing = required - found

if missing:
    print(f"❌ Missing functions: {missing}")
else:
    print(f"✓ All required functions found")

# Check onclick handlers
handlers = re.findall(r'onclick="([^"]+)"', html)
print(f"\nonclick handlers: {set(handlers)}")

# Check if handlers are defined
for handler in set(handlers):
    func_name = handler.split('(')[0].strip()
    if func_name in found:
        print(f"✓ {func_name} is defined")
    else:
        print(f"❌ {func_name} NOT defined")
