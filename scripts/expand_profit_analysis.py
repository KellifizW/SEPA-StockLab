"""Expand profit-space analysis: backtest ALL tickers in price_cache universe."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules import backtester

DB_PATH = ROOT / C.DB_FILE
PRICE_CACHE_DIR = ROOT / "data" / "price_cache"


@dataclass
class BtStat:
    ticker: str
    signals: int
    breakouts: int
    win_rate: Optional[float]
    avg_exit: Optional[float]
    avg_max_gain: Optional[float]
    max_gain_sample: Optional[float]  # Track sample max gain for feature analysis
    avg_headroom: Optional[float]
    capture_ratio: Optional[float]
    equity_end: float


def extract_tickers_from_cache() -> set[str]:
    """Extract all unique tickers from price_cache by scanning _2y.meta files."""
    tickers = set()
    if not PRICE_CACHE_DIR.exists():
        print(f"Warning: price_cache not found at {PRICE_CACHE_DIR}")
        return tickers

    for meta_file in PRICE_CACHE_DIR.glob("*_2y.meta"):
        ticker = meta_file.stem.replace("_2y", "").upper()
        if ticker and not ticker.startswith("."):
            tickers.add(ticker)

    return sorted(tickers)


def analyze_ticker(ticker: str) -> BtStat | None:
    """Run backtest and extract key performance metrics."""
    result = backtester.run_backtest(ticker, min_vcp_score=25, outcome_days=120)
    if not result.get("ok"):
        return None

    signals = result.get("signals", [])
    summary = result.get("summary", {})

    breakout_rows = [s for s in signals if s.get("outcome") != "NO_BREAKOUT"]
    if not breakout_rows:
        equity_end = 100.0
    else:
        eq_curve = result.get("equity_curve", [])
        equity_end = float(eq_curve[-1]["value"]) if eq_curve else 100.0

    paired = [
        (float(s.get("exit_gain_pct")), float(s.get("max_gain_pct")))
        for s in breakout_rows
        if s.get("exit_gain_pct") is not None and s.get("max_gain_pct") is not None
    ]

    exit_vals = [ex for ex, _ in paired]
    max_vals = [mx for _, mx in paired]

    avg_exit = round(sum(exit_vals) / len(exit_vals), 2) if exit_vals else None
    avg_max = round(sum(max_vals) / len(max_vals), 2) if max_vals else None
    sample_max = max_vals[0] if max_vals else None  # Track first occurrence

    if paired:
        headrooms = [mx - ex for ex, mx in paired]
        avg_headroom = round(sum(headrooms) / len(headrooms), 2)
        pos_pairs = [(ex, mx) for ex, mx in paired if mx > 0]
        capture_ratio = round(
            sum(ex / mx for ex, mx in pos_pairs) / len(pos_pairs) * 100, 1
        ) if pos_pairs else None
    else:
        avg_headroom = None
        capture_ratio = None

    return BtStat(
        ticker=ticker,
        signals=int(summary.get("total_signals") or 0),
        breakouts=int(summary.get("breakouts") or 0),
        win_rate=summary.get("win_rate_pct"),
        avg_exit=avg_exit,
        avg_max_gain=avg_max,
        max_gain_sample=sample_max,
        avg_headroom=avg_headroom,
        capture_ratio=capture_ratio,
        equity_end=round(equity_end, 2),
    )


def _rank_with_duckdb(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """DuckDB ranking: realized return and headroom."""
    conn = duckdb.connect()
    try:
        conn.register("bt", df)

        # Realized return (equity compound)
        realized = conn.execute(
            """
            SELECT *,
                   ROUND(((equity_end / 100.0) - 1.0) * 100.0, 2) AS equity_gain_pct
            FROM bt
            WHERE breakouts > 0
            ORDER BY equity_end DESC, signals DESC, breakouts DESC
            LIMIT 30
            """
        ).fetchdf()

        # Headroom (profit left on table)
        headroom = conn.execute(
            """
            SELECT *,
                   ROUND(((equity_end / 100.0) - 1.0) * 100.0, 2) AS equity_gain_pct
            FROM bt
            WHERE breakouts > 0 AND avg_headroom IS NOT NULL
            ORDER BY avg_headroom DESC, avg_max_gain DESC, equity_end DESC
            LIMIT 30
            """
        ).fetchdf()

        return realized, headroom
    finally:
        conn.close()


def main() -> None:
    print("=== Extracting Tickers from price_cache ===")
    tickers = extract_tickers_from_cache()
    print(f"Found {len(tickers)} tickers with 2-year price history")
    print()

    if not tickers:
        print("ERROR: No tickers found in price_cache.")
        return

    stats: list[BtStat] = []
    failed: list[str] = []

    print("=== Running Backtest Sweep ===")
    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            print(f"Progress: {i}/{len(tickers)}... ({ticker})")

        s = analyze_ticker(ticker)
        if s is None:
            failed.append(ticker)
            continue

        stats.append(s)

    print(f"Done. Analyzed {len(stats)} tickers, {len(failed)} failed.")
    print()

    if not stats:
        print("No analyzable tickers found.")
        return

    df = pd.DataFrame([s.__dict__ for s in stats])

    # Count by breakout status
    with_breakouts = df[df["breakouts"] > 0]
    if with_breakouts.empty:
        print("No tickers with confirmed breakouts under current strategy.")
        return

    print(f"=== Universe Performance (min_vcp_score=25, outcome_days=120) ===")
    print(f"Total tickers tested: {len(df)}")
    print(f"Tickers with >=1 breakout: {len(with_breakouts)}")
    print(f"Average equity return (all): {df['equity_end'].mean():.2f}")
    print(f"Average equity return (with breakouts only): {with_breakouts['equity_end'].mean():.2f}")
    print()

    # Robust subset: >= 3 signals AND >= 2 breakouts (reduces outliers)
    robust = with_breakouts[
        (with_breakouts["signals"] >= 3) & (with_breakouts["breakouts"] >= 2)
    ].copy()
    if not robust.empty:
        print(f"Robust subset (signals>=3, breakouts>=2): {len(robust)} tickers")
        print(f"Average equity return (robust): {robust['equity_end'].mean():.2f}")
        print()

    best_realized, best_headroom = _rank_with_duckdb(with_breakouts)

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)

    out_all = out_dir / "expand_all_backtests.csv"
    out_realized = out_dir / "expand_best_realized_top30.csv"
    out_headroom = out_dir / "expand_best_headroom_top30.csv"

    # Save full results
    with_breakouts.sort_values(["equity_end", "signals"], ascending=[False, False]).to_csv(
        out_all, index=False
    )
    best_realized.to_csv(out_realized, index=False)
    best_headroom.to_csv(out_headroom, index=False)

    cols = [
        "ticker", "signals", "breakouts", "win_rate", "avg_exit",
        "avg_max_gain", "avg_headroom", "capture_ratio", "equity_end", "equity_gain_pct"
    ]

    print("=== Top 20 by Realized Equity (Strategy Outcome) ===")
    print(best_realized[cols].head(20).to_string(index=False))
    print()

    print("=== Top 20 by Profit Space (Avg Headroom) ===")
    print(best_headroom[cols].head(20).to_string(index=False))
    print()

    # Feature analysis: winners vs average
    top_10 = best_realized.head(10)
    if not top_10.empty:
        print("=== Feature Analysis: Top 10 Winners vs Total Universe ===")
        print(
            f"Avg signals (top 10): {top_10['signals'].mean():.1f} "
            f"vs (all): {with_breakouts['signals'].mean():.1f}"
        )
        print(
            f"Avg breakouts (top 10): {top_10['breakouts'].mean():.1f} "
            f"vs (all): {with_breakouts['breakouts'].mean():.1f}"
        )
        print(
            f"Avg win rate (top 10): {top_10['win_rate'].mean():.1f}% "
            f"vs (all): {with_breakouts['win_rate'].mean():.1f}%"
        )
        print(
            f"Avg max_gain (top 10): {top_10['avg_max_gain'].mean():.1f}% "
            f"vs (all): {with_breakouts['avg_max_gain'].mean():.1f}%"
        )
        print(
            f"Avg capture_ratio (top 10): {top_10['capture_ratio'].mean():.1f}% "
            f"vs (all): {with_breakouts['capture_ratio'].mean():.1f}%"
        )
        print()

    # Highlight best individual
    if not best_realized.empty:
        row = best_realized.iloc[0]
        print(f"=== Champion: {row['ticker']} ===")
        print(
            f"Equity: ${row['equity_end']:.2f} ({row.get('equity_gain_pct', 0):.1f}%), "
            f"Signals: {int(row['signals'])}, Breakouts: {int(row['breakouts'])}, "
            f"WR: {row['win_rate']:.1f}%, MaxGain: {row['avg_max_gain']:.1f}%, "
            f"Headroom: {row['avg_headroom']:.1f}%"
        )
        print()

    print("=== Files Saved ===")
    print(f"- {out_all}")
    print(f"- {out_realized}")
    print(f"- {out_headroom}")


if __name__ == "__main__":
    main()
