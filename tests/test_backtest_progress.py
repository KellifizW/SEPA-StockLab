#!/usr/bin/env python
"""Quick test of backtester progress callback."""

from modules.backtester import run_backtest

updates = []

def progress_callback(pct, msg):
    updates.append((pct, msg))
    print(f"[{pct:3d}%] {msg}")

print("Testing NVDA backtest with progress callback...\n")
r = run_backtest("NVDA", min_vcp_score=35, progress_cb=progress_callback)

print(f"\n{'='*60}")
print(f"✓ Received {len(updates)} progress callbacks")
print(f"✓ Found {r['summary']['total_signals']} signals")
print(f"✓ Win rate: {r['summary']['win_rate_pct']}%")
print(f"✓ Result keys: {', '.join(r.keys())}")
