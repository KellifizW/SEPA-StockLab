"""
Test to locate DataFrame truth value ambiguity error
"""
import sys
from pathlib import Path
import pandas as pd
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))

# Direct test of yfinance behavior
import yfinance as yf

print("Test 1: Single ticker download")
try:
    raw_single = yf.download('AAPL', period='1y', progress=False)
    print(f"  Type: {type(raw_single)}")
    print(f"  Is DataFrame: {isinstance(raw_single, pd.DataFrame)}")
    print(f"  Has columns: {hasattr(raw_single, 'columns')}")
    if hasattr(raw_single, 'empty'):
        print(f"  .empty check: {raw_single.empty}")
    print("  SUCCESS")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 2: Multiple tickers download")
try:
    raw_multi = yf.download(['AAPL', 'MSFT'], period='1y', progress=False)
    print(f"  Type: {type(raw_multi)}")
    print(f"  Is DataFrame: {isinstance(raw_multi, pd.DataFrame)}")
    print(f"  Has columns: {hasattr(raw_multi, 'columns')}")
    if hasattr(raw_multi, 'empty'):
        print(f"  .empty check: {raw_multi.empty}")
    if isinstance(raw_multi, pd.DataFrame):
        print(f"  MultiIndex columns: {isinstance(raw_multi.columns, pd.MultiIndex)}")
    print("  SUCCESS")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 3: Check if pandas.empty works")
try:
    df = pd.DataFrame({'a': [1, 2, 3]})
    if df.empty:
        print("  Empty DataFrame detected")
    else:
        print("  Non-empty DataFrame OK")
    
    # Try boolean context (this should fail)
    try:
        if df:
            pass
    except ValueError as ve:
        print(f"  Boolean context fails as expected: {ve}")
        print("  SUCCESS - This is the error we're looking for!")
except Exception as e:
    print(f"  ERROR: {e}")
