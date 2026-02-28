"""Analyze 2-year VCP candidates and backtest profit space using DuckDB."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules import backtester

DB_PATH = ROOT / C.DB_FILE
CSV_DIR = ROOT / "data"


@dataclass
class BtStat:
    ticker: str
    source: str
    scan_hits: int
    avg_scan_score: float | None
    last_seen: str | None
    signals: int
    breakouts: int
    win_rate: float | None
    avg_exit: float | None
    avg_max_gain: float | None
    avg_headroom: float | None
    capture_ratio: float | None
    equity_end: float


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    query = """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE lower(table_name) = lower(?)
    """
    cnt = conn.execute(query, [table_name]).fetchone()[0]
    return bool(cnt)


def _candidates_from_scan_history(conn: duckdb.DuckDBPyConnection, limit: int) -> pd.DataFrame:
    if not _table_exists(conn, "scan_history"):
        return pd.DataFrame()

    query = """
        WITH base AS (
            SELECT
                ticker,
                scan_date,
                sepa_score,
                vcp_grade,
                recommendation
            FROM scan_history
            WHERE scan_date >= CURRENT_DATE - INTERVAL '2 years'
              AND vcp_grade IN ('A', 'B', 'C')
        )
        SELECT
            ticker,
            COUNT(*) AS scan_hits,
            ROUND(AVG(sepa_score), 2) AS avg_scan_score,
            MAX(scan_date) AS last_seen,
            SUM(CASE WHEN recommendation IN ('BUY', 'WATCH') THEN 1 ELSE 0 END) AS buy_watch_hits,
            'scan_history' AS source
        FROM base
        GROUP BY ticker
        HAVING COUNT(*) >= 3
        ORDER BY scan_hits DESC, avg_scan_score DESC NULLS LAST
        LIMIT ?
    """
    return conn.execute(query, [limit]).fetchdf()


def _candidates_from_scan_csv(conn: duckdb.DuckDBPyConnection, limit: int) -> pd.DataFrame:
    csv_files = sorted(CSV_DIR.glob("sepa_scan_*.csv"))
    if not csv_files:
        return pd.DataFrame()

    conn.execute("DROP TABLE IF EXISTS tmp_scan_csv")
    conn.execute("""
        CREATE TEMP TABLE tmp_scan_csv (
            ticker VARCHAR,
            total_score DOUBLE,
            vcp_grade VARCHAR,
            vcp_score_raw DOUBLE,
            t_count DOUBLE,
            source_file VARCHAR
        )
    """)

    for file_path in csv_files:
        file_str = file_path.as_posix()
        conn.execute(
            """
            INSERT INTO tmp_scan_csv
            SELECT
                ticker,
                try_cast(total_score AS DOUBLE) AS total_score,
                vcp_grade,
                try_cast(vcp_score_raw AS DOUBLE) AS vcp_score_raw,
                try_cast(t_count AS DOUBLE) AS t_count,
                ? AS source_file
            FROM read_csv_auto(
                ?,
                header = true,
                delim = ',',
                quote = '"',
                strict_mode = false,
                ignore_errors = true
            )
            """,
            [file_path.name, file_str],
        )

    query = """
        WITH base AS (
            SELECT
                upper(trim(ticker)) AS ticker,
                total_score AS sepa_score,
                upper(trim(vcp_grade)) AS vcp_grade,
                vcp_score_raw,
                t_count,
                regexp_extract(source_file, '(\\d{4}-\\d{2}-\\d{2})', 1) AS file_date
            FROM tmp_scan_csv
            WHERE ticker IS NOT NULL
              AND trim(ticker) <> ''
        )
        SELECT
            ticker,
            COUNT(*) AS scan_hits,
            ROUND(AVG(sepa_score), 2) AS avg_scan_score,
            MAX(file_date) AS last_seen,
            SUM(CASE WHEN vcp_grade IN ('A', 'B', 'C') THEN 1 ELSE 0 END) AS buy_watch_hits,
            'scan_csv' AS source
        FROM base
        WHERE (vcp_grade IN ('A', 'B', 'C') OR coalesce(vcp_score_raw, 0) >= 35)
          AND coalesce(t_count, 0) >= 2
        GROUP BY ticker
        ORDER BY avg_scan_score DESC NULLS LAST, scan_hits DESC
        LIMIT ?
    """
    return conn.execute(query, [limit]).fetchdf()


def get_candidates(limit: int = 120) -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH))
    try:
        from_history = _candidates_from_scan_history(conn, limit)
        if not from_history.empty:
            return from_history
        return _candidates_from_scan_csv(conn, limit)
    finally:
        conn.close()


def analyze_ticker(ticker: str) -> BtStat | None:
    result = backtester.run_backtest(ticker, min_vcp_score=35, outcome_days=120)
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

    if paired:
        headrooms = [mx - ex for ex, mx in paired]
        avg_headroom = round(sum(headrooms) / len(headrooms), 2)
        pos_pairs = [(ex, mx) for ex, mx in paired if mx > 0]
        capture_ratio = round(sum(ex / mx for ex, mx in pos_pairs) / len(pos_pairs) * 100, 1) if pos_pairs else None
    else:
        avg_headroom = None
        capture_ratio = None

    return BtStat(
        ticker=ticker,
        source="",
        scan_hits=0,
        avg_scan_score=None,
        last_seen=None,
        signals=int(summary.get("total_signals") or 0),
        breakouts=int(summary.get("breakouts") or 0),
        win_rate=summary.get("win_rate_pct"),
        avg_exit=avg_exit,
        avg_max_gain=avg_max,
        avg_headroom=avg_headroom,
        capture_ratio=capture_ratio,
        equity_end=round(equity_end, 2),
    )


def _rank_with_duckdb(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = duckdb.connect()
    try:
        conn.register("bt", df)
        realized = conn.execute(
            """
            SELECT *, ((equity_end / 100.0) - 1.0) * 100.0 AS equity_gain_pct
            FROM bt
            WHERE breakouts > 0
            ORDER BY equity_end DESC, scan_hits DESC
            LIMIT 20
            """
        ).fetchdf()
        headroom = conn.execute(
            """
            SELECT *, ((equity_end / 100.0) - 1.0) * 100.0 AS equity_gain_pct
            FROM bt
            WHERE breakouts > 0 AND avg_headroom IS NOT NULL
            ORDER BY avg_headroom DESC, avg_max_gain DESC, equity_end DESC
            LIMIT 20
            """
        ).fetchdf()
        return realized, headroom
    finally:
        conn.close()


def main() -> None:
    if not DB_PATH.exists():
        print(f"DuckDB not found: {DB_PATH}")
        return

    candidates = get_candidates(limit=120)
    if candidates.empty:
        print("No candidate rows found from DuckDB history or scan CSV fallback.")
        return

    stats: list[BtStat] = []
    for _, row in candidates.iterrows():
        ticker = str(row["ticker"]).upper()
        s = analyze_ticker(ticker)
        if s is None:
            continue
        s.source = str(row.get("source") or "")
        s.scan_hits = int(row.get("scan_hits", 0))
        s.avg_scan_score = float(row["avg_scan_score"]) if pd.notna(row.get("avg_scan_score")) else None
        s.last_seen = str(row["last_seen"]) if pd.notna(row.get("last_seen")) else None
        stats.append(s)

    if not stats:
        print("No analyzable candidates after backtest run.")
        return

    df = pd.DataFrame([s.__dict__ for s in stats])
    df = df[df["breakouts"] > 0].copy()
    if df.empty:
        print("Candidates had no confirmed breakouts under current strategy.")
        return

    best_realized, best_headroom = _rank_with_duckdb(df)

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out_all = out_dir / "vcp_profit_space_analysis.csv"
    out_realized = out_dir / "vcp_best_realized_top20.csv"
    out_headroom = out_dir / "vcp_best_headroom_top20.csv"

    df.sort_values(["equity_end", "scan_hits"], ascending=[False, False]).to_csv(out_all, index=False)
    best_realized.to_csv(out_realized, index=False)
    best_headroom.to_csv(out_headroom, index=False)

    cols = [
        "ticker", "source", "scan_hits", "signals", "breakouts", "win_rate",
        "avg_exit", "avg_max_gain", "avg_headroom", "capture_ratio",
        "equity_end", "equity_gain_pct",
    ]

    print("=== Universe Stats ===")
    print(f"Candidates (DuckDB history first, fallback CSV): {len(candidates)}")
    print(f"Backtested with >=1 breakout: {len(df)}")
    print()
    print("=== Top 12 by Realized Equity (Strategy Outcome) ===")
    print(best_realized[cols].head(12).to_string(index=False))
    print()
    print("=== Top 12 by Profit Space (Headroom) ===")
    print(best_headroom[cols].head(12).to_string(index=False))
    print()

    pltr = df[df["ticker"] == "PLTR"]
    if not pltr.empty:
        row = pltr.iloc[0]
        print("=== PLTR Snapshot ===")
        print(
            f"PLTR equity_end=${row['equity_end']:.2f}, breakouts={int(row['breakouts'])}, "
            f"avg_exit={row['avg_exit']}%, avg_max_gain={row['avg_max_gain']}%, "
            f"headroom={row['avg_headroom']}%, capture={row['capture_ratio']}%"
        )
        print()

    print("Saved:")
    print(f"- {out_all}")
    print(f"- {out_realized}")
    print(f"- {out_headroom}")


if __name__ == "__main__":
    main()
