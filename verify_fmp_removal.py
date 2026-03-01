#!/usr/bin/env python3
"""
Verify FMP removal is complete and DataFrame fixes are intact.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def check_file_for_pattern(filepath, pattern, should_exist=True):
    """Check if a pattern exists in a file."""
    if not filepath.exists():
        return False, f"File not found: {filepath}"
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    found = pattern.lower() in content.lower()
    if should_exist:
        return found, f"Pattern '{pattern}' {'found' if found else 'NOT FOUND'}"
    else:
        return not found, f"Pattern '{pattern}' {'NOT FOUND (good)' if not found else 'FOUND (should be removed)'}"

def main():
    print("\n" + "="*80)
    print("VERIFY FMP REMOVAL & DATAFRAME FIXES")
    print("="*80 + "\n")
    
    all_pass = True
    
    # ── Check FMP has been removed ────────────────────────────────────────
    print("[FMP REMOVAL CHECKS]")
    
    checks = [
        (ROOT / "templates" / "base.html", "refreshFmpCounter", False, "FMP counter function should be removed"),
        (ROOT / "templates" / "base.html", "fmp-counter-nav", False, "FMP counter HTML element should be removed"),
        (ROOT / "templates" / "base.html", "/api/fmp/stats", False, "FMP stats fetch should be removed"),
        (ROOT / "templates" / "combined_scan.html", "Financial Modeling Prep", False, "FMP reference in tooltip should be removed"),
        (ROOT / "templates" / "combined_scan.html", "yfinance/FMP", False, "FMP hint should be removed"),
        (ROOT / "app.py", "@app.route(\"/api/fmp/stats\"", False, "FMP endpoint route should be removed"),
        (ROOT / "app.py", "def api_fmp_stats", False, "FMP endpoint function should be removed"),
    ]
    
    for filepath, pattern, should_exist, desc in checks:
        passed, msg = check_file_for_pattern(filepath, pattern, should_exist)
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {desc}")
        if not passed:
            print(f"         {msg}")
            all_pass = False
    
    # ── Check DataFrame fixes are STILL in place ───────────────────────────
    print("\n[DATAFRAME FIX CHECKS]")
    
    df_checks = [
        (ROOT / "modules" / "combined_scanner.py", "isinstance(s2_results, pd.DataFrame) and s2_results.empty", True, "SEPA S2 safe empty check should exist"),
        (ROOT / "modules" / "combined_scanner.py", "isinstance(s2_passed, pd.DataFrame) and s2_passed.empty", True, "QM S2 safe empty check should exist"),
        (ROOT / "app.py", "def _sanitize_for_json", True, "JSON sanitization function should exist"),
        (ROOT / "app.py", "_sanitize_for_json(result)", True, "JSON sanitization should be called in responses"),
    ]
    
    for filepath, pattern, should_exist, desc in df_checks:
        passed, msg = check_file_for_pattern(filepath, pattern, should_exist)
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {desc}")
        if not passed:
            print(f"         {msg}")
            all_pass = False
    
    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "="*80)
    if all_pass:
        print("✓ ALL CHECKS PASSED - FMP completely removed and DataFrame fixes intact")
        print("="*80 + "\n")
        return 0
    else:
        print("✗ SOME CHECKS FAILED - See above for details")
        print("="*80 + "\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
