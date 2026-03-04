#!/usr/bin/env python3
import re
from pathlib import Path

html_file = Path('test_page.html')
if not html_file.exists():
    print("ERROR: test_page.html not found")
    exit(1)

with open(html_file, 'r', encoding='utf-8') as f:
    html = f.read()

# Find ALL scripts (not just the first one)
script_blocks = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
print(f"Found {len(script_blocks)} script block(s)\n")

all_script = ''.join(script_blocks)

# Find functions
funcs = re.findall(r'(?:async\s+)?function\s+(\w+)\s*\(', all_script)
print(f"All functions found:")
for f in sorted(set(funcs)):
    print(f"  - {f}")

# Check required
required = ['toggleCurrency', 'editAccountSize', 'saveAccountSize']
print(f"\nRequired functions:")
for func_name in required:
    if func_name in funcs:
        print(f"  ✓ {func_name}")
    else:
        print(f"  ❌ {func_name} NOT found")

# Check onclick handlers
handlers = re.findall(r'onclick="([^"]+)"', html)
print(f"\nonclick handlers: {sorted(set(handlers))}")
