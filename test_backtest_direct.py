#!/usr/bin/env python
"""Test QM backtest directly."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.qm_backtester import run_qm_backtest

print("Running QM backtest for PLTR in debug mode...")
result = run_qm_backtest("PLTR", debug_mode=True)

# Print summary
print(f"\n{'='*60}")
print(f"BACKTEST RESULT FOR {result['ticker']}")
print(f"{'='*60}")
print(f"Period: {result['period_start']} to {result['period_end']}")
print(f"Total bars: {result['total_bars']}")
print(f"Min star: {result['min_star']}")

summary = result.get("summary", {})
print(f"\nSummary:")
print(f"  Signals found: {summary.get('signals_found', 0)}")
print(f"  Wins: {summary.get('wins', 0)}")
print(f"  Losses: {summary.get('losses', 0)}")
print(f"  Small Wins: {summary.get('small_wins', 0)}")
print(f"  Win Rate: {summary.get('win_rate_pct', 0):.1f}%")
print(f"  CAGR: {summary.get('cagr_pct', 0):.1f}%")

signals = result.get("signals", [])
print(f"\nSignals: {len(signals)}")
if len(signals) > 0:
    print("\nFirst 5 signals:")
    for i, sig in enumerate(signals[:5]):
        print(f"  {i+1}. {sig['signal_date']}: {sig['ticker']} @ ${sig['signal_close']} ⭐ {sig['star_rating']}")
else:
    print("  ⚠️  No signals found")

print(f"\nBacktest OK: {result.get('ok')}")
if result.get('error'):
    print(f"Error: {result['error']}")
