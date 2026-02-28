"""Lightweight expanded analysis: quick profit-space ranking using batching."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules import backtester

# Load existing VCP candidates from CSV + expand with robust subset tuning
PRICE_CACHE_DIR = ROOT / "data" / "price_cache"
REPORTS_DIR = ROOT / "reports"


def extract_tickers_from_cache() -> set[str]:
    """Extract all unique tickers from price_cache."""
    tickers = set()
    if not PRICE_CACHE_DIR.exists():
        print(f"Warning: price_cache not found at {PRICE_CACHE_DIR}")
        return tickers

    for meta_file in PRICE_CACHE_DIR.glob("*_2y.meta"):
        ticker = meta_file.stem.replace("_2y", "").upper()
        if ticker and not ticker.startswith("."):
            tickers.add(ticker)

    return sorted(tickers)


def analyze_and_rank(limit: int = 150) -> None:
    """Batch backtest and rank by profitability."""
    print("=== Extracting Tickers from price_cache ===")
    all_tickers = extract_tickers_from_cache()
    print(f"Found {len(all_tickers)} tickers with 2-year price history")
    print()

    # Sample representative tickers (spread across alphabet):
    # To avoid timeout, sample every Nth ticker 
    step = max(1, len(all_tickers) // limit)
    test_tickers = all_tickers[::step][:limit]
    print(f"Testing {len(test_tickers)} representative tickers (every {step}th)...")
    print()

    results = []
    failed = 0
    
    print("=== Running Backtest Sweep ===")
    for i, ticker in enumerate(test_tickers, 1):
        if i % 30 == 0:
            print(f"Progress: {i}/{len(test_tickers)}...")

        try:
            result = backtester.run_backtest(ticker, min_vcp_score=25, outcome_days=120)
            if not result.get("ok"):
                failed += 1
                continue

            signals = result.get("signals", [])
            summary = result.get("summary", {})
            
            breakouts = [s for s in signals if s.get("outcome") != "NO_BREAKOUT"]
            if not breakouts:
                continue

            eq_curve = result.get("equity_curve", [])
            equity_end = float(eq_curve[-1]["value"]) if eq_curve else 100.0

            # Extract metrics
            paired = [
                (float(s.get("exit_gain_pct", 0)), float(s.get("max_gain_pct", 0)))
                for s in breakouts
                if s.get("exit_gain_pct") is not None and s.get("max_gain_pct") is not None
            ]

            if not paired:
                continue

            exit_vals = [ex for ex, _ in paired]
            max_vals = [mx for _, mx in paired]
            headrooms = [mx - ex for ex, mx in paired]

            avg_exit = round(sum(exit_vals) / len(exit_vals), 2)
            avg_max = round(sum(max_vals) / len(max_vals), 2)
            avg_headroom = round(sum(headrooms) / len(headrooms), 2)

            pos_pairs = [(ex, mx) for ex, mx in paired if mx > 0]
            capture = round(sum(ex / mx for ex, mx in pos_pairs) / len(pos_pairs) * 100, 1) if pos_pairs else 0

            results.append({
                "ticker": ticker,
                "signals": int(summary.get("total_signals", 0)),
                "breakouts": len(breakouts),
                "win_rate": summary.get("win_rate_pct", 0),
                "avg_exit_pct": avg_exit,
                "avg_max_pct": avg_max,
                "avg_headroom_pct": avg_headroom,
                "capture_ratio_pct": capture,
                "equity_end": equity_end,
                "equity_gain_pct": round((equity_end / 100.0 - 1.0) * 100, 2),
            })

        except Exception as e:
            failed += 1

    print(f"Done. Analyzed {len(results)} successful backtests ({failed} failed).")
    print()

    if not results:
        print("No valid results.")
        return

    df = pd.DataFrame(results)

    # Stats
    print(f"=== Universe Stats ({len(test_tickers)} sample) ===")
    print(f"Tickers with >=1 breakout: {len(df)}")
    print(f"Avg equity return: {df['equity_gain_pct'].mean():.2f}%")
    print()

    # Robust subset
    robust = df[(df["signals"] >= 3) & (df["breakouts"] >= 2)]
    if not robust.empty:
        print(f"Robust subset (sig>=3, brk>=2): {len(robust)}")
        print(f"Avg equity return (robust): {robust['equity_gain_pct'].mean():.2f}%")
        print()

    # Top performers
    top_realized = df.nlargest(20, "equity_end")
    top_headroom = df.nlargest(20, "avg_headroom_pct")

    print("=== Top 15 by Realized Equity ===")
    cols = ["ticker", "signals", "breakouts", "win_rate", "avg_exit_pct", 
            "avg_max_pct", "avg_headroom_pct", "equity_end", "equity_gain_pct"]
    print(top_realized[cols].head(15).to_string(index=False))
    print()

    print("=== Top 15 by Profit Headroom ===")
    print(top_headroom[cols].head(15).to_string(index=False))
    print()

    # Feature analysis
    if len(top_realized) >= 5:
        top_5 = top_realized.head(5)
        all_with_brk = df
        print("=== Feature Analysis: Top 5 vs All ===")
        print(
            f"Avg signals: {top_5['signals'].mean():.1f} vs {all_with_brk['signals'].mean():.1f}"
        )
        print(
            f"Avg breakouts: {top_5['breakouts'].mean():.1f} vs {all_with_brk['breakouts'].mean():.1f}"
        )
        print(
            f"Avg win_rate: {top_5['win_rate'].mean():.1f}% vs {all_with_brk['win_rate'].mean():.1f}%"
        )
        print(
            f"Avg max_gain: {top_5['avg_max_pct'].mean():.1f}% vs {all_with_brk['avg_max_pct'].mean():.1f}%"
        )
        print(
            f"Avg capture: {top_5['capture_ratio_pct'].mean():.1f}% vs {all_with_brk['capture_ratio_pct'].mean():.1f}%"
        )
        print()

    # Save
    REPORTS_DIR.mkdir(exist_ok=True)
    out_all = REPORTS_DIR / "expand_sample_all.csv"
    out_realized = REPORTS_DIR / "expand_sample_top20_realized.csv"
    out_headroom = REPORTS_DIR / "expand_sample_top20_headroom.csv"

    df.sort_values("equity_end", ascending=False).to_csv(out_all, index=False)
    top_realized.to_csv(out_realized, index=False)
    top_headroom.to_csv(out_headroom, index=False)

    print("=== Files Saved ===")
    print(f"- {out_all}")
    print(f"- {out_realized}")
    print(f"- {out_headroom}")


if __name__ == "__main__":
    analyze_and_rank(limit=150)
