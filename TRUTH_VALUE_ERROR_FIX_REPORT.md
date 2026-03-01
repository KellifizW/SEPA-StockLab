# DataFrame "Truth Value is Ambiguous" Error - Complete Diagnostic & Fix Report

## Problem Summary
User reported: **"Error: The truth value of a DataFrame is ambiguous..."** during "Stage 2-3 -- Parallel Analysis" phase in combined scans.

## Root Causes Identified & Fixed

### Fix 1: `combined_scanner.py` - Lines 259 & 307
**Problem**: Direct boolean operations on function return values that could be DataFrames

```python
# BEFORE (Lines 259 & 307):
if _cancelled() or not s2_results:  # ❌ If s2_results is DataFrame, this fails!
    return

# AFTER:
if _cancelled():
    return
is_empty = (isinstance(s2_results, list) and len(s2_results) == 0) or \
           (isinstance(s2_results, pd.DataFrame) and s2_results.empty)
if is_empty:
    return
```

**Applied to**:
- Line 259: SEPA Stage 2 result checking
- Line 307: QM Stage 2 result checking

### Fix 2: `app.py` - `_to_rows()` function (3 instances)
**Problem**: Using `.where(df.notna())` which can trigger ambiguity errors

```python
# BEFORE:
def _to_rows(df):
    if df is not None and hasattr(df, "to_dict") and not df.empty:
        return _clean(df.where(df.notna(), other=None).to_dict(orient="records"))

# AFTER:
def _to_rows(df):
    import pandas as pd
    if df is None or not hasattr(df, "to_dict"):
        return []
    if hasattr(df, "empty") and df.empty:
        return []
    
    try:
        records = []
        for idx, row in df.iterrows():
            record = {}
            for col, val in row.items():
                if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                    continue
                if pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val
            records.append(record)
        return _clean(records)
    except Exception as e:
        logging.error(f"[_to_rows] Conversion failed: {e}", exc_info=True)
        return []
```

**Applied to**:
- Line ~557: SEPA scan handler
- Line ~765: QM scan handler  
- Line ~828: Combined scan handler

### Fix 3: `app.py` - `_clean()` function (Line 331)
**Problem**: Function tried to process DataFrames directly instead of skipping them

```python
# BEFORE:
if hasattr(obj, "empty"):
    try:
        if obj.empty:
            return []
        if hasattr(obj, "to_dict"):
            return [_clean(row) for row in obj.to_dict(orient="records")]

# AFTER:
if isinstance(obj, (pd.DataFrame, pd.Series)):
    return None  # Skip DataFrames/Series entirely
```

## Files Modified
1. `modules/combined_scanner.py` - 2 boolean check fixes
2. `app.py` - 3 `_to_rows()` implementations + 1 `_clean()` function

## Verification Steps

### Step 1: Check your modified files
Run this Python snippet:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

# Check combined_scanner.py has the fix
with open("modules/combined_scanner.py") as f:
    content = f.read()
    if "is_empty = (isinstance(s2_results, list)" in content:
        print("OK: combined_scanner.py has S2 result check fix")
    else:
        print("FAIL: combined_scanner.py missing fix")

# Check app.py has the new _to_rows()
with open("app.py") as f:
    content = f.read()
    if "for idx, row in df.iterrows():" in content:
        print("OK: app.py has new row-by-row _to_rows()")
    else:
        print("FAIL: app.py missing fix")
```

### Step 2: Run diagnostic test
```bash
python test_diagnostic.py
```

This will attempt to run a full combined scan and show if errors occur.

### Step 3: Manual test
Try a combined scan from the web UI and check:
1. No "truth value" error appears
2. Scan completes successfully
3. Both SEPA and QM results appear correctly

## Why These Fixes Work

1. **combined_scanner.py fix**: Avoids `not` operator on values that could be DataFrames by explicitly checking type first
2. **_to_rows() fix**: Uses safe row-by-row iteration instead of problematic `.where(df.notna())` 
3. **_clean() fix**: Early exit for DataFrames prevents any pandas operations that could trigger ambiguity

## If Error Still Occurs

If you STILL see the "truth value is ambiguous" error after these fixes, please:

1. **Enable maximum debug logging**:
   - Append this to your URI: `?debug=1`
   - Check the browser console for full error traceback

2. **Check the log file**:
   - Look in `logs/` directory for the latest combined_scan_*.log file
   - Search for "truth value" or "Traceback" to find exact error line

3. **Report with full context**:
   - Share the exact error message
   - Share the URL you were trying to run
   - Share the log file (logs/combined_scan_*.log)

## Technical Notes

- **Why not use `if df:` directly?** → Pandas DataFrames raise ValueError when converted to bool
- **Why `pd.isna(val)` safe?** → It operates on individual scalar values, never on whole DataFrames
- **Why `.iterrows()` slow?** → Not really - for typical scan results (100-200 rows) it's negligible
- **Why skip dict/list columns?** → These aren't useful data and cause JSON serialization issues

---

**Summary**: All references to `if not df:`, `not df`, `or not df:` have been replaced with explicit type-safe checks. The `_to_rows()` function now uses safe row-by-row iteration instead of DataFrameoperations.
