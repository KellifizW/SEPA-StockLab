#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple DataFrame safety checker - checks for dangerous patterns
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def check_dangerous_patterns():
    """Check for the most critical DataFrame boolean patterns."""
    patterns = [
        (r'\bif\s+(\w+_df)\s*:', 'Direct DataFrame bool cast'),
        (r'(\w+_df)\s+or\s+', 'Unsafe OR with DataFrame'),
        (r'return\s+(\w+_df)\s+or\s+', 'Return with OR DataFrame'),
    ]
    
    issues = []
    modules_dir = ROOT / "modules"
    
    for py_file in sorted(modules_dir.glob("*.py")):
        with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                if '#' in line:
                    line = line.split('#')[0]
                
                for pattern, desc in patterns:
                    if re.search(pattern, line):
                        if 'is None' not in line and 'isinstance' not in line:
                            issues.append((py_file.name, line_num, desc, line.strip()))
    
    return issues

def main():
    print("\nDataFrame Safety Check\n" + "="*50)
    
    issues = check_dangerous_patterns()
    
    if not issues:
        print("OK: No obvious unsafe DataFrame patterns found\n")
        return 0
    
    print(f"FOUND {len(issues)} potential issue(s):\n")
    
    for filename, line_num, desc, line_text in issues:
        print(f"  {filename}:{line_num}")
        print(f"    {desc}")
        print(f"    > {line_text[:70]}\n")
    
    return 1 if any('Return' in d for _, _, d, _ in issues) else 0

if __name__ == '__main__':
    sys.exit(main())
