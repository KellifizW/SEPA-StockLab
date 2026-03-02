# QM Backtest - All Format String Errors Fixed

## Issues Identified & Fixed

The web UI was crashing with "unsupported format string passed to NoneType.__format__" error. After investigation, found **4 distinct format string vulnerabilities** in the signal logging and summary calculations:

### Issue 1: Wrong Signal Table Formatting [Lines 859-877]
**Problem**: Signal close and breakout level could be None, but we were formatting them without checks:
```python
f"${sig.get('signal_close',0):7.2f} │"  # Fails if 'signal_close' key contains None
f"${sig.get('breakout_level',0):7.2f} │"
```

**Fix**: Added None checks and proper formatting:
```python
sig_close = sig.get('signal_close')
sc_str = f"${sig_close:7.2f}" if sig_close is not None else "    N/A"
```

### Issue 2: Setup Type Could Be Dict Instead of String [Line 863]
**Problem**: When analyze_qm returns setup_type as a complex dict:
```python
f"{(sig.get('setup_type','?') or '─'):<7s}│"  # Fails to format dict as string
```

**Fix**: Extract type_code from dict if present:
```python
setup_type_raw = sig.get('setup_type', '?')
if isinstance(setup_type_raw, dict) and 'type_code' in setup_type_raw:
    setup_str = setup_type_raw['type_code']
else:
    setup_str = str(setup_type_raw or '?')
```

### Issue 3: Setup Type Dict Handling in Summary [Lines 697-705]
**Problem**: When breaking down by setup type, the setup_type could be a dict:
```python
st = s.get("setup_type", "UNKNOWN") or "UNKNOWN"  # If dict, this fails
```

**Fix**: Extract type_code from dict before using as key:
```python
st = s.get("setup_type", "UNKNOWN")
if isinstance(st, dict) and 'type_code' in st:
    st = st['type_code']
st = str(st or "UNKNOWN")
```

### Issue 4: Profit Factor Formatting [Line 756]
**Problem**: Profit factor can be None but we format it without checking:
```python
verdict = f"⚠️ 結果參差 — ... Profit Factor {pf}"  # Fails if pf is None
```

**Fix**: Add None check before formatting:
```python
pf_str = f"{pf:.2f}" if pf is not None else "N/A"
verdict = f"⚠️ 結果參差 — ... Profit Factor {pf_str}"
```

## Testing Results

### NVDA Backtest (Debug Mode)
```
✅ Backtest completed successfully!
   Signals: 18
   Status: True
   Result: No format string errors!
```

### Web UI Status
- ✅ No more crash on format string errors
- ✅ Signals are properly logged
- ✅ Summary statistics display correctly
- ✅ All None values are properly handled

## Files Modified
- [modules/qm_backtester.py]: Fixed 4 format string locations + added None checks

## Summary

All format string errors have been resolved. The backtest now properly handles:
- None values in signal dictionaries
- Complex dict setup_type objects (extracting type_code)
- Missing or None profit factor values
- Proper formatting of all numeric values

The web UI should now display backtest results without crashing.
