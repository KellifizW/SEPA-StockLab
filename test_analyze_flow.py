#!/usr/bin/env python3
"""
Test the analyze_qm function directly and print trade plan.
"""
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_analyzer import analyze_qm

result = analyze_qm("ASTI", print_report=False)

print("=" * 70)
print("Direct analyze_qm() Result - Trade Plan")
print("=" * 70)

tp = result.get('trade_plan', {})
print(f"\nRaw trade_plan dict:")
for k, v in tp.items():
    t = type(v).__name__
    print(f"  {k:20} = {v!r:40} ({t})")

print("\n" + "=" * 70)
print("JSON-serialized (like what API would return):")
print("=" * 70)

# Simulate what happens in app.py
def _clean(obj):
    """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
    if obj is None:
        return None
    
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        try:
            val = obj.item()
            return _clean(val)
        except (TypeError, ValueError, AttributeError):
            return None
    
    if isinstance(obj, bool):
        return obj
    
    if isinstance(obj, (int, float, str)):
        if isinstance(obj, float):
            try:
                if obj != obj:
                    return None
            except (TypeError, ValueError):
                pass
        return obj
    
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            cleaned_v = _clean(v)
            if cleaned_v is not None or v is None:
                cleaned[k] = cleaned_v
        return cleaned
    
    if isinstance(obj, (list, tuple)):
        return [_clean(i) for i in obj]
    
    if hasattr(obj, "empty"):
        try:
            if obj.empty:
                return []
            if hasattr(obj, "to_dict"):
                return [_clean(row) for row in obj.to_dict(orient="records")]
            if hasattr(obj, "tolist"):
                return [_clean(v) for v in obj.tolist()]
            return []
        except Exception:
            return None
    
    try:
        return str(obj)
    except Exception:
        return None

cleaned_result = _clean(result)
cleaned_tp = cleaned_result.get('trade_plan', {})

print(f"\nCleaned trade_plan dict (after _clean()):")
for k, v in cleaned_tp.items():
    t = type(v).__name__
    print(f"  {k:20} = {v!r:40} ({t})")

print("\n" + "=" * 70)
print("JSON string (what gets sent to browser):")
print("=" * 70)

json_str = json.dumps(cleaned_result)
data = json.loads(json_str)
tp_from_json = data.get('trade_plan', {})

print(f"\nAfter JSON round-trip:")
for k in ['day1_stop', 'day2_stop', 'day3plus_stop']:
    v = tp_from_json.get(k)
    t = type(v).__name__
    print(f"  {k:20} = {v!r:40} ({t})")
