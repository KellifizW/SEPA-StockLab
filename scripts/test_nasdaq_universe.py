"""scripts/test_nasdaq_universe.py

Quick end-to-end test for the NASDAQ FTP universe module.
Runs the full build and reports timing + output size.
"""
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from modules.nasdaq_universe import get_universe_nasdaq, invalidate_cache  # noqa: E402

print("=" * 60)
print("NASDAQ FTP Universe Test")
print("=" * 60)

# Force fresh  run
invalidate_cache()

t0 = time.time()
df = get_universe_nasdaq()
elapsed = time.time() - t0

print()
print(f"Result   : {len(df)} tickers")
print(f"Time     : {elapsed:.0f}s ({elapsed/60:.1f} min)")
if not df.empty:
    sample = df["Ticker"].head(20).tolist()
    print(f"Sample   : {sample}")
