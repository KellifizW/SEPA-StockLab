#!/usr/bin/env python3
"""Debug ML Stage 3 crash."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
import json
import traceback
from modules.ml_screener import run_ml_stage3
from modules.data_pipeline import get_enriched

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load the last stage 2 results from data/ml_last_scan.json
if not (ROOT / "data" / "ml_last_scan.json").exists():
    print("ERROR: data/ml_last_scan.json not found")
    sys.exit(1)

with open(ROOT / "data" / "ml_last_scan.json") as f:
    data = json.load(f)
    
if not isinstance(data, dict) or "passed" not in data:
    print("ERROR: Invalid format in ml_last_scan.json")
    sys.exit(1)

passed = data.get("passed", [])
if not passed:
    print("No passed records in ml_last_scan.json")
    sys.exit(1)

print(f"[DEBUG] Loaded {len(passed)} Stage 2 passed records from ml_last_scan.json")
print(f"[DEBUG] First record: {passed[0]}")

# Extract just the first 5 records to test
test_records = passed[:5]

print(f"\n[DEBUG] Testing first {len(test_records)} records...")
for i, rec in enumerate(test_records):
    ticker = rec.get("ticker", "???")
    print(f"\n[DEBUG] Testing {i+1}/{len(test_records)}: {ticker}")
    try:
        # Try to call get_enriched
        df = get_enriched(ticker, period="1y", use_cache=True)
        print(f"  ✓ get_enriched returned {len(df) if df is not None and not df.empty else 0} rows")
        
        if df is not None and not df.empty:
            # Try to call detect_setup_type
            try:
                from modules.ml_setup_detector import detect_setup_type
                setup_info = detect_setup_type(df, ticker)
                print(f"  ✓ detect_setup_type returned: {setup_info.get('primary_setup')}")
            except Exception as e:
                print(f"  ✗ detect_setup_type FAILED: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"  ✗ get_enriched FAILED: {e}")
        traceback.print_exc()

print("\n[DEBUG] Now trying full run_ml_stage3...")
try:
    result = run_ml_stage3(test_records)
    print(f"✓ run_ml_stage3 returned {len(result)} results")
except Exception as e:
    print(f"✗ run_ml_stage3 FAILED: {e}")
    traceback.print_exc()
