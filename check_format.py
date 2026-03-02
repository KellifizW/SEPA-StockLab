import re

with open('modules/qm_backtester.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        # Look for f-strings with format specifiers
        if 'f"' in line and re.search(r'f"[^"]*\{[^}]*:[^}]+\}', line):
            print(f"{i}: {line.rstrip()}")
