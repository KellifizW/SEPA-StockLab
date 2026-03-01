#!/usr/bin/env python3
"""
Find all potentially unsafe DataFrame boolean operations in Python modules.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES_DIR = ROOT / "modules"

# Patterns that are SAFE:
# - if df is None
# - if df is not None  
# - if isinstance(df, ...)
# - if hasattr(df, ...)
# - if isinstance(df, ...) and df.empty
# - if df is None or df.empty (already guards with None first)
# - if df is not None and not df.empty
# - if hasattr(df, "empty") and df.empty

# Patterns that are UNSAFE:
# - if df (direct boolean cast)
# - if not df (direct boolean cast)
# - if df and something
# - if something and df
# - if df or something  
# - if something or df
# - and  df.empty (without prior None check)
# - or df.empty (without prior None check)

def check_file(filepath):
    """Check a file for unsafe DataFrame operations."""
    unsafe_found = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines, 1):
        # Skip comments
        if line.strip().startswith('#'):
            continue
            
        # Check for direct df boolean - these are DEFINITELY unsafe
        # Pattern: if df: or if not df: (on variable names commonly used)
        if re.search(r'\bif\s+(not\s+)?(\w+_df|\w+)\b\s*:', line):
            var_match = re.search(r'\bif\s+(not\s+)?(\w+(?:_df|_results|_rows|_passed)?)\b', line)
            if var_match:
                var_name = var_match.group(2)
                # Skip known safe patterns
                if not any(x in line for x in ['is None', 'is not None', 'isinstance', 'hasattr', 'len(', '==']):
                    unsafe_found.append((i, line.strip(), f"Potentially unsafe: `if {var_match.group(1) or ''}{var_name}:`"))
        
        # Check for "and df.empty" without preceding None check
        # This is risky if df might be None
        if re.search(r'and\s+\w+\.empty\b', line):
            # Check if this line already has a preceding is None check
            if 'is not None' not in ''.join(lines[max(0, i-2):i]):
                unsafe_found.append((i, line.strip(), f"Unsafe: `and df.empty` without preceding None check"))
        
        # Check for "or df.empty"  
        if re.search(r'or\s+\w+\.empty\b', line):
            if 'is not None' not in ''.join(lines[max(0, i-2):i]):
                unsafe_found.append((i, line.strip(), f"Unsafe: `or df.empty` without preceding None check"))
    
    return unsafe_found

def main():
    print("\n" + "="*80)
    print("UNSAFE DATAFRAME BOOLEAN OPERATION SCAN")
    print("="*80 + "\n")
    
    all_unsafe = {}
    
    for pyfile in sorted(MODULES_DIR.glob("*.py")):
        unsafe = check_file(pyfile)
        if unsafe:
            all_unsafe[pyfile.name] = unsafe
    
    if not all_unsafe:
        print("✓ No obvious unsafe DataFrame boolean operations found!\n")
        return 0
    
    for filename, issues in sorted(all_unsafe.items()):
        print(f"⚠️  {filename}")
        for line_num, line_text, reason in issues:
            print(f"   Line {line_num}: {reason}")
            print(f"   >> {line_text}")
        print()
    
    print("="*80)
    print(f"Found {sum(len(v) for v in all_unsafe.values())} potential issues")
    print("="*80 + "\n")
    
    return 1 if all_unsafe else 0

if __name__ == '__main__':
    import sys
    sys.exit(main())
