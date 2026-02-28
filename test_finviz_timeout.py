#!/usr/bin/env python3
"""
Test the finvizfinance timeout feature in get_universe().
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import time

def test_get_universe_timeout():
    """Test that get_universe times out after 15 seconds instead of hanging forever."""
    from modules.data_pipeline import get_universe
    
    print("\n" + "="*70)
    print("TEST: get_universe() with 15-second timeout")
    print("="*70)
    
    filters = {
        "Price":   "Over $5",
        "Average Volume": "Over 300K",
        "Country": "USA",
    }
    
    print("\nCalling get_universe with Performance view...")
    print("(Will timeout after 15 seconds if finvizfinance is slow)\n")
    
    start = time.time()
    df = get_universe(filters, view="Performance", verbose=False)
    elapsed = time.time() - start
    
    print(f"✓ get_universe returned after {elapsed:.1f} seconds")
    print(f"  Rows returned: {len(df)}")
    
    if len(df) > 0:
        print(f"  Sample tickers: {list(df['Ticker'].head(3).values)}")
        return True
    elif elapsed < 15.5:
        print("  (Empty but completed quickly, likely no matching results)")
        return True
    else:
        print("  ✗ Timeout occurred - took too long")
        return False

if __name__ == "__main__":
    success = test_get_universe_timeout()
    sys.exit(0 if success else 1)
