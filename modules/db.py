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
import json
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
    
    # Create indexes for better query performance
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_history(scan_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_ticker ON scan_history(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_date ON rs_history(rank_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_ticker ON rs_history(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_date ON market_env_history(env_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_date ON watchlist_log(log_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_position_date ON position_log(log_date)")
    except:
        pass  # Indexes might already exist

    # Phase 2: Watchlist persistent storage
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_store (
            ticker      VARCHAR PRIMARY KEY,
            grade       VARCHAR NOT NULL,  -- 'A', 'B', or 'C'
            sepa_score  DOUBLE,
            added_date  DATE,
            note        VARCHAR
        )
    """)

    # Phase 2: Open positions tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            ticker          VARCHAR PRIMARY KEY,
            buy_price       DOUBLE  NOT NULL,
            shares          INTEGER NOT NULL,
            stop_loss       DOUBLE  NOT NULL,
            stop_pct        DOUBLE,
            target          DOUBLE,
            rr              DOUBLE,
            risk_dollar     DOUBLE,
            entry_date      DATE,
            last_stop_update DATE,
            trailing_stop   DOUBLE,
            pnl_pct         DOUBLE,
            note            VARCHAR
        )
    """)

    # Phase 2: Closed positions history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS closed_positions (
            ticker          VARCHAR NOT NULL,
            entry_date      DATE,
            exit_date       DATE,
            buy_price       DOUBLE,
            exit_price      DOUBLE,
            shares          INTEGER,
            pnl_amount      DOUBLE,
            pnl_pct         DOUBLE,
            hold_days       INTEGER,
            exit_reason     VARCHAR,
            note            VARCHAR
        )
    """)

    # Phase 2: Fundamentals cache (JSON serialized DataFrames)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            ticker          VARCHAR PRIMARY KEY,
            last_update     DATE,
            data_json       VARCHAR
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
            # 處理多種欄位名稱格式（相容舊版本）
            sepa_score = _to_float(r.get("sepa_score") or r.get("total_score"))
            tt_passed = _to_int(r.get("tt_passed") or r.get("tt_score"))
            tt_checks = r.get("tt_checks") or {}
            tt_total = len([v for v in tt_checks.values() if v is True]) if isinstance(tt_checks, dict) else 10
            
            # 直接欄位或從 pillar_scores 字典中開取
            pillar = r.get("pillar_scores") or {}
            trend = _to_float(pillar.get("trend") or r.get("trend_score"))
            fundamental = _to_float(pillar.get("fundamental") or r.get("fundamental_score"))
            catalyst = _to_float(pillar.get("catalyst") or r.get("catalyst_score"))
            entry = _to_float(pillar.get("entry") or r.get("entry_score"))
            risk = _to_float(pillar.get("risk_reward") or r.get("rr_score"))

            records.append({
                "scan_date":           today,
                "ticker":              str(r.get("ticker", "")).upper(),
                "sepa_score":          sepa_score,
                "vcp_grade":           str(r.get("vcp_grade") or ""),
                "tt_passed":           tt_passed,
                "tt_total":            tt_total,
                "close_price":         _to_float(r.get("close") or r.get("price")),
                "rs_rank":             _to_float(r.get("rs_rank")),
                "trend_score":         trend,
                "fundamental_score":   fundamental,
                "catalyst_score":      catalyst,
                "entry_score":         entry,
                "risk_score":          risk,
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
    
    # 從 {spy_trend, qqq_trend, iwm_trend} 中提取收盤價
    spy_trend = env.get("spy_trend") or {}
    qqq_trend = env.get("qqq_trend") or {}
    iwm_trend = env.get("iwm_trend") or {}

    record = {
        "env_date":          today,
        "regime":            str(env.get("regime") or ""),
        "distribution_days": _to_int(env.get("distribution_days")),
        "breadth_pct":       _to_float(env.get("breadth_pct")),
        "spy_close":         _to_float(spy_trend.get("close")),
        "qqq_close":         _to_float(qqq_trend.get("close")),
        "iwm_close":         _to_float(iwm_trend.get("close")),
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
              "watchlist_log", "position_log", "watchlist_store",
              "open_positions", "closed_positions", "fundamentals_cache"]
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
# Phase 2: Watchlist persistence APIs
# ─────────────────────────────────────────────────────────────────────────────

def wl_load() -> dict:
    """Load watchlist from DuckDB, fallback to JSON if DB fails."""
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            df = conn.execute("""
                SELECT ticker, grade, sepa_score, added_date, note
                FROM watchlist_store
                ORDER BY grade, ticker
            """).df()
            conn.close()
            
            if df.empty:
                return {"A": {}, "B": {}, "C": {}}
            
            result = {"A": {}, "B": {}, "C": {}}
            for _, row in df.iterrows():
                ticker = row["ticker"]
                grade = str(row["grade"]) or "C"
                result[grade][ticker] = {
                    "sepa_score": _to_float(row["sepa_score"]),
                    "added_date": str(row["added_date"]) if row["added_date"] else None,
                    "note": str(row["note"]) or "",
                }
            logger.info("[DB] wl_load: loaded %d tickers", len(df))
            return result
        except Exception as exc:
            logger.warning("[DB] wl_load failed (%s), attempting JSON fallback", exc)
            # Fallback to JSON
            try:
                from modules.watchlist import _load as wl_json_load
                data = wl_json_load()
                logger.info("[DB] wl_load: JSON fallback successful")
                return data
            except Exception as exc2:
                logger.error("[DB] wl_load JSON fallback failed: %s", exc2)
                return {"A": {}, "B": {}, "C": {}}


def wl_save(data: dict) -> bool:
    """Save watchlist to DuckDB, with JSON backup if DB_JSON_BACKUP_ENABLED."""
    if not data:
        return False

    records = []
    for grade in ["A", "B", "C"]:
        for ticker, info in data.get(grade, {}).items():
            records.append({
                "ticker":      ticker.upper(),
                "grade":       grade,
                "sepa_score":  _to_float(info.get("sepa_score")),
                "added_date":  info.get("added_date"),
                "note":        info.get("note", ""),
            })

    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            conn.execute("DELETE FROM watchlist_store")
            if records:
                df = pd.DataFrame(records)
                conn.execute("INSERT INTO watchlist_store SELECT * FROM df")
            conn.close()
            logger.info("[DB] wl_save: saved %d tickers", len(records))
            
            # JSON backup (Phase 2 safety)
            if C.DB_JSON_BACKUP_ENABLED:
                _backup_json("watchlist", data)
            return True
        except Exception as exc:
            logger.error("[DB] wl_save failed: %s", exc)
            # Force JSON backup on DB failure
            try:
                from modules.watchlist import _save as wl_json_save
                wl_json_save(data)
                logger.warning("[DB] wl_save: saved to JSON fallback")
            except Exception as exc2:
                logger.error("[DB] wl_save: JSON fallback failed — %s", exc2)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Position persistence APIs
# ─────────────────────────────────────────────────────────────────────────────

def pos_load() -> dict:
    """Load positions from DuckDB, fallback to JSON if DB fails."""
    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            
            # Load open positions
            df_open = conn.execute("SELECT * FROM open_positions").df()
            open_dict = {}
            if not df_open.empty:
                for _, row in df_open.iterrows():
                    ticker = row["ticker"]
                    
                    # Helper to extract date string (handle pandas NaT/None)
                    def parse_date(val):
                        if val is None:
                            return None
                        s = str(val)
                        if s == "NaT" or s == "None":
                            return None
                        # Extract just the date part (YYYY-MM-DD)
                        if " " in s:
                            return s.split()[0]
                        return s if s else None
                    
                    open_dict[ticker] = {
                        "buy_price": _to_float(row["buy_price"]),
                        "shares": _to_int(row["shares"]),
                        "stop_loss": _to_float(row["stop_loss"]),
                        "stop_pct": _to_float(row["stop_pct"]),
                        "target": _to_float(row["target"]),
                        "rr": _to_float(row["rr"]),
                        "risk_dollar": _to_float(row["risk_dollar"]),
                        "entry_date": parse_date(row["entry_date"]),
                        "last_stop_update": parse_date(row["last_stop_update"]),
                        "trailing_stop": _to_float(row["trailing_stop"]),
                        "pnl_pct": _to_float(row["pnl_pct"]),
                        "note": str(row["note"]) or "",
                    }
            
            # Load closed positions
            df_closed = conn.execute("SELECT * FROM closed_positions ORDER BY exit_date DESC").df()
            closed_list = []
            if not df_closed.empty:
                for _, row in df_closed.iterrows():
                    closed_list.append({
                        "ticker": row["ticker"],
                        "entry_date": str(row["entry_date"]) if row["entry_date"] else None,
                        "exit_date": str(row["exit_date"]) if row["exit_date"] else None,
                        "buy_price": _to_float(row["buy_price"]),
                        "exit_price": _to_float(row["exit_price"]),
                        "shares": _to_int(row["shares"]),
                        "pnl_amount": _to_float(row["pnl_amount"]),
                        "pnl_pct": _to_float(row["pnl_pct"]),
                        "hold_days": _to_int(row["hold_days"]),
                        "exit_reason": str(row["exit_reason"]) or "",
                        "note": str(row["note"]) or "",
                    })
            
            conn.close()
            
            # Get account_high from most recent closed position or use default
            account_high = C.ACCOUNT_SIZE
            if closed_list:
                # Could compute account high from equity curve, using default for now
                pass
            
            result = {
                "positions": open_dict,
                "closed": closed_list,
                "account_high": account_high,
            }
            logger.info("[DB] pos_load: loaded %d open, %d closed", len(open_dict), len(closed_list))
            return result
        except Exception as exc:
            logger.warning("[DB] pos_load failed (%s), attempting JSON fallback", exc)
            # Fallback to JSON
            try:
                from modules.position_monitor import _load as pos_json_load
                data = pos_json_load()
                logger.info("[DB] pos_load: JSON fallback successful")
                return data
            except Exception as exc2:
                logger.error("[DB] pos_load JSON fallback failed: %s", exc2)
                return {"positions": {}, "closed": [], "account_high": C.ACCOUNT_SIZE}


def pos_save(data: dict) -> bool:
    """Save positions to DuckDB, with JSON backup if DB_JSON_BACKUP_ENABLED."""
    if not data:
        return False

    open_records = []
    for ticker, info in data.get("positions", {}).items():
        open_records.append({
            "ticker": ticker.upper(),
            "buy_price": _to_float(info.get("buy_price")),
            "shares": _to_int(info.get("shares")),
            "stop_loss": _to_float(info.get("stop_loss")),
            "stop_pct": _to_float(info.get("stop_pct")),
            "target": _to_float(info.get("target")),
            "rr": _to_float(info.get("rr")),
            "risk_dollar": _to_float(info.get("risk_dollar")),
            "entry_date": info.get("entry_date"),
            "last_stop_update": info.get("last_stop_update"),
            "trailing_stop": _to_float(info.get("trailing_stop")),
            "pnl_pct": _to_float(info.get("pnl_pct")),
            "note": info.get("note", ""),
        })

    closed_records = []
    for closed in data.get("closed", []):
        closed_records.append({
            "ticker": str(closed.get("ticker", "")).upper(),
            "entry_date": closed.get("entry_date"),
            "exit_date": closed.get("exit_date"),
            "buy_price": _to_float(closed.get("buy_price")),
            "exit_price": _to_float(closed.get("exit_price")),
            "shares": _to_int(closed.get("shares")),
            "pnl_amount": _to_float(closed.get("pnl_amount")),
            "pnl_pct": _to_float(closed.get("pnl_pct")),
            "hold_days": _to_int(closed.get("hold_days")),
            "exit_reason": str(closed.get("exit_reason", "")),
            "note": str(closed.get("note", "")),
        })

    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            
            # Save open positions
            conn.execute("DELETE FROM open_positions")
            if open_records:
                df = pd.DataFrame(open_records)
                conn.execute("INSERT INTO open_positions SELECT * FROM df")
            
            # Save closed positions (append-only, don't delete)
            if closed_records:
                df = pd.DataFrame(closed_records)
                conn.execute("INSERT INTO closed_positions SELECT * FROM df")
            
            conn.close()
            logger.info("[DB] pos_save: saved %d open, %d closed", len(open_records), len(closed_records))
            
            # JSON backup (Phase 2 safety)
            if C.DB_JSON_BACKUP_ENABLED:
                _backup_json("positions", data)
            return True
        except Exception as exc:
            logger.error("[DB] pos_save failed: %s", exc)
            # Force JSON backup on DB failure
            try:
                from modules.position_monitor import _save as pos_json_save
                pos_json_save(data)
                logger.warning("[DB] pos_save: saved to JSON fallback")
            except Exception as exc2:
                logger.error("[DB] pos_save: JSON fallback failed — %s", exc2)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Fundamentals cache APIs
# ─────────────────────────────────────────────────────────────────────────────

def fund_cache_get(ticker: str) -> Optional[dict]:
    """Retrieve fundamentals from cache (as dict, rehydrated from JSON)."""
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT data_json FROM fundamentals_cache WHERE ticker = ?",
            [ticker.upper()]
        ).fetchone()
        conn.close()
        
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception as exc:
                logger.warning("[DB] fund_cache_get: JSON deserialize failed — %s", exc)
        return None
    except Exception as exc:
        logger.warning("[DB] fund_cache_get failed: %s", exc)
        return None


def fund_cache_set(ticker: str, data: dict) -> bool:
    """Store fundamentals in cache (serialized as JSON string)."""
    if not data:
        return False

    try:
        data_json = json.dumps(data, default=str)  # Serialize with fallback for non-JSON types
    except Exception as exc:
        logger.error("[DB] fund_cache_set: JSON serialize failed — %s", exc)
        return False

    with _lock:
        try:
            conn = _get_conn()
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO fundamentals_cache (ticker, last_update, data_json) VALUES (?, ?, ?)",
                [ticker.upper(), date.today(), data_json]
            )
            conn.close()
            logger.info("[DB] fund_cache_set: cached for %s", ticker)
            return True
        except Exception as exc:
            logger.error("[DB] fund_cache_set failed: %s", exc)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Helper: JSON backup
# ─────────────────────────────────────────────────────────────────────────────

def _backup_json(entity_type: str, data: dict):
    """Write backup JSON if DB_JSON_BACKUP_ENABLED."""
    try:
        backup_dir = ROOT / C.DB_JSON_BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"{entity_type}_{date.today().isoformat()}.json"
        backup_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("[DB] _backup_json failed: %s", exc)


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
