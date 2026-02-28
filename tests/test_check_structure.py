#!/usr/bin/env python3
"""Quick check: where is capped_stars in the response?"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

resp = requests.post("http://127.0.0.1:5000/api/qm/analyze", json={"ticker": "ASTI"}, timeout=30)
result = resp.json().get("result", {})

print("Top-level keys in result:")
for key in sorted(result.keys()):
    val_type = type(result[key]).__name__
    val_str = str(result[key])[:60] if not isinstance(result[key], (dict, list)) else f"{val_type}"
    print(f"  • {key}: {val_str}")

print(f"\ncapped_stars found at root level? {result.get('capped_stars')}")

# If capped_stars is there
if "capped_stars" in result:
    print(f"✓ capped_stars = {result['capped_stars']}")
else:
    print("✗ capped_stars NOT found at root level")
    # Check if it's nested somewhere
    import json
    s = json.dumps(result)
    if "capped_stars" in s:
        print("  But found in JSON string - checking where...")
        for k, v in result.items():
            if isinstance(v, dict) and "capped_stars" in str(v):
                print(f"    found in result['{k}']")
