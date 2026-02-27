"""
modules/db.py
─────────────
DuckDB persistence layer — append-only historical store.

All writes are ADDITIVE; existing JSON/Parquet/CSV logic is untouched.
This module only records history; it never replaces flat-file caching.

Tables
──────
  scan_history     — 每次掃描的通過/評分股票（每日 × 每股）
  rs_history       — RS 排名快照（每日 × 每股）
  market_env_history — 市場環境日誌（每日一行）
  watchlist_log    — 觀察名單異動記錄
  position_log     — 持倉新增 / 平倉記錄
"""

import sys
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

_DB_PATH = ROOT / C.DATA_DIR / "sepa_stock.duckdb"
_lock = threading.Lock()   # DuckDB write-lock (single writer)

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    """Return a new DuckDB connection (thread-local read/write)."""
    try:
        import duckdb
        return duckdb.connect(str(_DB_PATH))
    except ImportError:
        raise RuntimeError("duckdb not installed. Run: pip install duckdb")


def _ensure_schema(conn):
    """Create all tables if they don't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            scan_date       DATE    NOT NULL,
            ticker          VARCHAR NOT NULL,
            sepa_score      DOUBLE,
            vcp_grade       VARCHAR,
            tt_passed       INTEGER,
            tt_total        INTEGER,
            close_price     DOUBLE,
            rs_rank         DOUBLE,
            trend_score     DOUBLE,
            fundamental_score DOUBLE,
            catalyst_score  DOUBLE,
            entry_score     DOUBLE,
            risk_score      DOUBLE,
            recommendation  VARCHAR,
            PRIMARY KEY (scan_date, ticker)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rs_history (
            rank_date   DATE    NOT NULL,
            ticker      VARCHAR NOT NULL,
            rs_raw      DOUBLE,
            rs_rank     DOUBLE,
            PRIMARY KEY (rank_date, ticker)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_env_history (
            env_date          DATE PRIMARY KEY,
            regime            VARCHAR,
            distribution_days INTEGER,
            breadth_pct       DOUBLE,
            spy_close         DOUBLE,
            qqq_close         DOUBLE,
            iwm_close         DOUBLE,
            action_note       VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_log (
            log_date    DATE    NOT NULL,
            ticker      VARCHAR NOT NULL,
            action      VARCHAR NOT NULL,
            grade       VARCHAR,
            sepa_score  DOUBLE,
            note        VARCHAR,
            PRIMARY KEY (log_date, ticker, action)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_log (
            log_date    DATE    NOT NULL,
            ticker      VARCHAR NOT NULL,
            action      VARCHAR NOT NULL,
            price       DOUBLE,
            shares      INTEGER,
            stop_price  DOUBLE,
            pnl_pct     DOUBLE,
            hold_days   INTEGER,
            note        VARCHAR
        )
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Public write APIs
# ─────────────────────────────────────────────────────────────────────────────

def append_scan_history(rows: list, scan_date: date = None) -> int:
    """
    Persist scan result rows to scan_history table.

    Parameters
    ----------
    rows      : list of dicts (from _save_last_scan in app.py)
    scan_date : defaults to today

    Returns number of rows upserted.
    """
    if not rows:
        return 0

    today = scan_date or date.today()
    records = []
    for r in rows:
        try:
            pillar = r.get("pillar_scores") or {}
            records.append({
                "scan_date":           today,
                "ticker":              str(r.get("ticker", "")).upper(),
                "sepa_score":          _to_float(r.get("sepa_score")),
                "vcp_grade":           str(r.get("vcp_grade") or ""),
                "tt_passed":           _to_int(r.get("tt_passed")),
                "tt_total":            _to_int(r.get("tt_total")),
                "close_price":         _to_float(r.get("close") or r.get("price")),
                "rs_rank":             _to_float(r.get("rs_rank")),
                "trend_score":         _to_float(pillar.get("trend")),
                "fundamental_score":   _to_float(pillar.get("fundamental")),
                "catalyst_score":      _to_float(pillar.get("catalyst")),
                "entry_score":         _to_float(pillar.get("entry")),
                "risk_score":          _to_float(pillar.get("risk_reward")),
                "recommendation":      str(r.get("recommendation") or ""),
            })
        except Exception as exc:
            logger.warning("append_scan_history: skipping row %s — %s", r.get("ticker"), exc)

    if not records:
        return 0

    df = pd.DataFrame(records)
    inserted = 0
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            # INSERT OR REPLACE semantics via DELETE + INSERT
            conn.execute(
                "DELETE FROM scan_history WHERE scan_date = ?", [today]
            )
            conn.execute("INSERT INTO scan_history SELECT * FROM df")
            inserted = len(records)
            conn.close()
            logger.info("[DB] scan_history: upserted %d rows for %s", inserted, today)
        except Exception as exc:
            logger.error("[DB] append_scan_history failed: %s", exc)

    return inserted


def append_rs_history(df_rs: pd.DataFrame, rank_date: date = None) -> int:
    """
    Persist RS ranking DataFrame to rs_history table.

    Parameters
    ----------
    df_rs     : DataFrame with columns ['Ticker', 'RS_Raw', 'RS_Rank']
    rank_date : defaults to today
    """
    if df_rs is None or df_rs.empty:
        return 0

    today = rank_date or date.today()

    try:
        df = df_rs[["Ticker", "RS_Raw", "RS_Rank"]].copy()
        df.columns = ["ticker", "rs_raw", "rs_rank"]
        df["rank_date"] = today
        df["ticker"]    = df["ticker"].str.upper()
        df["rs_raw"]    = pd.to_numeric(df["rs_raw"],  errors="coerce")
        df["rs_rank"]   = pd.to_numeric(df["rs_rank"], errors="coerce")
        df = df[["rank_date", "ticker", "rs_raw", "rs_rank"]].dropna(subset=["ticker"])
    except Exception as exc:
        logger.error("[DB] append_rs_history: data preparation failed — %s", exc)
        return 0

    inserted = 0
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            conn.execute(
                "DELETE FROM rs_history WHERE rank_date = ?", [today]
            )
            conn.execute("INSERT INTO rs_history SELECT * FROM df")
            inserted = len(df)
            conn.close()
            logger.info("[DB] rs_history: upserted %d rows for %s", inserted, today)
        except Exception as exc:
            logger.error("[DB] append_rs_history failed: %s", exc)

    return inserted


def append_market_env(env: dict, env_date: date = None) -> bool:
    """
    Persist market environment snapshot.

    Parameters
    ----------
    env      : dict from market_env.assess() result
    env_date : defaults to today
    """
    if not env:
        return False

    today = env_date or date.today()
    action_matrix = env.get("action_matrix") or {}

    record = {
        "env_date":          today,
        "regime":            str(env.get("regime") or ""),
        "distribution_days": _to_int(env.get("distribution_days")),
        "breadth_pct":       _to_float(env.get("breadth_pct")),
        "spy_close":         _to_float((env.get("index_data") or {}).get("SPY")),
        "qqq_close":         _to_float((env.get("index_data") or {}).get("QQQ")),
        "iwm_close":         _to_float((env.get("index_data") or {}).get("IWM")),
        "action_note":       str(action_matrix.get("note") or ""),
    }

    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            conn.execute(
                "DELETE FROM market_env_history WHERE env_date = ?", [today]
            )
            df = pd.DataFrame([record])
            conn.execute("INSERT INTO market_env_history SELECT * FROM df")
            conn.close()
            logger.info("[DB] market_env_history: saved for %s (%s)", today, record["regime"])
            return True
        except Exception as exc:
            logger.error("[DB] append_market_env failed: %s", exc)
            return False


def log_watchlist_action(ticker: str, action: str, grade: str = None,
                         sepa_score: float = None, note: str = "") -> bool:
    """
    Record a watchlist ADD/REMOVE/PROMOTE/DEMOTE event.
    action must be one of: 'ADD', 'REMOVE', 'PROMOTE', 'DEMOTE'
    """
    record = {
        "log_date":   date.today(),
        "ticker":     ticker.upper(),
        "action":     action.upper(),
        "grade":      grade or "",
        "sepa_score": _to_float(sepa_score),
        "note":       note or "",
    }
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            df = pd.DataFrame([record])
            conn.execute("INSERT OR REPLACE INTO watchlist_log SELECT * FROM df")
            conn.close()
            return True
        except Exception as exc:
            logger.error("[DB] log_watchlist_action failed: %s", exc)
            return False


def log_position_action(ticker: str, action: str, price: float = None,
                        shares: int = None, stop_price: float = None,
                        pnl_pct: float = None, hold_days: int = None,
                        note: str = "") -> bool:
    """
    Record a position OPEN/CLOSE event.
    action must be one of: 'OPEN', 'CLOSE'
    """
    record = {
        "log_date":   date.today(),
        "ticker":     ticker.upper(),
        "action":     action.upper(),
        "price":      _to_float(price),
        "shares":     _to_int(shares),
        "stop_price": _to_float(stop_price),
        "pnl_pct":    _to_float(pnl_pct),
        "hold_days":  _to_int(hold_days),
        "note":       note or "",
    }
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            df = pd.DataFrame([record])
            conn.execute("INSERT INTO position_log SELECT * FROM df")
            conn.close()
            return True
        except Exception as exc:
            logger.error("[DB] log_position_action failed: %s", exc)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Public read APIs  (for Flask API endpoints)
# ─────────────────────────────────────────────────────────────────────────────

def query_scan_trend(ticker: str, days: int = 90) -> pd.DataFrame:
    """
    Return scan_history for a ticker over the last N days.
    Used for score trend charts in analyze.html.
    """
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        df = conn.execute("""
            SELECT scan_date, sepa_score, vcp_grade, rs_rank, close_price,
                   tt_passed, recommendation
            FROM scan_history
            WHERE ticker = ?
              AND scan_date >= CURRENT_DATE - INTERVAL (?) DAY
            ORDER BY scan_date
        """, [ticker.upper(), days]).df()
        conn.close()
        return df
    except Exception as exc:
        logger.error("[DB] query_scan_trend failed: %s", exc)
        return pd.DataFrame()


def query_persistent_signals(min_appearances: int = 5, days: int = 30) -> pd.DataFrame:
    """
    Return tickers that appeared in scan results ≥ N times in last D days.
    Helps identify stable, recurring SEPA signals.
    """
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        df = conn.execute("""
            SELECT ticker,
                   COUNT(*)           AS appearances,
                   AVG(sepa_score)    AS avg_score,
                   MAX(sepa_score)    AS max_score,
                   MAX(scan_date)     AS last_seen,
                   MAX(vcp_grade)     AS best_vcp
            FROM scan_history
            WHERE scan_date >= CURRENT_DATE - INTERVAL (?) DAY
            GROUP BY ticker
            HAVING COUNT(*) >= ?
            ORDER BY avg_score DESC
        """, [days, min_appearances]).df()
        conn.close()
        return df
    except Exception as exc:
        logger.error("[DB] query_persistent_signals failed: %s", exc)
        return pd.DataFrame()


def query_rs_trend(ticker: str, days: int = 90) -> pd.DataFrame:
    """Return RS rank history for a ticker (for trend acceleration analysis)."""
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        df = conn.execute("""
            SELECT rank_date, rs_raw, rs_rank
            FROM rs_history
            WHERE ticker = ?
              AND rank_date >= CURRENT_DATE - INTERVAL (?) DAY
            ORDER BY rank_date
        """, [ticker.upper(), days]).df()
        conn.close()
        return df
    except Exception as exc:
        logger.error("[DB] query_rs_trend failed: %s", exc)
        return pd.DataFrame()


def query_market_env_history(days: int = 60) -> pd.DataFrame:
    """Return market regime history for the last N days."""
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        df = conn.execute("""
            SELECT env_date, regime, distribution_days, breadth_pct,
                   spy_close, qqq_close, iwm_close
            FROM market_env_history
            WHERE env_date >= CURRENT_DATE - INTERVAL (?) DAY
            ORDER BY env_date DESC
        """, [days]).df()
        conn.close()
        return df
    except Exception as exc:
        logger.error("[DB] query_market_env_history failed: %s", exc)
        return pd.DataFrame()


def db_stats() -> dict:
    """Return row counts for all tables (for health check / UI display)."""
    tables = ["scan_history", "rs_history", "market_env_history",
              "watchlist_log", "position_log"]
    stats = {}
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        for t in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                stats[t] = row[0] if row else 0
            except Exception:
                stats[t] = -1
        # DB file size
        stats["db_size_mb"] = round(_DB_PATH.stat().st_size / 1024 / 1024, 2) \
                              if _DB_PATH.exists() else 0
        conn.close()
    except Exception as exc:
        logger.error("[DB] db_stats failed: %s", exc)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Internal type coercion helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        import math
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
