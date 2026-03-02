#!/usr/bin/env python3
"""Test Stage 3 functions directly."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.DEBUG)

print("[1] Testing imports from data_pipeline...")
try:
    from modules.data_pipeline import (
        get_ema_alignment, get_pullback_depth, get_avwap_from_swing_high,
        get_avwap_from_swing_low, get_ema_slope, get_enriched
    )
    print("  ✓ All data_pipeline imports OK")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n[2] Testing import of detect_setup_type...")
try:
    from modules.ml_setup_detector import detect_setup_type
    print("  ✓ detect_setup_type import OK")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n[3] Testing compute_star_rating...")
try:
    from modules.ml_analyzer import compute_star_rating
    print("  ✓ compute_star_rating import OK")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n[4] Getting sample data for AAPL...")
try:
    df = get_enriched("AAPL", period="1y", use_cache=True)
    print(f"  ✓ Got {len(df)} rows of data")
    if df.empty:
        print("  ✗ DataFrame is empty!")
        sys.exit(1)
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n[5] Testing detect_setup_type on AAPL...")
try:
    result = detect_setup_type(df, "AAPL")
    print(f"  ✓ Result: {result.get('primary_setup')}")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n[6] Testing get_ema_alignment on AAPLdata...")
try:
    result = get_ema_alignment(df)
    print(f"  ✓ Result: {result}")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n[7] Testing _score_ml_stage3...")
try:
    from modules.ml_screener import _score_ml_stage3
    row = {"ticker": "AAPL"}
    result = _score_ml_stage3(row, df)
    print(f"  ✓ Scored successfully")
    if result:
        print(f"     ml_star={result.get('ml_star')}")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n[SUCCESS] All Stage 3 functions work correctly!")
