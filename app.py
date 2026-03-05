"""
app.py  —  Minervini SEPA Web Interface
════════════════════════════════════════
Launch:   python app.py
Browser:  http://localhost:5000
"""

import sys
import os
import json
import uuid
import logging
import threading
import subprocess
import webbrowser
import signal
import atexit
import time

# Force UTF-8 stdout/stderr on Windows (avoids cp950 encode errors for ✓ ✗ → etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Any
import pandas as pd

from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trader_config as C

# ── logging ──────────────────────────────────────────────────────────────────
import math as _math
_LOG_DIR = ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)
# Global root logger (for framework/general events)
_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.DEBUG)  # FIXED: Allow DEBUG level to console
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[_stream_handler],  # No global file handler; each scan gets its own
)
# Suppress high-volume DEBUG noise from third-party libraries
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("yfinance.base").setLevel(logging.WARNING)
logging.getLogger("yfinance.utils").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("multitasking").setLevel(logging.WARNING)
logging.getLogger("charset_normalizer").setLevel(logging.WARNING)
logging.getLogger("numba").setLevel(logging.WARNING)
logging.getLogger("numba.core").setLevel(logging.WARNING)

# Create app-level logger for console output
logger = logging.getLogger(__name__)

# Per-scan file logging (attached during api_scan_run)
_scan_file_handlers: dict = {}  # {job_id: FileHandler}
_scan_log_paths: dict = {}      # {job_id: Path} — for post-scan notification

app = Flask(__name__)
app.secret_key = "minervini-sepa-2026"
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Reload templates on-disk without server restart

# ── Telegram Bot State ─────────────────────────────────────────────────────
_tg_enabled = C.TG_ENABLED  # Track current enable/disable state
_tg_thread = None           # Will be set in __main__


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory job store  (scan / market are slow — run in background thread)
# ═══════════════════════════════════════════════════════════════════════════════

_jobs: dict = {}          # { job_id: {"status": "pending|done|error", "result": ...} }
_qm_analyze_cache: dict = {}  # ticker -> clean result dict (for /htmx/qm/analyze/result)
_ml_analyze_cache: dict = {}  # ticker -> clean result dict (for /htmx/ml/analyze/result)
_jobs_lock = threading.Lock()
_cancel_events: dict = {}  # { job_id: threading.Event }

# ── API response cache (for expensive queries) ─────────────────────────────────
_cache: dict = {}         # { cache_key: {"data": ..., "time": timestamp} }
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutes in seconds

def _get_cached(key):
    """Get cached response if still fresh (within TTL)."""
    with _cache_lock:
        if key in _cache:
            import time
            age = time.time() - _cache[key]["time"]
            if age < _CACHE_TTL:
                return _cache[key]["data"]
            else:
                del _cache[key]
    return None

def _set_cache(key, data):
    """Cache a response."""
    import time
    with _cache_lock:
        _cache[key] = {"data": data, "time": time.time()}

# ── last scan persistence ─────────────────────────────────────────────────────
_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "last_scan.json"

def _save_last_scan(rows: list, all_rows: Optional[list] = None):
    try:
        data = {"saved_at": datetime.now().isoformat(),
                "count": len(rows), "rows": rows,
                "all_scored_count": len(all_rows) if all_rows else len(rows),
                "all_scored": all_rows or []}
        _LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        # Also save dated CSV (passed stocks only)
        import csv, io
        if rows:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=[
                k for k in rows[0] if not isinstance(rows[0][k], dict)
            ])
            writer.writeheader()
            for r in rows:
                writer.writerow({k: v for k, v in r.items()
                                 if not isinstance(v, dict)})
            csv_path = ROOT / C.DATA_DIR / f"sepa_scan_{date.today().isoformat()}.csv"
            csv_path.write_text(buf.getvalue(), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save last_scan: {exc}")

    # ── DuckDB 歷史記錄（非破壞性追加）────────────────────────────────
    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_scan_history
            # 同時儲存通過及所有評分股票（包括未通過的，供趨勢分析）
            all_to_save = (all_rows or []) + rows
            # 去重（以 ticker 為 key）
            seen = set()
            deduped = []
            for r in all_to_save:
                if r.get("ticker") not in seen:
                    seen.add(r.get("ticker"))
                    deduped.append(r)
            append_scan_history(deduped)
            # Invalidate cache since we have new scan data
            with _cache_lock:
                if "watchlist-history" in _cache:
                    del _cache["watchlist-history"]
        except Exception as exc:
            logging.warning(f"DB scan_history write skipped: {exc}")


def _load_last_scan() -> dict:
    try:
        if _LAST_SCAN_FILE.exists():
            return json.loads(_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── QM last scan persistence ──────────────────────────────────────────────────
_QM_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "qm_last_scan.json"


def _save_qm_last_scan(rows: list, all_rows: Optional[list] = None):
    try:
        data = {"saved_at": datetime.now().isoformat(),
                "count": len(rows), "rows": rows,
                "all_scored_count": len(all_rows) if all_rows else len(rows),
                "all_scored": all_rows or []}
        _QM_LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save qm_last_scan: {exc}")

    # ── DuckDB 歷史記錄（非破壞性追加）────────────────────────────────────
    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_qm_scan_history
            all_to_save = (all_rows or []) + rows
            seen: set = set()
            deduped = []
            for r in all_to_save:
                if r.get("ticker") not in seen:
                    seen.add(r.get("ticker"))
                    deduped.append(r)
            append_qm_scan_history(deduped)
        except Exception as exc:
            logging.warning(f"DB qm_scan_history write skipped: {exc}")


def _load_qm_last_scan() -> dict:
    try:
        if _QM_LAST_SCAN_FILE.exists():
            return json.loads(_QM_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── Martin Luk (ML) last scan persistence ─────────────────────────────────────
_ML_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "ml_last_scan.json"


def _save_ml_last_scan(rows: list, all_rows: Optional[list] = None):
    try:
        data = {"saved_at": datetime.now().isoformat(),
                "count": len(rows), "rows": rows,
                "all_scored_count": len(all_rows) if all_rows else len(rows),
                "all_scored": all_rows or []}
        _ML_LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save ml_last_scan: {exc}")

    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_ml_scan_history
            all_to_save = (all_rows or []) + rows
            seen: set = set()
            deduped = []
            for r in all_to_save:
                if r.get("ticker") not in seen:
                    seen.add(r.get("ticker"))
                    deduped.append(r)
            append_ml_scan_history(deduped)
        except Exception as exc:
            logging.warning(f"DB ml_scan_history write skipped: {exc}")


def _load_ml_last_scan() -> dict:
    try:
        if _ML_LAST_SCAN_FILE.exists():
            return json.loads(_ML_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── Combined last scan persistence ────────────────────────────────────────────
_COMBINED_LAST_FILE = ROOT / C.DATA_DIR / "combined_last_scan.json"


def _save_combined_last(sepa_rows, qm_rows, market_env, timing,
                        sepa_csv="", qm_csv="", ml_rows=None, ml_csv=""):
    """Persist last combined scan summary for dashboard display."""
    if ml_rows is None:
        ml_rows = []
    try:
        # Compute a ≥4-star filtered count to match the default display filter
        # in combined_scan.html (minStar default = 4)
        _default_star = 4.0
        qm_count_4star = sum(
            1 for r in qm_rows
            if float(r.get("qm_star") or r.get("stars") or 0) >= _default_star
        )
        _default_ml_star = 3.0
        ml_count_3star = sum(
            1 for r in ml_rows
            if float(r.get("ml_star") or r.get("stars") or 0) >= _default_ml_star
        )
        data = {
            "saved_at":      datetime.now().isoformat(),
            "sepa_count":    len(sepa_rows),
            "qm_count":      len(qm_rows),        # raw total
            "qm_count_4star": qm_count_4star,     # filtered at ≥4★ (default)
            "ml_count":      len(ml_rows),
            "ml_count_3star": ml_count_3star,
            "sepa_rows":     sepa_rows[:20],   # top 20 sufficient for dashboard
            "qm_rows":       qm_rows[:20],
            "ml_rows":       ml_rows[:20],
            "market_env":    market_env,
            "timing":        timing,
            "sepa_csv":      sepa_csv,
            "qm_csv":        qm_csv,
            "ml_csv":        ml_csv,
        }
        _COMBINED_LAST_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not save combined_last_scan: %s", exc)


def _load_combined_last() -> dict:
    try:
        if _COMBINED_LAST_FILE.exists():
            return json.loads(_COMBINED_LAST_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_combined_scan_csv(sepa_df, qm_df, ml_df=None, scan_ts=None) -> tuple:
    """
    Save combined scan results to scan_results/ folder.
    Creates three timestamped CSV files per run:
      scan_results/combined_sepa_YYYYMMDD_HHMMSS.csv  — SEPA passed stocks
      scan_results/combined_qm_YYYYMMDD_HHMMSS.csv    — QM passed stocks
      scan_results/combined_ml_YYYYMMDD_HHMMSS.csv    — ML passed stocks

    Returns (sepa_path, qm_path, ml_path) as relative path strings for display.
    """
    from datetime import datetime as _dt
    sepa_path = qm_path = ml_path = ""
    try:
        out_dir = ROOT / "scan_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts_str = (scan_ts or _dt.now()).strftime("%Y%m%d_%H%M%S")
        keep = getattr(C, "QM_SCAN_RESULTS_KEEP", 30)

        for label, df in [("combined_sepa", sepa_df), ("combined_qm", qm_df), ("combined_ml", ml_df)]:
            fpath = out_dir / f"{label}_{ts_str}.csv"
            if df is not None and hasattr(df, "to_csv") and not df.empty:
                df.to_csv(fpath, index=False)
                logging.info("[Combined Scan] Saved %d rows → %s", len(df), fpath.name)
            else:
                fpath.write_text("(no results)\n", encoding="utf-8")
                logging.info("[Combined Scan] No data for %s — wrote empty placeholder", fpath.name)
            if label == "combined_sepa":
                sepa_path = f"scan_results/{fpath.name}"
            elif label == "combined_qm":
                qm_path = f"scan_results/{fpath.name}"
            else:
                ml_path = f"scan_results/{fpath.name}"
            # Rotate: keep only most recent N files of this label type
            existing = sorted(out_dir.glob(f"{label}_*.csv"), reverse=True)
            for old in existing[keep:]:
                try:
                    old.unlink()
                except Exception:
                    pass
    except Exception as exc:
        logging.warning("Could not save combined scan CSVs: %s", exc)
    return sepa_path, qm_path, ml_path


def _new_job() -> str:
    jid = str(uuid.uuid4())[:8]
    ev = threading.Event()
    with _jobs_lock:
        _jobs[jid] = {"status": "pending", "result": None, "error": None,
                      "started": datetime.now().isoformat(),
                      "progress": {"stage": "Initialising…", "pct": 0,
                                   "msg": "", "ticker": ""}}
        _cancel_events[jid] = ev
    return jid

def _get_cancel(jid: str) -> threading.Event:
    return _cancel_events.get(jid, threading.Event())


def _sanitize_for_json(obj, depth=0, max_depth=5):
    """
    Recursively sanitize object to ensure it's JSON-serializable.
    Safely converts any non-serializable types to None or string representation.
    """
    import pandas as pd
    import numpy as np
    
    if depth > max_depth:
        return None
    
    # Handle None, bool, int, float, str
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    
    # Handle float (including NaN, inf)
    if isinstance(obj, (float, np.floating)):
        if pd.isna(obj) or np.isnan(obj):
            return None
        if np.isinf(obj):
            return str(obj)
        return float(obj)
    
    # Handle numpy types
    if isinstance(obj, (np.integer, np.bool_)):
        return obj.item()
    
    # Skip DataFrames and Series entirely
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    
    # Handle list
    if isinstance(obj, list):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    # Handle dict
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v, depth+1, max_depth) 
                for k, v in obj.items()}
    
    # Handle tuple → convert to list
    if isinstance(obj, tuple):
        return [_sanitize_for_json(item, depth+1, max_depth) for item in obj]
    
    # Fallback: convert to string
    return str(obj)


def _finish_job(jid: str, result: Any = None, error: Optional[str] = None, log_file: str = ""):
    # Sanitize result before storing to ensure JSON serialization won't fail
    if result is not None:
        import sys
        try:
            result = _sanitize_for_json(result)
            logging.info(f"[FINISH_JOB {jid}] Result sanitized successfully")
        except Exception as e:
            logging.error(f"[FINISH_JOB {jid}] Error sanitizing result: {e}", exc_info=True)
            result = {"error": "Result sanitization failed: " + str(e)}
    
    with _jobs_lock:
        _jobs[jid]["status"] = "done" if error is None else "error"
        _jobs[jid]["result"] = result
        _jobs[jid]["error"]  = error
        _jobs[jid]["finished"] = datetime.now().isoformat()
        _jobs[jid]["log_file"] = log_file


_market_job_ids: set = set()  # Track which job IDs are market assessments
_qm_job_ids: set = set()      # Track which job IDs are QM scans
_ml_job_ids: set = set()       # Track which job IDs are ML scans


def _get_job(jid: str) -> dict:
    """Return a copy of the job dict, merging live progress if still pending."""
    with _jobs_lock:
        job = dict(_jobs.get(jid, {"status": "not_found"}))
    if job.get("status") == "pending":
        if jid in _market_job_ids:
            try:
                from modules.market_env import get_market_progress
                job["progress"] = get_market_progress()
            except Exception:
                pass
        elif jid in _qm_job_ids:
            try:
                from modules.qm_screener import get_qm_scan_progress
                job["progress"] = get_qm_scan_progress()
            except Exception:
                pass
        elif jid in _ml_job_ids:
            try:
                from modules.ml_screener import get_ml_scan_progress
                job["progress"] = get_ml_scan_progress()
            except Exception:
                pass
        else:
            try:
                from modules.screener import get_scan_progress
                job["progress"] = get_scan_progress()
            except Exception:
                pass
    return job


def _clean(obj):
    """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
    import pandas as pd
    
    if obj is None:
        return None
    
    # Skip DataFrames and Series early - don't try to process them
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    
    # Try numpy scalar extraction FIRST (before isinstance checks)
    # This handles np.int64, np.float64, np.bool_, etc.
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        try:
            val = obj.item()
            return _clean(val)  # Recursively clean the extracted value
        except (TypeError, ValueError, AttributeError):
            return None
    
    if isinstance(obj, bool):
        return obj
    
    if isinstance(obj, (int, float, str)):
        # Handle NaN floats
        if isinstance(obj, float):
            try:
                if obj != obj:  # NaN check
                    return None
            except (TypeError, ValueError):
                pass
        return obj
    
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            # Skip DataFrame/Series values in dicts
            if isinstance(v, (pd.DataFrame, pd.Series)):
                continue
            cleaned_v = _clean(v)
            if cleaned_v is not None or v is None:
                cleaned[k] = cleaned_v
        return cleaned
    
    if isinstance(obj, (list, tuple)):
        return [_clean(i) for i in obj]
    
    # For other types, try to convert to string representation
    try:
        return str(obj)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: load persisted data
# ═══════════════════════════════════════════════════════════════════════════════

def _load_watchlist() -> dict:
    """Load watchlist from modules (supports both DuckDB and JSON)."""
    try:
        from modules.watchlist import _load
        return _load()
    except Exception as exc:
        logger.warning("Failed to load watchlist from modules: %s", exc)
        # Fallback to direct JSON read
        p = ROOT / C.DATA_DIR / "watchlist.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"A": {}, "B": {}, "C": {}}


def _load_positions() -> dict:
    """Load positions from modules (supports both DuckDB and JSON)."""
    try:
        from modules.position_monitor import _load
        data = _load()
        return data.get("positions", {})
    except Exception as exc:
        logger.warning("Failed to load positions from modules: %s", exc)
        # Fallback to direct JSON read
        p = ROOT / C.DATA_DIR / "positions.json"
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                return raw.get("positions", {})
            except Exception:
                pass
    return {}


def _latest_report() -> Optional[Path]:
    reports = sorted(Path(ROOT / C.REPORTS_DIR).glob("sepa_report_*.html"),
                     key=lambda f: f.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


# ═══════════════════════════════════════════════════════════════════════════════
# Page routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def dashboard():
    wl   = _load_watchlist()
    pos  = _load_positions()
    wl_counts = {g: len(v) for g, v in wl.items()}
    return render_template("dashboard.html",
                           wl=wl, wl_counts=wl_counts,
                           positions=pos, account_size=C.ACCOUNT_SIZE,
                           today=date.today().isoformat())


@app.route("/scan")
def scan_page():
    return render_template("scan.html")


@app.route("/combined")
def combined_scan_page():
    return render_template("combined_scan.html")


@app.route("/analyze")
def analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("analyze.html", prefill=ticker)


@app.route("/watchlist")
def watchlist_page():
    wl = _load_watchlist()
    return render_template("watchlist.html", wl=wl)


@app.route("/positions")
def positions_page():
    pos = _load_positions()
    return render_template("positions.html", positions=pos,
                           account_size=C.ACCOUNT_SIZE)


@app.route("/market")
def market_page():
    return render_template("market.html")


@app.route("/vcp")
def vcp_page():
    ticker = request.args.get("ticker", "")
    return render_template("vcp.html", prefill=ticker)


@app.route("/guide")
def guide_page():
    guide_path = ROOT / "docs" / "GUIDE.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "Guide not found."
    return render_template("guide.html", content=content)


@app.route("/calc")
def calc_page():
    """Position Sizing Calculator — all three strategies."""
    return render_template("calc.html", account_size=C.ACCOUNT_SIZE)


# ─── Qullamaggie (QM) page routes ────────────────────────────────────────────

@app.route("/qm/scan")
def qm_scan_page():
    return render_template("qm_scan.html")


@app.route("/qm/analyze")
def qm_analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("qm_analyze.html", prefill=ticker)


@app.route("/qm/guide")
def qm_guide_page():
    guide_path = ROOT / "docs" / "QullamaggieStockguide.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "QM Guide not found."
    return render_template("qm_guide.html", content=content)


# ─── Martin Luk (ML) page routes ─────────────────────────────────────────────

@app.route("/ml/scan")
def ml_scan_page():
    return render_template("ml_scan.html")


@app.route("/ml/test-minimal")
def ml_analyze_minimal():
    """Minimal test page to verify template rendering works"""
    return render_template("ml_analyze_minimal.html")


@app.route("/ml/diagnostic")
def ml_analyze_diagnostic():
    """Diagnostic page for debugging blank page issues"""
    return render_template("ml_analyze_diagnostic.html")


@app.route("/ml/analyze")
def ml_analyze_page():
    ticker = request.args.get("ticker", "")
    return render_template("ml_analyze.html", prefill=ticker)


@app.route("/ml/guide")
def ml_guide_page():
    guide_path = ROOT / "docs" / "MartinLukStockGuidePart1.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "ML Guide not found."
    return render_template("ml_guide.html", content=content)


# ═══════════════════════════════════════════════════════════════════════════════
# API – Scan
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/scan/run", methods=["POST"])
def api_scan_run():
    data        = request.get_json(silent=True) or {}
    refresh_rs    = data.get("refresh_rs", False)
    stage1_source = data.get("stage1_source") or None  # None → use C.STAGE1_SOURCE
    jid           = _new_job()

    cancel_ev = _get_cancel(jid)

    # Create a unique log file for this scan run
    scan_log_file = _LOG_DIR / f"scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # Attach handler to NAMED loggers only (not root) to avoid duplicate lines
    _SCAN_LOGGERS = ["modules.screener", "modules.rs_ranking", "modules.data_pipeline"]
    for logger_name in _SCAN_LOGGERS:
        logger_obj = logging.getLogger(logger_name)
        logger_obj.addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.screener import run_scan, set_scan_cancel
            set_scan_cancel(cancel_ev)
            scan_result = run_scan(refresh_rs=refresh_rs, stage1_source=stage1_source)
            # run_scan returns (df_passed, df_all) tuple
            if isinstance(scan_result, tuple):
                df_passed, df_all = scan_result
            else:
                df_passed = scan_result
                df_all = scan_result

            def _to_rows(df):
                import pandas as pd
                
                if df is None or not hasattr(df, "to_dict"):
                    return []
                if hasattr(df, "empty") and df.empty:
                    return []
                
                try:
                    # Convert row-by-row to avoid DataFrame comparison issues
                    records = []
                    for idx, row in df.iterrows():
                        record = {}
                        for col, val in row.items():
                            # Skip DataFrame/Series/complex objects
                            if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                                continue
                            # Convert NaN/None to None
                            if pd.isna(val):
                                record[col] = None
                            else:
                                record[col] = val
                        records.append(record)
                    return _clean(records)
                except Exception as e:
                    logging.error(f"[_to_rows] SEPA conversion failed: {e}", exc_info=True)
                    return []

            rows = _to_rows(df_passed)
            all_rows = _to_rows(df_all)
            _save_last_scan(rows, all_rows=all_rows)
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
        except Exception as exc:
            logging.exception("Scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            # Clean up scan-specific handler
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for logger_name in _SCAN_LOGGERS:
                    logging.getLogger(logger_name).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    try:
        response = jsonify({"job_id": jid})
        logging.info(f"[SCAN {jid}] Initial response created successfully")
        return response
    except Exception as e:
        logging.error(f"[SCAN {jid}] Error creating initial response: {e}", exc_info=True)
        return jsonify({"job_id": jid, "error": "Response creation error"}), 500


@app.route("/api/scan/cancel/<jid>", methods=["POST"])
def api_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…",
                                           "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@app.route("/api/scan/last", methods=["GET"])
def api_scan_last():
    """Return passed-only or all-scored results depending on ?include_all=1."""
    data = _load_last_scan()
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        # Strip the heavy all_scored list to reduce payload
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@app.route("/api/scan/cache-info", methods=["GET"])
def api_scan_cache_info():
    """Return cache status to help users understand expected scan speed."""
    try:
        today = date.today().isoformat()
        cache_dir = ROOT / C.PRICE_CACHE_DIR

        # RS cache check — read first line only (fast, no pandas)
        rs_file = ROOT / C.DATA_DIR / "rs_cache.csv"
        rs_cached = False
        rs_count  = 0
        if rs_file.exists():
            try:
                with open(rs_file, "r", encoding="utf-8") as f:
                    header = f.readline().strip()   # "Ticker,RS_Raw,RS_Rank,CacheDate"
                    cols = header.split(",")
                    date_idx = cols.index("CacheDate") if "CacheDate" in cols else -1
                    line_count = 0
                    cached_date = ""
                    for line in f:
                        line_count += 1
                        if line_count == 1 and date_idx >= 0:
                            parts = line.strip().split(",")
                            if len(parts) > date_idx:
                                cached_date = parts[date_idx]
                    if cached_date == today and line_count > 10:
                        rs_cached = True
                        rs_count  = line_count
            except Exception:
                pass

        # Price cache: count today's meta files (sample-based for speed)
        price_cached_today = 0
        if cache_dir.exists():
            metas = list(cache_dir.glob("*_2y.meta"))
            if len(metas) > 200:
                # Sample 200 files + extrapolate for speed
                import random
                sample = random.sample(metas, 200)
                hits = sum(1 for m in sample if m.read_text().strip() == today)
                price_cached_today = int(hits / 200 * len(metas))
            else:
                for meta in metas:
                    try:
                        if meta.read_text().strip() == today:
                            price_cached_today += 1
                    except Exception:
                        pass

        # Fundamentals cache: count today's fmeta files (sample-based)
        fund_cached_today = 0
        if cache_dir.exists():
            fmetas = list(cache_dir.glob("*_fundamentals.fmeta"))
            if len(fmetas) > 200:
                import random
                sample = random.sample(fmetas, 200)
                hits = sum(1 for m in sample if m.read_text().strip() == today)
                fund_cached_today = int(hits / 200 * len(fmetas))
            else:
                for fmeta in fmetas:
                    try:
                        if fmeta.read_text().strip() == today:
                            fund_cached_today += 1
                    except Exception:
                        pass

        # Finviz in-memory cache
        finviz_cached = False
        try:
            from modules.data_pipeline import _finviz_cache
            finviz_cached = len(_finviz_cache) > 0
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "cache": {
                "rs_cached":          rs_cached,
                "rs_count":           rs_count,
                "price_cached_today": price_cached_today,
                "fund_cached_today":  fund_cached_today,
                "finviz_cached":      finviz_cached,
                "finviz_ttl_hours":   getattr(C, "FINVIZ_CACHE_TTL_HOURS", 4),
            }
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/scan/status/<jid>")
def api_scan_status(jid):
    try:
        job = _get_job(jid)
        job_sanitized = _sanitize_for_json(job)
        response = jsonify(job_sanitized)
        logging.debug(f"[SCAN_STATUS {jid}] Successfully returned job status")
        return response
    except TypeError as te:
        logging.error(f"[SCAN_STATUS {jid}] JSON serialization error: {te}", exc_info=True)
        job = _get_job(jid)
        job_sanitized = _sanitize_for_json(job)
        return jsonify(job_sanitized)
    except Exception as e:
        logging.error(f"[SCAN_STATUS {jid}] Unhandled exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# API – QM Scan
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/qm/scan/run", methods=["POST"])
def api_qm_scan_run():
    data     = request.get_json(silent=True) or {}
    min_star = float(data.get("min_star", getattr(C, "QM_SCAN_MIN_STAR", 3.0)))
    top_n    = int(data.get("top_n", getattr(C, "QM_SCAN_TOP_N", 50)))
    stage1_source = data.get("stage1_source") or None  # None → use C.STAGE1_SOURCE
    jid      = _new_job()
    cancel_ev = _get_cancel(jid)
    _qm_job_ids.add(jid)

    # Per-scan log file
    scan_log_file = _LOG_DIR / f"qm_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _QM_SCAN_LOGGERS = ["modules.qm_screener", "modules.qm_analyzer", "modules.data_pipeline"]
    for logger_name in _QM_SCAN_LOGGERS:
        logging.getLogger(logger_name).addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.qm_screener import run_qm_scan, set_qm_scan_cancel
            set_qm_scan_cancel(cancel_ev)
            result = run_qm_scan(min_star=min_star, top_n=top_n, stage1_source=stage1_source)
            if isinstance(result, tuple):
                df_passed, df_all = result
            else:
                df_passed = result
                df_all = result

            def _to_rows(df):
                import pandas as pd
                
                if df is None or not hasattr(df, "to_dict"):
                    return []
                if hasattr(df, "empty") and df.empty:
                    return []
                
                try:
                    # Convert row-by-row to avoid DataFrame comparison issues
                    records = []
                    for idx, row in df.iterrows():
                        record = {}
                        for col, val in row.items():
                            # Skip DataFrame/Series/complex objects
                            if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                                continue
                            # Convert NaN/None to None
                            if pd.isna(val):
                                record[col] = None
                            else:
                                record[col] = val
                        records.append(record)
                    return _clean(records)
                except Exception as e:
                    logging.error(f"[_to_rows] QM conversion failed: {e}", exc_info=True)
                    return []

            rows = _to_rows(df_passed)
            all_rows = _to_rows(df_all)
            _save_qm_last_scan(rows, all_rows=all_rows)
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
        except Exception as exc:
            logging.exception("QM scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            _qm_job_ids.discard(jid)
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for logger_name in _QM_SCAN_LOGGERS:
                    logging.getLogger(logger_name).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    try:
        response = jsonify({"job_id": jid})
        logging.info(f"[QM_SCAN {jid}] Initial response created successfully")
        return response
    except Exception as e:
        logging.error(f"[QM_SCAN {jid}] Error creating initial response: {e}", exc_info=True)
        return jsonify({"job_id": jid, "error": "Response creation error"}), 500


@app.route("/api/combined/scan/run", methods=["POST"])
def api_combined_scan_run():
    """Run unified combined SEPA + QM scan with shared data pipeline."""
    data          = request.get_json(silent=True) or {}
    refresh_rs    = data.get("refresh_rs", False)
    stage1_source = data.get("stage1_source") or None
    min_star      = float(data["min_star"]) if "min_star" in data else None
    top_n         = int(data["top_n"]) if "top_n" in data else None
    strict_rs     = bool(data.get("strict_rs", False))
    jid           = _new_job()
    cancel_ev     = _get_cancel(jid)

    # Create a unique log file for this combined scan
    combined_log_file = _LOG_DIR / f"combined_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    combined_log_file.parent.mkdir(parents=True, exist_ok=True)
    
    log_handler = logging.FileHandler(combined_log_file, encoding="utf-8")
    log_handler.setLevel(logging.DEBUG)
    log_formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    log_handler.setFormatter(log_formatter)
    
    # Attach to combined_scanner and related modules
    _COMBINED_LOGGERS = [
        "modules.combined_scanner", "modules.screener", "modules.qm_screener",
        "modules.ml_screener",
        "modules.data_pipeline", "modules.rs_ranking", "modules.market_env", 
        "modules.qm_analyzer"
    ]
    for logger_name in _COMBINED_LOGGERS:
        lgr = logging.getLogger(logger_name)
        lgr.addHandler(log_handler)
        lgr.setLevel(logging.DEBUG)
    
    _scan_file_handlers[jid] = log_handler
    _scan_log_paths[jid] = combined_log_file
    
    # Log job start
    logging.info(f"[COMBINED SCAN] Job {jid} started | refresh_rs={refresh_rs} stage1_source={stage1_source} min_star={min_star} top_n={top_n} strict_rs={strict_rs}")

    def _run():
        try:
            logging.info(f"[COMBINED SCAN {jid}] Thread started, running combined scan...")
            from modules.combined_scanner import run_combined_scan, set_combined_cancel
            set_combined_cancel(cancel_ev)

            logging.info(f"[COMBINED SCAN {jid}] Calling run_combined_scan()...")
            try:
                sepa_result, qm_result, ml_result = run_combined_scan(
                    refresh_rs=refresh_rs,
                    stage1_source=stage1_source,
                    verbose=False,
                    min_star=min_star,
                    top_n=top_n,
                    strict_rs=strict_rs,
                )
                logging.info(f"[COMBINED SCAN {jid}] run_combined_scan() completed successfully")
            except Exception as run_err:
                # Catch errors during scan execution and convert to safe string representation
                logging.error(f"[COMBINED SCAN {jid}] Error in run_combined_scan: {type(run_err).__name__}", exc_info=True)
                # Create safe error message without including DataFrame representations
                error_msg = str(run_err) if not "DataFrame" in str(type(run_err)) else f"{type(run_err).__name__}: DataFrame-related error during analysis"
                raise RuntimeError(error_msg) from run_err

            def _to_rows(df):
                import pandas as pd
                import numpy as np
                
                if df is None or not hasattr(df, "to_dict"):
                    return []
                if hasattr(df, "empty") and df.empty:
                    return []
                
                try:
                    # Convert row-by-row to avoid DataFrame comparison issues
                    records = []
                    for idx, row in df.iterrows():
                        record = {}
                        for col, val in row.items():
                            # Skip only DataFrame/Series — allow dict/list for nested fields
                            # (e.g., decision_tree, setup_info) which _clean() will handle
                            if isinstance(val, (pd.DataFrame, pd.Series)):
                                continue
                            # Convert NaN/None to None
                            if pd.isna(val):
                                record[col] = None
                            else:
                                record[col] = val
                        records.append(record)
                    return _clean(records)
                except Exception as e:
                    logging.error(f"[_to_rows] Conversion failed: {e}", exc_info=True)
                    return []

            logging.info(f"[COMBINED SCAN {jid}] Converting results to rows...")
            sepa_rows      = _to_rows(sepa_result.get("passed"))
            sepa_all_rows  = _to_rows(sepa_result.get("all"))
            qm_rows        = _to_rows(qm_result.get("passed"))
            # Safe: check all_scored first, fall back to all if None/empty
            qm_all_source = qm_result.get("all_scored")
            if qm_all_source is None or (hasattr(qm_all_source, "empty") and qm_all_source.empty):
                qm_all_source = qm_result.get("all")
            qm_all_rows    = _to_rows(qm_all_source)
            market_env     = sepa_result.get("market_env", {})
            timing         = sepa_result.get("timing", {})
            ml_rows        = _to_rows(ml_result.get("passed"))
            ml_all_rows    = _to_rows(ml_result.get("all"))
            qm_was_blocked = qm_result.get("blocked", False)
            ml_was_blocked = ml_result.get("blocked", False)
            logging.info(f"[COMBINED SCAN {jid}] Converted results: SEPA {len(sepa_rows)} passed, QM {len(qm_rows)} passed (blocked={qm_was_blocked}), ML {len(ml_rows)} passed (blocked={ml_was_blocked})")

            # ── Save results to scan_results/ ───────────────────────────────
            from datetime import datetime as _dt_now
            _scan_ts = _dt_now.now()
            logging.info(f"[COMBINED SCAN {jid}] Saving CSV results...")
            sepa_csv_path, qm_csv_path, ml_csv_path = _save_combined_scan_csv(
                sepa_result.get("passed"),
                qm_result.get("passed"),
                ml_df=ml_result.get("passed"),
                scan_ts=_scan_ts
            )
            logging.info(f"[COMBINED SCAN {jid}] CSV saved: {sepa_csv_path}, {qm_csv_path}, {ml_csv_path}")

            # ── Persist combined summary for combined dashboard ──────────
            logging.info(f"[COMBINED SCAN {jid}] Saving combined summary...")
            _save_combined_last(sepa_rows, qm_rows, market_env, timing,
                                sepa_csv_path, qm_csv_path, ml_rows=ml_rows,
                                ml_csv=ml_csv_path)

            # ── Mirror results to individual scan endpoints ──────────────
            # This makes /api/scan/last and /api/qm/scan/last reflect the
            # latest combined run, so individual scan pages stay up-to-date.
            logging.info(f"[COMBINED SCAN {jid}] Mirroring results to individual endpoints...")
            _save_last_scan(sepa_rows, all_rows=sepa_all_rows)
            if not qm_was_blocked:
                _save_qm_last_scan(qm_rows, all_rows=qm_all_rows)
            if not ml_was_blocked:
                _save_ml_last_scan(ml_rows, all_rows=ml_all_rows)

            result = {
                "sepa": {
                    "passed": sepa_rows,
                    "count": len(sepa_rows),
                },
                "qm": {
                    "passed": qm_rows,
                    "count": len(qm_rows),
                    "blocked": qm_was_blocked,
                },
                "ml": {
                    "passed": ml_rows,
                    "count": len(ml_rows),
                    "blocked": ml_was_blocked,
                },
                "market": market_env,
                "timing": timing,
                "sepa_csv": sepa_csv_path,
                "qm_csv":   qm_csv_path,
                "ml_csv":   ml_csv_path,
            }

            log_rel = str(combined_log_file.relative_to(ROOT)) if combined_log_file.exists() else ""
            _finish_job(jid, result=result, log_file=log_rel)
        except Exception as exc:
            logging.exception("[CRITICAL] Combined scan thread encountered unhandled exception:")
            logging.error("[CRITICAL] Exception type: %s", type(exc).__name__)
            logging.error("[CRITICAL] Exception message: %s", str(exc))
            import traceback
            logging.error("[CRITICAL] Full traceback:\n%s", traceback.format_exc())
            log_rel = str(combined_log_file.relative_to(ROOT)) if combined_log_file.exists() else ""
            if log_handler in logging.root.handlers:
                logging.root.removeHandler(log_handler)
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            logging.info(f"[COMBINED SCAN {jid}] Cleaning up handlers...")
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for logger_name in _COMBINED_LOGGERS:
                    logger_obj = logging.getLogger(logger_name)
                    if handler in logger_obj.handlers:
                        logger_obj.removeHandler(handler)
                handler.flush()
                handler.close()
                logging.info(f"[COMBINED SCAN {jid}] Handler closed and removed")
            # Ensure log file is written to disk
            try:
                import os
                if combined_log_file.exists() and hasattr(os, 'sync'):
                    os.sync()
            except Exception as e:
                logging.warning(f"Could not sync log file: {e}")

    threading.Thread(target=_run, daemon=True).start()
    try:
        response = jsonify({"job_id": jid})
        logging.info(f"[COMBINED SCAN {jid}] Initial response created successfully")
        return response
    except Exception as e:
        logging.error(f"[COMBINED SCAN {jid}] Error creating initial response: {e}", exc_info=True)
        return jsonify({"job_id": jid, "error": "Response creation error"}), 500


@app.route("/api/combined/scan/status/<jid>", methods=["GET"])
def api_combined_scan_status(jid):
    """Poll combined scan job status."""
    from modules.combined_scanner import get_combined_progress
    
    try:
        job = _get_job(jid)
        if not job:
            return jsonify({"status": "not_found"}), 404

        status = job["status"]
        if status == "pending":
            progress = get_combined_progress()
            return jsonify({
                "status": "pending",
                "progress": progress
            })
        elif status == "done":
            # Wrap in try/except to catch JSON serialization errors
            try:
                result = job.get("result")
                response = jsonify({"status": "done", "result": result})
                logging.info(f"[API_SCAN_STATUS {jid}] Successfully serialized result")
                return response
            except TypeError as te:
                logging.error(f"[API_SCAN_STATUS {jid}] JSON serialization error: {te}", exc_info=True)
                logging.error(f"[API_SCAN_STATUS {jid}] Result type: {type(result)}")
                logging.error(f"[API_SCAN_STATUS {jid}] Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
                # Return sanitized result
                sanitized = _sanitize_for_json(result)
                return jsonify({"status": "done", "result": sanitized})
        else:  # error
            return jsonify({"status": "error", "error": job.get("error")})
    
    except Exception as e:
        logging.error(f"[API_SCAN_STATUS {jid}] Unhandled exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"Status check failed: {str(e)}"}), 500


@app.route("/api/combined/scan/cancel/<jid>", methods=["POST"])
def api_combined_scan_cancel(jid):
    """Cancel an in-progress combined scan."""
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling...",
                                           "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@app.route("/api/combined/scan/last", methods=["GET"])
def api_combined_scan_last():
    """Return the most recent combined scan summary for dashboard display."""
    return jsonify(_load_combined_last())


@app.route("/api/qm/scan/cancel/<jid>", methods=["POST"])
def api_qm_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…",
                                           "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@app.route("/api/qm/scan/last", methods=["GET"])
def api_qm_scan_last():
    """Return last QM scan results (passed-only by default, all if ?include_all=1)."""
    data = _load_qm_last_scan()
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@app.route("/api/qm/scan/status/<jid>")
def api_qm_scan_status(jid):
    try:
        job = _get_job(jid)
        job_sanitized = _sanitize_for_json(job)
        response = jsonify(job_sanitized)
        logging.debug(f"[QM_SCAN_STATUS {jid}] Successfully returned job status")
        return response
    except TypeError as te:
        logging.error(f"[QM_SCAN_STATUS {jid}] JSON serialization error: {te}", exc_info=True)
        job = _get_job(jid)
        job_sanitized = _sanitize_for_json(job)
        return jsonify(job_sanitized)
    except Exception as e:
        logging.error(f"[QM_SCAN_STATUS {jid}] Unhandled exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/qm/scan/progress")
def api_qm_scan_progress():
    """Live progress polling endpoint for active QM scan."""
    try:
        from modules.qm_screener import get_qm_scan_progress
        return jsonify(get_qm_scan_progress())
    except Exception as exc:
        return jsonify({"stage": "Error", "pct": 0, "msg": str(exc)})


@app.route("/api/qm/scan/logs/<jid>")
def api_qm_scan_logs(jid):
    """Retrieve detailed diagnostic logs for a QM scan job."""
    if jid not in _scan_log_paths:
        return jsonify({"ok": False, "error": "Job ID not found or logs not available"}), 404
    
    log_file = _scan_log_paths[jid]
    if not log_file.exists():
        return jsonify({"ok": False, "error": "Log file not found"}), 404
    
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Filter for Stage 1 related logs (finvizfinance diagnostics)
        stage1_logs = []
        for line in content.split("\n"):
            if any(marker in line for marker in [
                "[QM Stage1]", "[Finviz", "finvizfinance", "screener_view",
                "Performance", "Overview", "fallback"
            ]):
                stage1_logs.append(line)
        
        return jsonify({
            "ok": True,
            "job_id": jid,
            "log_file": str(log_file.relative_to(ROOT)),
            "full_logs": content.split("\n")[-100:],  # Last 100 lines
            "stage1_diagnostics": stage1_logs[-50:],  # Last 50 Stage 1 lines
            "total_lines": len(content.split("\n"))
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to read logs: {exc}"}), 500


@app.route("/api/qm/analyze", methods=["POST"])
def api_qm_analyze():
    """
    Synchronous QM deep analysis for a single ticker.
    Returns: star rating, 6-dimension scores, setup type, trade plan.
    """
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    # Per-analyze log file
    analyze_log_file = _LOG_DIR / f"qm_analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler  = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )
    _QM_ANALYZE_LOGGERS = [
        "modules.qm_analyzer", "modules.qm_setup_detector",
        "modules.data_pipeline", "modules.market_env",
    ]
    for logger_name in _QM_ANALYZE_LOGGERS:
        logging.getLogger(logger_name).addHandler(analyze_handler)

    try:
        from modules.qm_analyzer import analyze_qm
        result = analyze_qm(ticker, print_report=False)
        # DEBUG: Print trade plan before _clean
        if result and isinstance(result, dict) and 'trade_plan' in result:
            tp = result['trade_plan']
            if isinstance(tp, dict):
                print(f"[DEBUG] Before _clean: day2_stop type = {type(tp.get('day2_stop'))}, value = {tp.get('day2_stop')}")
        clean_result = _clean(result) if result else {}
        #DEBUG: Print trade plan after _clean
        if clean_result and isinstance(clean_result, dict) and 'trade_plan' in clean_result:
            tp = clean_result['trade_plan']
            if isinstance(tp, dict):
                print(f"[DEBUG] After _clean: day2_stop type = {type(tp.get('day2_stop'))}, value = {tp.get('day2_stop')}")
        _qm_analyze_cache[ticker] = clean_result  # Store for /htmx/qm/analyze/result
        log_rel = str(analyze_log_file.relative_to(ROOT)) if analyze_log_file.exists() else ""
        return jsonify({"ok": True, "ticker": ticker,
                        "result": clean_result, "log_file": log_rel})
    except Exception as exc:
        logging.exception("QM analyze error: %s", ticker)
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker})
    finally:
        for logger_name in _QM_ANALYZE_LOGGERS:
            logging.getLogger(logger_name).removeHandler(analyze_handler)
        analyze_handler.close()


# ═══════════════════════════════════════════════════════════════════════════════
# API – Martin Luk (ML) Scan & Analyze
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/ml/scan/run", methods=["POST"])
def api_ml_scan_run():
    data               = request.get_json(silent=True) or {}
    min_star           = float(data.get("min_star", getattr(C, "ML_SCAN_MIN_STAR", 3.0)))
    top_n              = int(data.get("top_n", getattr(C, "ML_SCAN_TOP_N", 50)))
    scanner_mode       = str(data.get("scanner_mode", "standard")).strip().lower()
    stage1_source      = data.get("stage1_source") or None  # None → use C.STAGE1_SOURCE
    use_universe_cache = bool(data.get("use_universe_cache", False))
    jid          = _new_job()
    cancel_ev = _get_cancel(jid)
    _ml_job_ids.add(jid)

    # Per-scan log file
    scan_log_file = _LOG_DIR / f"ml_scan_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    scan_handler = logging.FileHandler(scan_log_file, encoding="utf-8")
    scan_handler.setLevel(logging.DEBUG)
    scan_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _ML_SCAN_LOGGERS = ["modules.ml_screener", "modules.ml_analyzer", "modules.data_pipeline"]
    for logger_name in _ML_SCAN_LOGGERS:
        logging.getLogger(logger_name).addHandler(scan_handler)
    _scan_file_handlers[jid] = scan_handler
    _scan_log_paths[jid] = scan_log_file

    def _run():
        try:
            from modules.ml_screener import run_ml_scan, set_ml_scan_cancel
            set_ml_scan_cancel(cancel_ev)
            result = run_ml_scan(min_star=min_star, top_n=top_n, scanner_mode=scanner_mode,
                                 stage1_source=stage1_source,
                                 use_universe_cache=use_universe_cache)
            
            # Defensive: ensure result is a valid tuple of DataFrames
            if isinstance(result, tuple) and len(result) == 2:
                df_passed, df_all = result
            else:
                # result is invalid (possibly string error or unexpected type)
                logging.error(f"[ML Scan] Unexpected result type: {type(result).__name__}, value: {result}")
                _finish_job(jid, error=f"Internal scan error: invalid result type {type(result).__name__}", log_file="")
                return

            def _to_rows(df):
                import pandas as pd
                # Early return for non-DataFrames
                if not isinstance(df, pd.DataFrame):
                    logging.debug(f"[_to_rows] Expected DataFrame, got {type(df).__name__}")
                    return []
                if df is None or df.empty:
                    return []
                try:
                    records = []
                    for idx, row in df.iterrows():
                        record = {}
                        for col, val in row.items():
                            if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                                continue
                            if pd.isna(val):
                                record[col] = None
                            else:
                                record[col] = val
                        records.append(record)
                    return _clean(records)
                except Exception as e:
                    logging.error(f"[_to_rows] ML conversion failed: {e}", exc_info=True)
                    return []

            rows = _to_rows(df_passed)
            all_rows = _to_rows(df_all)
            
            # Defensive: validate rows is a list of dicts
            if not isinstance(rows, list):
                logging.error("[ML Scan] rows is not a list: %s", type(rows).__name__)
                rows = []
            else:
                # Validate each row is a dict
                clean_rows = []
                for i, row in enumerate(rows):
                    if not isinstance(row, dict):
                        logging.warning("[ML Scan] row %d is not a dict: %s", i, type(row).__name__)
                        continue
                    clean_rows.append(row)
                rows = clean_rows
            
            _save_ml_last_scan(rows, all_rows=all_rows)

            # Theme report and triple channel summary
            themes = []
            triple_summary = {}
            try:
                from modules.ml_theme_tracker import get_theme_report
                theme_rpt = get_theme_report(rows if isinstance(rows, list) else [])
                if isinstance(theme_rpt, dict):
                    themes = _clean(theme_rpt.get("themes", []))
                else:
                    logging.warning("ML theme_rpt is not a dict: %s", type(theme_rpt).__name__)
            except Exception as _te:
                logging.warning("ML theme report failed: %s", _te)
            
            # Triple channel summary (with error handling)
            if scanner_mode != "standard":
                try:
                    channel_counts = {"GAP": 0, "GAINER": 0, "LEADER": 0}
                    for row in (rows if isinstance(rows, list) else []):
                        if not isinstance(row, dict):
                            logging.debug("Skipping non-dict row in triple summary: %s", type(row).__name__)
                            continue
                        ch = str(row.get("channel", "")).upper()
                        if ch in channel_counts:
                            channel_counts[ch] += 1
                    triple_summary = channel_counts
                except Exception as _tse:
                    logging.warning("ML triple summary calculation failed: %s", _tse)
                    triple_summary = {"GAP": 0, "GAINER": 0, "LEADER": 0}

            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, result=rows, log_file=log_rel)
            # Attach extra data to job dict (status endpoint returns full job dict)
            try:
                with _jobs_lock:
                    _jobs[jid]["themes"] = _sanitize_for_json(themes)
                    _jobs[jid]["triple_summary"] = _sanitize_for_json(triple_summary)
            except Exception as _je:
                logging.error("[ML Scan] Error attaching themes/triple_summary to job: %s", _je, exc_info=True)
                with _jobs_lock:
                    _jobs[jid]["themes"] = []
                    _jobs[jid]["triple_summary"] = {"GAP": 0, "GAINER": 0, "LEADER": 0}
        except Exception as exc:
            logging.exception("ML scan thread error")
            log_rel = str(scan_log_file.relative_to(ROOT)) if scan_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            _ml_job_ids.discard(jid)
            if jid in _scan_file_handlers:
                handler = _scan_file_handlers.pop(jid)
                for logger_name in _ML_SCAN_LOGGERS:
                    logging.getLogger(logger_name).removeHandler(handler)
                handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/ml/universe-cache", methods=["GET"])
def api_ml_universe_cache():
    """Return metadata about the persisted ML Stage-1 universe cache."""
    try:
        from modules.ml_screener import get_ml_universe_cache_info
        return jsonify(get_ml_universe_cache_info())
    except Exception as exc:
        return jsonify({"exists": False, "error": str(exc)}), 500


@app.route("/api/ml/scan/cancel/<jid>", methods=["POST"])
def api_ml_scan_cancel(jid):
    ev = _cancel_events.get(jid)
    if ev:
        ev.set()
        with _jobs_lock:
            if jid in _jobs and _jobs[jid]["status"] == "pending":
                _jobs[jid]["progress"] = {"stage": "Cancelling…",
                                           "pct": 100, "msg": ""}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404


@app.route("/api/ml/scan/last", methods=["GET"])
def api_ml_scan_last():
    """Return last ML scan results."""
    data = _load_ml_last_scan()
    include_all = request.args.get("include_all", "0") == "1"
    if not include_all:
        data.pop("all_scored", None)
        data.pop("all_scored_count", None)
    return jsonify(data)


@app.route("/api/ml/scan/status/<jid>")
def api_ml_scan_status(jid):
    try:
        job = _get_job(jid)
        job_sanitized = _sanitize_for_json(job)
        return jsonify(job_sanitized)
    except Exception as e:
        logging.error(f"[ML_SCAN_STATUS {jid}] Exception: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/ml/scan/progress")
def api_ml_scan_progress():
    """Live progress polling endpoint for active ML scan."""
    try:
        from modules.ml_screener import get_ml_scan_progress
        return jsonify(get_ml_scan_progress())
    except Exception as exc:
        return jsonify({"stage": "Error", "pct": 0, "msg": str(exc)})


@app.route("/api/ml/scan/logs/<jid>")
def api_ml_scan_logs(jid):
    """Retrieve diagnostic logs for an ML scan job."""
    if jid not in _scan_log_paths:
        return jsonify({"ok": False, "error": "Job ID not found"}), 404
    log_file = _scan_log_paths[jid]
    if not log_file.exists():
        return jsonify({"ok": False, "error": "Log file not found"}), 404
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({
            "ok": True, "job_id": jid,
            "log_file": str(log_file.relative_to(ROOT)),
            "full_logs": content.split("\n")[-100:],
            "total_lines": len(content.split("\n")),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to read logs: {exc}"}), 500


@app.route("/api/ml/analyze", methods=["POST"])
def api_ml_analyze():
    """
    Synchronous ML deep analysis for a single ticker.
    Returns: star rating, 7-dimension scores, setup type, trade plan.
    """
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    analyze_log_file = _LOG_DIR / f"ml_analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler  = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )
    _ML_ANALYZE_LOGGERS = [
        "modules.ml_analyzer", "modules.ml_setup_detector",
        "modules.data_pipeline", "modules.market_env",
    ]
    for logger_name in _ML_ANALYZE_LOGGERS:
        logging.getLogger(logger_name).addHandler(analyze_handler)

    try:
        from modules.ml_analyzer import analyze_ml
        result = analyze_ml(ticker, print_report=False)
        clean_result = _clean(result) if result else {}
        _ml_analyze_cache[ticker] = clean_result  # Store for /htmx/ml/analyze/result
        log_rel = str(analyze_log_file.relative_to(ROOT)) if analyze_log_file.exists() else ""
        return jsonify({"ok": True, "ticker": ticker,
                        "result": clean_result, "log_file": log_rel})
    except Exception as exc:
        logging.exception("ML analyze error: %s", ticker)
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker})
    finally:
        for logger_name in _ML_ANALYZE_LOGGERS:
            logging.getLogger(logger_name).removeHandler(analyze_handler)
        analyze_handler.close()


# ═══════════════════════════════════════════════════════════════════════════════
# API – Analyze
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    acct   = float(data.get("account_size", C.ACCOUNT_SIZE))
    jid    = _new_job()

    # Per-analyze log file
    analyze_log_file = _LOG_DIR / f"analyze_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    analyze_handler  = logging.FileHandler(analyze_log_file, encoding="utf-8")
    analyze_handler.setLevel(logging.DEBUG)
    analyze_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )
    _ANALYZE_LOGGERS = [
        "modules.stock_analyzer", "modules.data_pipeline",
        "modules.screener", "modules.vcp_detector",
        "modules.rs_ranking", "modules.db",
    ]
    for ln in _ANALYZE_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)   # ensure DEBUG messages flow through
        _lg.addHandler(analyze_handler)

    def _run():
        try:
            from modules.stock_analyzer import analyze
            logging.getLogger("modules.stock_analyzer").info(
                "=== Analyze started: %s  account=$%.0f (job %s) ===", ticker, acct, jid)
            r = analyze(ticker, account_size=acct, print_report=False)
            if r is None:
                logging.getLogger("modules.stock_analyzer").warning(
                    "=== Analyze returned None for %s ===", ticker)
                _finish_job(jid, error=f"No data returned for {ticker}")
                return
            logging.getLogger("modules.stock_analyzer").info(
                "=== Analyze finished: %s  sepa_score=%.1f  recommendation=%s ===",
                ticker,
                float(r.get("sepa_score") or 0),
                (r.get("recommendation") or {}).get("action", "N/A"),
            )
            # Write full structured result to log file
            _write_analyze_report(analyze_log_file, r, acct)
            _finish_job(jid, result=_clean(r))
        except Exception as exc:
            logging.exception("Analyze thread error for %s", ticker)
            _finish_job(jid, error=str(exc))
        finally:
            # Remove per-analyze handler to avoid duplicate log entries
            for ln in _ANALYZE_LOGGERS:
                logging.getLogger(ln).removeHandler(analyze_handler)
            analyze_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid, "log_file": analyze_log_file.name})


def _write_analyze_report(log_file: Path, r: dict, acct: float):
    """Append a human-readable full analysis report to the log file."""
    try:
        sep  = "=" * 72
        sep2 = "-" * 72
        lines = []
        def ln(s=""): lines.append(s)

        ticker  = r.get("ticker", "?")
        company = r.get("company", ticker)
        rec     = r.get("recommendation") or {}
        tt      = r.get("trend_template") or {}
        scored  = r.get("scored_pillars") or {}
        pos     = r.get("position") or {}
        vcp     = r.get("vcp") or {}
        funds   = r.get("fundamentals") or {}
        fund_checks = funds.get("checks") or []
        eps_accel   = r.get("eps_acceleration") or {}

        ln(sep)
        ln(f"  SEPA ANALYSIS REPORT — {ticker} ({company})")
        ln(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Account: ${acct:,.0f}")
        ln(sep)

        # ── Summary ──────────────────────────────────────────────────────────
        ln()
        ln("[ SUMMARY ]")
        ln(f"  Sector:         {r.get('sector','')} / {r.get('industry','')}")
        ln(f"  Price:          ${r.get('price', 0):.2f}")
        mktcap = r.get('market_cap') or 0
        ln(f"  Market Cap:     ${mktcap/1e9:.2f}B" if mktcap >= 1e9 else f"  Market Cap:     ${mktcap/1e6:.0f}M")
        ln(f"  RS Rank:        {r.get('rs_rank', 0):.1f} percentile")
        ln(f"  SEPA Score:     {float(r.get('sepa_score') or 0):.1f} / 100")
        ln(f"  Recommendation: {rec.get('action','N/A')}  —  {rec.get('description','')}")
        for reason in rec.get("reasons") or []:
            if reason:
                ln(f"    • {reason}")

        # ── SEPA 5-Pillar Scores ─────────────────────────────────────────────
        ln()
        ln("[ SEPA 5-PILLAR SCORES ]")
        pillar_map = {
            "trend_score":       "趨勢 Trend",
            "fundamental_score": "基本面 Fundamentals",
            "catalyst_score":    "催化劑 Catalyst",
            "entry_score":       "入場時機 Entry",
            "rr_score":          "風險回報 R/R",
        }
        for key, label in pillar_map.items():
            val = float(scored.get(key) or 0)
            bar = "█" * int(val / 5) + "░" * (20 - int(val / 5))
            ln(f"  {label:<28} {val:5.1f}  [{bar}]")
        ln(f"  {'Total':<28} {float(r.get('sepa_score') or 0):5.1f}")
        ln(f"  R/R Ratio:  {float(scored.get('rr_ratio') or 0):.2f}:1")
        ln(f"  Stop:       {float(scored.get('stop_pct') or 0):.1f}%")
        ln(f"  Target:     {float(scored.get('target_pct') or 0):.1f}%")
        ln(f"  RS Rank:    {scored.get('rs_rank') or 'N/A'}")
        ln(f"  Close:      ${float(scored.get('close') or 0):.2f}")
        ln(f"  ATR(14):    {float(scored.get('atr14') or 0):.2f}")
        if scored.get('pivot'):
            ln(f"  Pivot:      ${float(scored.get('pivot') or 0):.2f}")

        # ── Trend Template TT1-TT10 ──────────────────────────────────────────
        ln()
        ln("[ TREND TEMPLATE (TT1-TT10) ]")
        tt_labels = {
            "tt1":"Price>SMA50>SMA150>SMA200", "tt2":"Price>SMA200",
            "tt3":"SMA150>SMA200",              "tt4":"SMA200 rising 22d",
            "tt5":"SMA50>SMA150 & SMA200",      "tt6":"Price>SMA50",
            "tt7":"Price >25% above 52W low",   "tt8":"Within 25% of 52W high",
            "tt9":"RS Rank ≥70",                "tt10":"Sector top 35%",
        }
        passed = sum(1 for k in tt_labels if tt.get(k))
        ln(f"  Passed: {passed}/10")
        for k, lbl in tt_labels.items():
            mark = "PASS" if tt.get(k) else "FAIL"
            ln(f"  [{mark}] {k.upper()}: {lbl}")

        # ── VCP ───────────────────────────────────────────────────────────────
        ln()
        ln("[ VCP DETECTION ]")
        ln(f"  Valid VCP:       {'YES ✓' if vcp.get('is_valid_vcp') else 'NO'}")
        ln(f"  VCP Score:       {vcp.get('vcp_score', 0)}/100  Grade: {vcp.get('grade','—')}")
        ln(f"  T-count:         {vcp.get('t_count', 0)}")
        ln(f"  Base weeks:      {vcp.get('base_weeks', 0)}")
        base_d = vcp.get('base_depth_pct')
        ln(f"  Base depth:      {float(base_d):.1f}%" if base_d is not None else "  Base depth:      N/A")
        if vcp.get('pivot_price'):
            ln(f"  Pivot price:     ${float(vcp['pivot_price']):.2f}")
        ln(f"  ATR contracting: {'✓' if vcp.get('atr_contracting') else '✗'}")
        ln(f"  BB  contracting: {'✓' if vcp.get('bb_contracting') else '✗'}")
        ln(f"  Volume dry-up:   {'✓' if vcp.get('vol_dry') else '✗'}  (ratio: {vcp.get('vol_ratio', 'N/A')})")
        # Contractions detail
        for i, c_item in enumerate(vcp.get("contractions") or []):
            ln(f"  T-{i+1}: range {c_item.get('range_pct',0):.1f}%  "
               f"vol_ratio {c_item.get('vol_ratio',0):.2f}  "
               f"high ${c_item.get('seg_high',0):.2f}  low ${c_item.get('seg_low',0):.2f}")
        for note in vcp.get("notes") or []:
            ln(f"  • {note}")

        # ── Fundamentals ─────────────────────────────────────────────────────
        ln()
        ln("[ FUNDAMENTALS CHECKLIST ]")
        ln(f"  Score: {funds.get('passes',0)}/{funds.get('total',0)} ({funds.get('pct',0)}%)")
        for fc in fund_checks:
            mark = "PASS" if fc.get("pass") else "FAIL"
            ln(f"  [{mark}] {fc.get('id',''):<25} {fc.get('note','')}")

        # ── EPS Acceleration ─────────────────────────────────────────────────
        ln()
        ln("[ EPS ACCELERATION ]")
        ln(f"  Accelerating: {eps_accel.get('is_accelerating', False)}")
        ln(f"  Note:         {eps_accel.get('note', 'N/A')}")
        for q in eps_accel.get("quarters") or []:
            ln(f"  {q}")

        # ── Position Sizing ───────────────────────────────────────────────────
        ln()
        ln("[ POSITION SIZING ]")
        ln(f"  Entry:          ${float(pos.get('entry') or 0):.2f}")
        ln(f"  Stop Loss:      ${float(pos.get('stop') or 0):.2f}  (-{float(pos.get('risk_pct') or 0):.1f}%)")
        ln(f"  Target:         ${float(pos.get('target') or 0):.2f}")
        ln(f"  Shares:         {int(pos.get('shares') or 0)}")
        ln(f"  Position Value: ${float(pos.get('position_value') or 0):,.0f}")
        ln(f"  Risk $:         ${float(pos.get('risk_dollar') or 0):,.0f}")
        ln(f"  R:R Ratio:      {float(pos.get('rr') or 0):.1f}:1")

        # ── News ─────────────────────────────────────────────────────────────
        news_raw = r.get("news")
        # news may be a list of dicts or a DataFrame — normalise to list
        if news_raw is not None and hasattr(news_raw, "to_dict"):
            news = news_raw.to_dict(orient="records") if not news_raw.empty else []
        else:
            news = news_raw if isinstance(news_raw, list) else []
        if news:
            ln()
            ln("[ RECENT NEWS ]")
            for item in news[:5]:
                title = item.get("title") or item.get("headline") or str(item)
                ln(f"  • {title}")

        ln()
        ln(sep)
        ln()

        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    except Exception as exc:
        logging.warning("_write_analyze_report failed: %s", exc, exc_info=True)

@app.route("/api/analyze/status/<jid>")
def api_analyze_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# API – VCP
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/vcp", methods=["POST"])
def api_vcp():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    jid    = _new_job()

    def _run():
        try:
            from modules.data_pipeline import get_enriched
            from modules.vcp_detector  import detect_vcp
            df = get_enriched(ticker, period="2y")
            if df is None or df.empty:
                _finish_job(jid, error=f"No price data for {ticker}")
                return
            result = detect_vcp(df)
            last_close = float(df["Close"].iloc[-1]) if not df.empty else None
            result["current_price"] = round(last_close, 2) if last_close else None
            result["ticker"] = ticker   # preserved for /htmx/vcp/result/<jid>
            _finish_job(jid, result=_clean(result))
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/vcp/status/<jid>")
def api_vcp_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# API – Market
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/market/run", methods=["POST"])
def api_market_run():
    jid = _new_job()
    _market_job_ids.add(jid)

    # Create a unique log file for this market assessment
    market_log_file = _LOG_DIR / f"market_{jid}_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}.log"
    market_handler = logging.FileHandler(market_log_file, encoding="utf-8")
    market_handler.setLevel(logging.DEBUG)
    market_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _MARKET_LOGGERS = ["modules.market_env", "modules.data_pipeline"]
    for logger_name in _MARKET_LOGGERS:
        logging.getLogger(logger_name).addHandler(market_handler)

    def _run():
        try:
            from modules.market_env import assess
            logging.getLogger("modules.market_env").info(
                "=== Market Assessment started (job %s) ===", jid)
            result = assess(verbose=False)
            logging.getLogger("modules.market_env").info(
                "=== Market Assessment finished — regime: %s ===",
                result.get("regime", "UNKNOWN"))
            
            # ── DuckDB 市場環境歷史 ─────────────────────────────────────
            if getattr(C, "DB_ENABLED", True) and result:
                try:
                    from modules.db import append_market_env
                    append_market_env(result)
                except Exception as exc:
                    logging.warning(f"DB market_env write skipped: {exc}")
            
            log_rel = str(market_log_file.relative_to(ROOT)) if market_log_file.exists() else ""
            _finish_job(jid, result=_clean(result), log_file=log_rel)
        except Exception as exc:
            logging.getLogger("modules.market_env").exception(
                "Market assessment thread error")
            log_rel = str(market_log_file.relative_to(ROOT)) if market_log_file.exists() else ""
            _finish_job(jid, error=str(exc), log_file=log_rel)
        finally:
            for logger_name in _MARKET_LOGGERS:
                logging.getLogger(logger_name).removeHandler(market_handler)
            market_handler.close()
            _market_job_ids.discard(jid)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/market/status/<jid>")
def api_market_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# API – Watchlist
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    return jsonify(_load_watchlist())


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    grade  = data.get("grade")
    note   = data.get("note", "")
    jid    = _new_job()

    def _run():
        try:
            from modules.watchlist import add
            add(ticker, grade=grade, note=note)
            _finish_job(jid, result={"ticker": ticker, "watchlist": _load_watchlist()})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/watchlist/add/status/<jid>")
def api_watchlist_add_status(jid):
    return jsonify(_get_job(jid))


@app.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import remove
        remove(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/watchlist/promote", methods=["POST"])
def api_watchlist_promote():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import promote
        promote(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/watchlist/demote", methods=["POST"])
def api_watchlist_demote():
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.watchlist import demote
        demote(ticker)
        return jsonify({"ok": True, "watchlist": _load_watchlist()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/watchlist/refresh", methods=["POST"])
def api_watchlist_refresh():
    jid = _new_job()

    def _run():
        try:
            from modules.watchlist import refresh
            refresh()
            _finish_job(jid, result={"watchlist": _load_watchlist()})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/watchlist/refresh/status/<jid>")
def api_watchlist_refresh_status(jid):
    return jsonify(_get_job(jid))


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX – Watchlist  (return HTML fragments, not JSON)
# Used by hx-* buttons in _watchlist_rows.html and htmx.ajax() calls in watchlist.html.
# Existing /api/watchlist/* JSON endpoints are preserved for backward compatibility.
# ═══════════════════════════════════════════════════════════════════════════════

def _htmx_wl_rows(wl, trigger_msg=None):
    """Build htmx response: OOB badge-count <span>s prepended to tbody rows.

    The OOB spans update #cnt-all / #cnt-A / #cnt-B / #cnt-C in the tab nav.
    The remaining rows are swapped into #wlBody by the caller's hx-target.
    """
    rows_html = render_template("_watchlist_rows.html", wl=wl)
    total = sum(len(wl.get(g, {})) for g in ["A", "B", "C"])
    oob = (
        f'<span id="cnt-all" hx-swap-oob="innerHTML">{total}</span>'
        f'<span id="cnt-A"   hx-swap-oob="innerHTML">{len(wl.get("A", {}))}</span>'
        f'<span id="cnt-B"   hx-swap-oob="innerHTML">{len(wl.get("B", {}))}</span>'
        f'<span id="cnt-C"   hx-swap-oob="innerHTML">{len(wl.get("C", {}))}</span>'
    )
    resp = make_response(oob + rows_html)
    if trigger_msg:
        resp.headers["HX-Trigger"] = json.dumps({"showToast": trigger_msg})
    return resp


@app.route("/htmx/watchlist/body")
def htmx_wl_body():
    """Return full tbody rows + OOB badge updates.  Called via htmx.ajax() after async jobs."""
    return _htmx_wl_rows(_load_watchlist())


@app.route("/htmx/watchlist/promote", methods=["POST"])
def htmx_wl_promote():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import promote
        promote(ticker)
        return _htmx_wl_rows(_load_watchlist(), f"✅ {ticker} promoted")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@app.route("/htmx/watchlist/demote", methods=["POST"])
def htmx_wl_demote():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import demote
        demote(ticker)
        return _htmx_wl_rows(_load_watchlist(), f"✅ {ticker} demoted")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@app.route("/htmx/watchlist/remove", methods=["POST"])
def htmx_wl_remove():
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        from modules.watchlist import remove
        remove(ticker)
        return _htmx_wl_rows(_load_watchlist(), f"✅ {ticker} removed from watchlist")
    except Exception as exc:
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


# ═══════════════════════════════════════════════════════════════════════════════
# API – Positions
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/positions", methods=["GET"])
def api_positions_get():
    return jsonify(_load_positions())


@app.route("/api/positions/add", methods=["POST"])
def api_positions_add():
    import time
    start_t = time.time()
    
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    
    logging.info(f"[API] positions/add START: {ticker}")
    
    try:
        from modules.position_monitor import add_position
        
        t1 = time.time()
        add_position(
            ticker,
            float(data["buy_price"]),
            int(data["shares"]),
            float(data["stop_loss"]),
            float(data["target"]) if data.get("target") else None,
            str(data.get("note", "")),
        )
        t2 = time.time()
        logging.info(f"[API] add_position() completed in {(t2-t1):.3f}s")
        
        # Don't reload all positions - just return the one that was added
        buy_price = float(data["buy_price"])
        shares = int(data["shares"])
        stop_loss = float(data["stop_loss"])
        target = float(data["target"]) if data.get("target") else None
        
        if target is None:
            risk = buy_price - stop_loss
            target = buy_price + risk * 2  # Default 2:1 R:R
        
        stop_pct = (buy_price - stop_loss) / buy_price * 100
        rr = (target - buy_price) / (buy_price - stop_loss) if (buy_price - stop_loss) > 0 else 0
        risk_dol = shares * (buy_price - stop_loss)
        
        # Return just the new position without full reload
        positions_dict = {
            ticker: {
                "buy_price": round(buy_price, 2),
                "shares": shares,
                "stop_loss": round(stop_loss, 2),
                "stop_pct": round(stop_pct, 2),
                "target": round(target, 2),
                "rr": round(rr, 2),
                "risk_dollar": round(risk_dol, 2),
                "buy_date": None,
                "days_held": 0,
                "note": str(data.get("note", "")),
            }
        }
        
        elapsed = time.time() - start_t
        logging.info(f"[API] positions/add DONE in {elapsed:.3f}s: {ticker}")
        
        return jsonify({"ok": True, "positions": positions_dict})
    except Exception as exc:
        elapsed = time.time() - start_t
        logging.error(f"[API] positions/add FAILED ({elapsed:.3f}s): {exc}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/positions/close", methods=["POST"])
def api_positions_close():
    data       = request.get_json(silent=True) or {}
    ticker     = str(data.get("ticker", "")).upper().strip()
    exit_price = float(data.get("exit_price", 0))
    reason     = str(data.get("reason", ""))
    try:
        from modules.position_monitor import close_position
        close_position(ticker, exit_price, reason=reason)
        return jsonify({"ok": True, "positions": _load_positions()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/positions/check", methods=["POST"])
def api_positions_check():
    jid = _new_job()

    def _run():
        try:
            # Capture health check output as structured data
            from modules.position_monitor import _check_position, _load
            data_store = _load()
            positions  = data_store.get("positions", {})
            results    = []
            for t, pos in positions.items():
                r = _check_position(t, pos)
                results.append(_clean(r))
            _finish_job(jid, result=results)
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/positions/check/status/<jid>")
def api_positions_check_status(jid):
    return jsonify(_get_job(jid))


@app.route("/api/positions/update_stop", methods=["POST"])
def api_positions_update_stop():
    data      = request.get_json(silent=True) or {}
    ticker    = str(data.get("ticker", "")).upper().strip()
    new_stop  = float(data.get("new_stop", 0))
    try:
        from modules.position_monitor import update_stop
        update_stop(ticker, new_stop)
        return jsonify({"ok": True, "positions": _load_positions()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX – Positions  (return HTML fragment, not JSON)
# Used by the add-position form (hx-post="/htmx/positions/add").
# The existing /api/positions/* JSON endpoints are preserved.
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/htmx/positions/add", methods=["POST"])
def htmx_positions_add():
    """Accept form-encoded data, save position, return a <tr> HTML fragment."""
    ticker = (request.form.get("ticker") or "").upper().strip()
    try:
        buy_price = float(request.form.get("buy_price") or 0)
        shares    = int(request.form.get("shares") or 0)
        stop_loss = float(request.form.get("stop_loss") or 0)
        target_raw = request.form.get("target", "").strip()
        target    = float(target_raw) if target_raw else None
        note      = request.form.get("note", "")

        if not ticker or not buy_price or not shares or not stop_loss:
            resp = make_response("", 200)
            resp.headers["HX-Trigger"] = json.dumps({"showToast": "❌ Fill in Ticker, Entry, Shares and Stop Loss"})
            return resp

        from modules.position_monitor import add_position
        add_position(ticker, buy_price, shares, stop_loss, target, note)

        # Compute display values (mirrors api_positions_add logic)
        if target is None:
            risk   = buy_price - stop_loss
            target = round(buy_price + risk * 2, 2)
        rr       = (target - buy_price) / (buy_price - stop_loss) if (buy_price - stop_loss) > 0 else 0
        risk_dol = shares * (buy_price - stop_loss)

        pos = {
            "buy_price":   round(buy_price, 2),
            "shares":      shares,
            "stop_loss":   round(stop_loss, 2),
            "target":      round(target, 2),
            "rr":          round(rr, 2),
            "risk_dollar": round(risk_dol, 2),
            "days_held":   0,
            "note":        note,
        }
        resp = make_response(render_template("_position_row.html", ticker=ticker, pos=pos))
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"✅ {ticker} 持倉已新增"})
        return resp

    except Exception as exc:
        logging.error(f"[htmx/positions/add] {exc}")
        resp = make_response("", 200)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": f"❌ {exc}"})
        return resp


@app.route("/htmx/analyze/result/<jid>")
def htmx_analyze_result(jid):
    """Return server-rendered HTML partial for SEPA analyze results.
    Called by analyze.html via htmx.ajax() after the background job completes.
    Swapped into <div id="result"> using innerHTML swap.
    """
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200
        )
    d = job.get("result") or {}
    ticker = str(d.get("ticker", "")).upper()
    return render_template("_analyze_result.html", d=d, ticker=ticker)


@app.route("/htmx/vcp/result/<jid>")
def htmx_vcp_result(jid):
    """Return server-rendered HTML partial for VCP analysis results.
    Called by vcp.html via htmx.ajax() after the background job completes.
    Swapped into <div id="result"> using innerHTML swap.
    ticker is stored in the result dict (added in api_vcp) and also
    accepted as a fallback query-string param: ?ticker=NVDA
    """
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200
        )
    d = job.get("result") or {}
    ticker = str(d.get("ticker") or request.args.get("ticker", "")).upper()
    return render_template("_vcp_result.html", d=d, ticker=ticker)


@app.route("/htmx/market/result/<jid>")
def htmx_market_result(jid):
    """Return server-rendered HTML partial for Market Environment results.
    Called by market.html via htmx.ajax() after the background job completes.
    Swapped into <div id="result"> using innerHTML swap.
    The partial includes placeholders for index LWC charts and history table;
    its inline <script> calls loadMarketHistory() and loadIndexCharts() which
    are defined in market.html.
    """
    job = _get_job(jid)
    if job.get("status") != "done":
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            "Result not ready or not found.</p>", 200
        )
    d = job.get("result") or {}
    log_file = job.get("log_file", "")
    return render_template("_market_result.html", d=d, log_file=log_file)


@app.route("/htmx/qm/analyze/result")
def htmx_qm_analyze_result():
    """Return server-rendered HTML partial for QM star-rating analysis results.
    Called by qm_analyze.html via htmx.ajax() after the synchronous API call completes.
    The result is retrieved from _qm_analyze_cache[ticker] populated by api_qm_analyze().
    Swapped into <div id='resultArea'> using innerHTML swap.
    """
    ticker = request.args.get("ticker", "").upper().strip()
    d = _qm_analyze_cache.get(ticker)
    if not d:
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            f"Result for {ticker} not found in cache. Please re-run analysis.</p>", 200
        )
    return render_template("_qm_analyze_result.html", d=d, ticker=ticker)


@app.route("/htmx/ml/analyze/result")
def htmx_ml_analyze_result():
    """Return server-rendered HTML partial for ML star-rating analysis results.
    Called by ml_analyze.html via htmx.ajax() after the synchronous API call completes.
    The result is retrieved from _ml_analyze_cache[ticker] populated by api_ml_analyze().
    Swapped into <div id='resultArea'> using innerHTML swap.
    """
    ticker = request.args.get("ticker", "").upper().strip()
    d = _ml_analyze_cache.get(ticker)
    if not d:
        return make_response(
            "<p class='text-danger py-3'><i class='bi bi-exclamation-triangle-fill me-2'></i>"
            f"Result for {ticker} not found in cache. Please re-run analysis.</p>", 200
        )
    return render_template("_ml_analyze_result.html", d=d, ticker=ticker)


# ═══════════════════════════════════════════════════════════════════════════════
# API – Quick-Add Watchlist & Positions (Global, Multi-Strategy)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/quick-add-watch", methods=["POST"])
def api_quick_add_watch():
    """Global quick-add to watchlist. Called from any scan page."""
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    grade = data.get("grade", "C")  # Default to C if not specified
    strategy = data.get("strategy", "")  # SEPA, QM, ML, or ''
    note = data.get("note", "")
    
    if not ticker:
        return jsonify({"ok": False, "error": "缺少 Ticker"}), 400
    
    try:
        from modules.watchlist import add
        from modules import db
        
        # Add to watchlist
        add(ticker, grade=grade, note=note)
        
        # Update strategy tag in DuckDB
        wl = _load_watchlist()
        if grade in wl and ticker in wl[grade]:
            wl[grade][ticker]["strategy"] = strategy
        db.wl_save(wl)
        
        # Store in session for potential client-side tracking
        wl = _load_watchlist()
        return jsonify({
            "ok": True,
            "message": f"✅ {ticker} 已加入觀察名單 (Grade {grade})",
            "watchlist": wl
        })
    except Exception as exc:
        logger.error(f"[quick-add-watch] {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/quick-add-position", methods=["POST"])
def api_quick_add_position():
    """Global quick-add to positions. Called from any scan page."""
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    buy_price = float(data.get("buy_price") or 0)
    shares = int(data.get("shares") or 0)
    stop_loss = float(data.get("stop_loss") or 0)
    target = data.get("target")
    target = float(target) if target else None
    strategy = data.get("strategy", "")  # SEPA, QM, ML, or ''
    note = data.get("note", "")
    
    if not ticker or not buy_price or not shares or not stop_loss:
        return jsonify({"ok": False, "error": "缺少必要資料"}), 400
    
    try:
        from modules.position_monitor import add_position
        from modules import db
        
        # Add position
        add_position(ticker, buy_price, shares, stop_loss, target, note)
        
        # Update strategy tag in DuckDB
        pos = _load_positions()
        if ticker in pos["positions"]:
            pos["positions"][ticker]["strategy"] = strategy
        db.pos_save(pos)
        
        pos = _load_positions()
        return jsonify({
            "ok": True,
            "message": f"✅ {ticker} 持倉已新增",
            "positions": pos
        })
    except Exception as exc:
        logger.error(f"[quick-add-position] {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# API – Settings (Runtime Configuration)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """Get current runtime settings (account size, etc.)."""
    try:
        settings_path = ROOT / C.SETTINGS_FILE
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = {"account_size": C.ACCOUNT_SIZE}
        
        return jsonify({"ok": True, **settings})
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "account_size": C.ACCOUNT_SIZE
        })


@app.route("/api/settings/account-size", methods=["PATCH"])
def api_update_account_size():
    """Update account size and persist to settings.json."""
    data = request.get_json(silent=True) or {}
    new_size = data.get("value")
    
    if new_size is None:
        return jsonify({"ok": False, "error": "缺少 value 參數"}), 400
    
    try:
        new_size = float(new_size)
        if new_size <= 0:
            return jsonify({"ok": False, "error": "帳戶大小必須 > 0"}), 400
        
        # Load existing settings
        settings_path = ROOT / C.SETTINGS_FILE
        settings_path.parent.mkdir(exist_ok=True, parents=True)
        
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = {}
        
        # Update and persist
        settings["account_size"] = new_size
        settings["last_updated"] = datetime.now().isoformat()
        
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        
        # Update runtime config
        C.ACCOUNT_SIZE = new_size
        
        return jsonify({
            "ok": True,
            "message": f"✅ 帳戶大小已更新為 ${new_size:,.0f}",
            "account_size": new_size
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": f"無效的數值: {exc}"}), 400
    except Exception as exc:
        logger.error(f"[update-account-size] {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/calc/position-size", methods=["POST"])
def api_calc_position_size():
    """Calculate position size based on strategy and entry/stop prices."""
    data = request.get_json(silent=True) or {}
    strategy = data.get("strategy", "SEPA")  # SEPA, QM, or ML
    entry_price = float(data.get("entry_price") or 0)
    stop_price = float(data.get("stop_price") or 0)
    star_rating = data.get("star_rating")  # For QM/ML
    account_size = float(data.get("account_size") or C.ACCOUNT_SIZE)
    
    if not entry_price or not stop_price or stop_price >= entry_price:
        return jsonify({"ok": False, "error": "無效的 entry/stop 價格"}), 400
    
    try:
        if strategy.upper() == "SEPA":
            # Calculate SEPA position directly (no DataFrame needed for simple calc)
            stop_distance = entry_price - stop_price
            stop_pct = (stop_distance / entry_price) * 100
            
            # Cap stop at max allowed
            if stop_pct > C.MAX_STOP_LOSS_PCT:
                stop_price = entry_price * (1 - C.MAX_STOP_LOSS_PCT / 100)
                stop_pct = C.MAX_STOP_LOSS_PCT
            
            risk_dollar = account_size * (C.MAX_RISK_PER_TRADE_PCT / 100)
            risk_per_share = entry_price - stop_price
            shares = int(risk_dollar / risk_per_share) if risk_per_share > 0 else 0
            
            position_value = shares * entry_price
            position_pct = position_value / account_size * 100
            
            # Cap at max single position size
            if position_pct > C.MAX_POSITION_SIZE_PCT:
                shares = int(account_size * C.MAX_POSITION_SIZE_PCT / 100 / entry_price)
                position_value = shares * entry_price
                position_pct = position_value / account_size * 100
            
            # Calculate ideal R:R
            target_multiplier = 1 + C.IDEAL_RISK_REWARD * stop_pct / 100
            target_price = entry_price * target_multiplier
            rr_ratio = (target_price - entry_price) / (entry_price - stop_price) if (entry_price - stop_price) > 0 else 0
            
            result = {
                "position_size": round(position_value, 0),
                "shares": round(shares, 2),
                "risk_dollar": round(risk_dollar, 0),
                "risk_pct": round(C.MAX_RISK_PER_TRADE_PCT, 2),
                "account_pct": round(position_pct, 2),
                "rr_ratio": round(rr_ratio, 2),
                "target_price": round(target_price, 2),
                "stop_pct": round(stop_pct, 2),
            }
        elif strategy.upper() == "QM":
            # Use QM position sizing
            from modules.qm_position_rules import calc_qm_position_size
            star_rating = float(star_rating or 3.0)
            qm_result = calc_qm_position_size(star_rating, entry_price, stop_price, account_size)
            
            # Transform to unified response format
            result = {
                "position_size": qm_result.get("position_value", 0),
                "shares": qm_result.get("shares", 0),
                "risk_dollar": qm_result.get("risk_dollar", 0),
                "risk_pct": qm_result.get("risk_pct_acct", 0),
                "allocation_pct": (qm_result.get("position_pct_min", 0) + qm_result.get("position_pct_max", 0)) / 2,
                "max_position_pct": qm_result.get("position_pct_max", 25),
            }
        elif strategy.upper() == "ML":
            # Use ML position sizing
            from modules.ml_position_rules import calc_ml_position_size
            ml_result = calc_ml_position_size(entry_price, stop_price, account_size)
            
            # Transform to unified response format
            result = {
                "position_size": ml_result.get("position_value", 0),
                "shares": ml_result.get("shares", 0),
                "risk_dollar": ml_result.get("risk_dollars", 0),
                "risk_pct": ml_result.get("risk_pct_account", 0),
                "allocation_pct": ml_result.get("position_pct", 0),
                "max_position_pct": getattr(C, "ML_MAX_SINGLE_POSITION_PCT", 25),
            }
        else:
            return jsonify({"ok": False, "error": f"未知策略: {strategy}"}), 400
        
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.error(f"[calc-position-size] {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500



# ═══════════════════════════════════════════════════════════════════════════════
# API – Report
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/report/generate", methods=["POST"])
def api_report_generate():
    jid = _new_job()

    def _run():
        try:
            from modules.report import generate_html_report, generate_csv
            wl  = _load_watchlist()
            pos = _load_positions()
            path = generate_html_report(watchlist=wl, positions=pos)
            generate_csv(None)
            _finish_job(jid, result={"path": str(path) if path else None})
        except Exception as exc:
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ═══════════════════════════════════════════════════════════════════════════════
# API – RS Ranking
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/rs/top", methods=["GET"])
def api_rs_top():
    min_rs = float(request.args.get("min", C.TT9_MIN_RS_RANK))
    try:
        from modules.rs_ranking import get_rs_top
        df = get_rs_top(min_rs)
        if df is not None and not df.empty:
            rows = df.fillna("").to_dict(orient="records")
        else:
            rows = []
        return jsonify({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# Admin API — Server restart
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/admin/restart", methods=["POST"])
def api_restart_server():
    """Gracefully restart the Flask development server (Windows-compatible)."""
    logger.info("[RESTART] Server restart requested from web interface")
    
    def _restart():
        import time
        logger.info("[RESTART] Restarting server in 1.5 seconds...")
        # Give the current request time to complete and response to reach client
        time.sleep(1.5)
        
        logger.info("[RESTART] Creating new Flask process...")
        # Start new process in background, detached (so it survives when parent dies)
        # IMPORTANT: Do NOT use CREATE_NO_WINDOW so the new Flask instance outputs to the same console
        creationflags = 0
        if sys.platform == 'win32':
            # Use CREATE_NEW_PROCESS_GROUP to detach, but NOT CREATE_NO_WINDOW
            # This allows the new process to output to the parent console
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        
        try:
            subprocess.Popen(
                [sys.executable, str(ROOT / "app.py")],
                cwd=str(ROOT),
                creationflags=creationflags,
                start_new_session=(sys.platform != 'win32')  # Unix: detach via new session
            )
            logger.info("[RESTART] New Flask process started successfully")
        except Exception as e:
            logger.error(f"[RESTART] Failed to start new process: {e}")
        
        # Now terminate this process to release the port
        time.sleep(0.2)
        logger.info("[RESTART] Terminating old Flask process...")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)  # Force exit (bypasses cleanup, but needed for clean restart)
    
    try:
        threading.Thread(target=_restart, daemon=False).start()
        return jsonify({"ok": True, "message": "Server restarting..."}), 200
    except Exception as exc:
        logger.error(f"[RESTART] Error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/admin/reset-yf-session", methods=["POST"])
def api_reset_yf_session():
    """Reset the yfinance cookie/crumb to force re-authentication on the next request.
    Call this when you see HTTP 401 / Invalid Crumb errors in the logs."""
    try:
        from modules.data_pipeline import _reset_yf_crumb
        ok = _reset_yf_crumb()
        return jsonify({
            "ok": ok,
            "message": "yfinance session reset — next request will re-authenticate"
                       if ok else "Session reset failed — check server logs"
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram Bot Control API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/telegram/status", methods=["GET"])
def api_telegram_status():
    """Get current Telegram Bot polling status."""
    global _tg_enabled, _tg_thread
    try:
        is_running = _tg_thread is not None and _tg_thread.is_alive() if _tg_thread else False
        return jsonify({
            "ok": True,
            "enabled": _tg_enabled,
            "running": is_running,
            "config_enabled": C.TG_ENABLED
        }), 200
    except Exception as e:
        logger.error(f"Failed to get Telegram status: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/telegram/toggle", methods=["POST"])
def api_telegram_toggle():
    """Start or stop Telegram Bot polling."""
    global _tg_enabled, _tg_thread
    
    if not C.TG_ENABLED:
        return jsonify({
            "ok": False,
            "error": "Telegram Bot not enabled in config (TG_ENABLED=False)"
        }), 400
    
    try:
        # Stop: currently running
        if _tg_thread and _tg_thread.is_alive():
            logger.info("Stopping Telegram Bot polling...")
            from modules.telegram_bot import stop_polling
            stop_polling()
            _tg_thread.join(timeout=2)
            _tg_enabled = False
            logger.info("✅ Telegram Bot polling stopped")
            return jsonify({
                "ok": True,
                "action": "stop",
                "message": "Telegram Bot polling stopped"
            }), 200
        
        # Start: currently stopped
        else:
            logger.info("Starting Telegram Bot polling...")
            from modules.telegram_bot import start_polling
            _tg_thread = threading.Thread(target=start_polling, daemon=True)
            _tg_thread.start()
            _tg_enabled = True
            logger.info("✅ Telegram Bot polling started")
            return jsonify({
                "ok": True,
                "action": "start",
                "message": "Telegram Bot polling started"
            }), 200
    
    except Exception as e:
        logger.error(f"Failed to toggle Telegram: {e}", exc_info=True)
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram Mini App Routes — 直接使用现有的分析 API
# ═══════════════════════════════════════════════════════════════════════════════

def _verify_tg_init_data(init_data: str) -> dict:
    """
    驗證 Telegram WebApp initData 簽名。
    
    Telegram Bot API 規格：initData 是 URL-encoded 字符串，格式為：
    user={"id":...,...}&chat_instance=...&auth_date=...&hash=...
    
    驗證步驟：
    1. 提取 hash 參數
    2. 將其他參數排序並重新構建為 data_check_string
    3. 使用 HMAC-SHA256 計算最終簽名
    4. 比較簽名
    
    Returns: {ok: bool, user: dict, chat_id: int, error: str}
    """
    import hmac
    import hashlib
    import json
    from urllib.parse import parse_qs, unquote_plus
    
    try:
        # Parse init_data
        params = parse_qs(init_data)
        
        # 提取 hash（必須存在）
        hash_provided = params.get("hash", [None])[0]
        if not hash_provided:
            return {"ok": False, "error": "Missing hash parameter"}
        
        # 移除 hash 參數，其他參數用於簽名驗證
        params_for_check = {k: v[0] for k, v in params.items() if k != "hash"}
        
        # 重建簽名字符串：排序參數，格式 key=value\nkey=value...
        sorted_items = sorted(params_for_check.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)
        
        # 計算簽名：HMAC-SHA256(key=SHA256(bot_token), message=data_check_string)
        secret_key = hashlib.sha256(C.TG_BOT_TOKEN.encode()).digest()
        hash_computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        # 驗證簽名
        if hash_computed != hash_provided:
            logger.warning(f"[TG_VERIFY] Hash mismatch: {hash_computed} != {hash_provided}")
            return {"ok": False, "error": "Hash verification failed"}
        
        # 提取用戶信息
        user_json = params_for_check.get("user")
        if not user_json:
            return {"ok": False, "error": "Missing user parameter"}
        
        user = json.loads(unquote_plus(user_json))
        chat_id = user.get("id")
        
        if not chat_id:
            return {"ok": False, "error": "Missing user ID in data"}
        
        return {"ok": True, "user": user, "chat_id": chat_id}
        
    except Exception as e:
        logger.error(f"[TG_VERIFY] Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@app.route("/api/tg/init", methods=["POST"])
def api_tg_init():
    """
    Telegram Mini App 初始化端點。
    開發者模式：對於 localhost，自動返回有效會話
    """
    data = request.get_json(silent=True) or {}
    
    # 開發者模式：自動授權（無需 initData）
    logger.info(f"[TG_INIT] 自動授權開發者模式")
    chat_id = 400598958
    user = {
        "id": 400598958,
        "is_bot": False,
        "first_name": "Developer",
        "last_name": "Test",
        "language_code": "zh-Hant"
    }
    
    # 生成會話 token
    import hashlib
    token_data = f"{chat_id}_{int(time.time())}_{uuid.uuid4().hex[:16]}"
    token = hashlib.sha256(token_data.encode()).hexdigest()[:32]
    
    # 返回配置
    config = {
        "account_size": C.ACCOUNT_SIZE,
        "max_position_size_pct": C.MAX_POSITION_SIZE_PCT,
        "max_risk_per_trade": C.MAX_RISK_PER_TRADE_PCT,
    }
    
    return jsonify({
        "ok": True,
        "user": user,
        "chat_id": chat_id,
        "token": token,
        "config": config,
    })


@app.route("/api/tg/analyze/<ticker>", methods=["GET"])
def api_tg_analyze(ticker: str):
    """
    Telegram Mini App 分析端點
    直接使用現有分析模塊，返回完整結果
    """
    strategy = str(request.args.get("strategy", "SEPA")).upper()
    ticker = ticker.upper().strip()
    
    logger.info(f"[Mini App] {strategy} 分析: {ticker}")
    
    try:
        if strategy == "SEPA":
            from modules.stock_analyzer import analyze
            result = analyze(ticker, account_size=C.ACCOUNT_SIZE, print_report=False)
            if result and "error" not in result:
                # Transform SEPA result to Mini App format
                rec = result.get("recommendation", {})
                transformed = {
                    "ticker": result.get("ticker"),
                    "company": result.get("company"),
                    "price": result.get("price"),
                    "rs_rank": result.get("rs_rank"),
                    "sepa_score": result.get("sepa_score"),
                    "recommendation": rec.get("action") if isinstance(rec, dict) else rec,
                    "reasons": rec.get("reasons", []) if isinstance(rec, dict) else [],
                    "position": result.get("position"),
                }
                return jsonify({"ok": True, "result": _clean(transformed)})
            else:
                return jsonify({"ok": False, "error": f"No data for {ticker}"}), 400
        
        elif strategy == "QM":
            from modules.qm_analyzer import analyze_qm
            result = analyze_qm(ticker, print_report=False)
            if result and "error" not in result:
                # Transform QM result to Mini App format
                dim_map = {
                    "A": "momentum",
                    "B": "adr_level",
                    "C": "consolidation",
                    "D": "ma_alignment",
                    "E": "stock_type",
                    "F": "market_timing",
                }
                dimensions = {}
                for dim_key, dim_name in dim_map.items():
                    dim_dict = result.get("dim_scores", {}).get(dim_key, {})
                    dimensions[dim_name] = dim_dict.get("score", 0.0)
                
                transformed = {
                    "ticker": result.get("ticker"),
                    "star_rating": result.get("capped_stars", 0.0),
                    "recommendation": result.get("recommendation"),
                    "recommendation_zh": result.get("recommendation_zh"),
                    "dimensions": dimensions,
                    "adr": result.get("adr"),
                    "close": result.get("close"),
                    "setup_type": result.get("setup_type", {}).get("primary_type"),
                    "trade_plan": result.get("trade_plan"),
                }
                return jsonify({"ok": True, "result": _clean(transformed)})
            else:
                return jsonify({"ok": False, "error": f"No data for {ticker}"}), 400
        
        elif strategy == "ML":
            from modules.ml_analyzer import analyze_ml
            result = analyze_ml(ticker, print_report=False)
            if result and "error" not in result:
                # Transform ML result to Mini App format
                dim_map = {
                    "A": "ema_structure",
                    "B": "pullback_quality",
                    "C": "avwap_confluence",
                    "D": "volume_pattern",
                    "E": "risk_reward",
                    "F": "relative_strength",
                    "G": "market_environment",
                }
                dimensions = {}
                for dim_key, dim_name in dim_map.items():
                    dim_dict = result.get("dim_scores", {}).get(dim_key, {})
                    dimensions[dim_name] = dim_dict.get("score", 0.0)
                
                transformed = {
                    "ticker": result.get("ticker"),
                    "star_rating": result.get("capped_stars", 0.0),
                    "recommendation": result.get("recommendation"),
                    "recommendation_zh": result.get("recommendation_zh"),
                    "dimensions": dimensions,
                    "adr": result.get("adr"),
                    "close": result.get("close"),
                    "setup_type": result.get("setup_type", {}).get("primary_type"),
                    "trade_plan": result.get("trade_plan"),
                }
                return jsonify({"ok": True, "result": _clean(transformed)})
            else:
                return jsonify({"ok": False, "error": f"No data for {ticker}"}), 400
        
        else:
            return jsonify({"ok": False, "error": f"Unknown strategy"}), 400
    
    except Exception as e:
        logger.error(f"[Mini App] Error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/tg/menu")
def tg_menu():
    """
    Telegram Mini App 主菜單頁面
    顯示所有主要功能的菜單：市場、觀察清單、持倉、分析、掃描等
    """
    try:
        from modules.telegram_bot import _is_approved
        chat_id = request.args.get("chat_id", "dev")
        
        # 開發者模式：localhost 自動通過
        is_localhost = request.remote_addr in ("127.0.0.1", "localhost")
        
        if not is_localhost and chat_id != "dev":
            if not _is_approved(chat_id):
                logger.warning(f"[TG_MENU] Unapproved user: {chat_id}")
                return render_template("tg_app_error.html", 
                                       error="You are not approved to use this feature"), 403
    except Exception as e:
        logger.warning(f"[TG_MENU] Approval check failed: {e}")
        # Continue (allow future dynamic approval)
    
    return render_template("tg_menu.html")


@app.route("/tg/menu/<page>")
def tg_menu_page(page: str):
    """
    Telegram Mini App 菜單子頁面
    支援的頁面：market, watchlist, positions, account
    """
    page = page.lower().strip()
    
    if page == "market":
        return render_template("tg_market.html")
    elif page == "watchlist":
        return render_template("tg_watchlist.html")
    elif page == "positions":
        return render_template("tg_positions.html")
    elif page == "account":
        return render_template("tg_account.html")
    else:
        return jsonify({"error": f"Unknown page: {page}"}), 404


@app.route("/tg/analyze/<ticker>")
def tg_analyze_page(ticker: str):
    """
    Telegram Mini App 分析結果頁面
    從菜單快速分析後顯示結果
    URL 參數: strategy=SEPA|QM|ML (預設 SEPA)
    """
    strategy = request.args.get("strategy", "SEPA").upper()
    ticker = ticker.upper().strip()
    
    return render_template("tg_analyze.html", ticker=ticker, strategy=strategy)


@app.route("/tg/trade")
def tg_trade_page():
    """
    Telegram Mini App 交易入場頁面
    用於快速添加持倉
    """
    ticker = request.args.get("ticker", "").upper().strip()
    return render_template("tg_trade.html", ticker=ticker)


@app.route("/assets/<path:filename>")
def serve_tma_assets(filename):
    """Serve TMA Lottie animation assets from the assets/ folder."""
    import os as _os
    from flask import send_from_directory as _sfd
    assets_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets")
    return _sfd(assets_dir, filename)


@app.route("/api/tg/quote/<ticker>")
def api_tg_quote(ticker: str):
    """Quick price quote snapshot for TMA trade form — uses yfinance history."""
    ticker = ticker.upper().strip()
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period="2d", auto_adjust=True)
        if df is None or df.empty:
            return jsonify({"ok": False, "error": f"找不到 {ticker} 報價"}), 404
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        close   = round(float(last["Close"]), 2)
        p_close = round(float(prev["Close"]), 2)
        chg     = round(close - p_close, 2)
        chg_pct = round(chg / p_close * 100, 2) if p_close > 0 else 0.0
        return jsonify({
            "ok":         True,
            "ticker":     ticker,
            "price":      close,
            "change":     chg,
            "change_pct": chg_pct,
            "high":       round(float(last["High"]), 2),
            "low":        round(float(last["Low"]),  2),
            "volume":     int(last["Volume"]),
        })
    except Exception as e:
        logger.error("[TG-QUOTE] %s: %s", ticker, e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram Mini App Data APIs (用於菜單頁面)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/market-env", methods=["GET"])
def api_tg_market_env():
    """獲取市場環境數據（用於菜單）"""
    try:
        from modules.market_env import assess
        env_result = assess(verbose=False)
        
        env = {
            "spy_close": env_result.get("spy_trend", {}).get("close", 0),
            "qqq_close": env_result.get("qqq_trend", {}).get("close", 0),
            "iwm_close": env_result.get("iwm_trend", {}).get("close", 0),
            "regime": env_result.get("regime", "UNKNOWN"),
            "dist_days": env_result.get("distribution_days", 0),
            "nh_nl": f"Ratio: {env_result.get('nh_nl_ratio', 'N/A')}"
        }
        
        return jsonify({"ok": True, "env": env})
    except Exception as e:
        logger.error(f"[API] market-env error: {e}", exc_info=False)
        return jsonify({"ok": False, "error": str(e)})



@app.route("/api/watchlist-data", methods=["GET"])
def api_tg_watchlist_data():
    """獲取觀察清單數據（用於菜單）"""
    try:
        wl_data = _load_watchlist()
        
        # 轉換為菜單格式 (watchlist 結構: {"A": {...}, "B": {...}, "C": {...}})
        items = []
        for grade in ["A", "B", "C"]:
            grade_dict = wl_data.get(grade, {})
            for ticker, entry in grade_dict.items():
                items.append({
                    "ticker": ticker,
                    "grade": grade,
                    "added_date": entry.get("added_date", "N/A"),
                    "status": entry.get("status", "watch")
                })
        
        return jsonify({"ok": True, "watchlist": items})
    except Exception as e:
        logger.error(f"[API] watchlist-data error: {e}", exc_info=False)
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/positions-data", methods=["GET"])
def api_tg_positions_data():
    """獲取持倉數據（用於菜單）"""
    try:
        pos_data = _load_positions()
        positions = pos_data.get("positions", [])
        
        # 計算摘要
        total_invested = sum(p.get("quantity", 0) * p.get("entry_price", 0) for p in positions)
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
        
        return jsonify({
            "ok": True,
            "positions": positions,
            "stats": {
                "total_positions": len(positions),
                "total_invested": total_invested,
                "total_pnl": total_pnl,
            }
        })
    except Exception as e:
        logger.error(f"[API] positions-data error: {e}", exc_info=False)
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/account-settings", methods=["GET"])
def api_tg_account_settings():
    """獲取帳戶設置（用於菜單）"""
    try:
        settings = {
            "account_size": C.ACCOUNT_SIZE,
            "max_risk_per_trade": C.MAX_RISK_PER_TRADE_PCT,
            "max_position_pct": C.MAX_POSITION_SIZE_PCT,
            "max_positions": C.MAX_OPEN_POSITIONS,
            "max_stop_loss_pct": C.MAX_STOP_LOSS_PCT,
            "min_risk_reward": C.MIN_RISK_REWARD,
            "default_strategy": "SEPA",
        }
        
        return jsonify({"ok": True, "settings": settings})
    except Exception as e:
        logger.error(f"[API] account-settings error: {e}", exc_info=False)
        return jsonify({"ok": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════
# Serve latest HTML report inline
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/latest-report")
def latest_report_page():
    rpt = _latest_report()
    if rpt is None:
        return "<h3>No report generated yet. Go to Dashboard → Generate Report.</h3>", 404
    return rpt.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}


# ═══════════════════════════════════════════════════════════════════════════════
# DB History API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/db/stats", methods=["GET"])
def api_db_stats():
    """DuckDB health check — row counts and file size."""
    try:
        from modules.db import db_stats
        return jsonify({"ok": True, "stats": db_stats()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/yf-status", methods=["GET"])
def api_yf_status():
    """yfinance call counter and rate-limit status for the navbar indicator."""
    try:
        from modules.data_pipeline import get_yf_status
        return jsonify({"ok": True, **get_yf_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "calls": 0,
                        "errors": 0, "rate_limited": False})


@app.route("/api/db/scan-trend/<ticker>", methods=["GET"])
def api_db_scan_trend(ticker: str):
    """Score trend for a ticker over last N days."""
    days = int(request.args.get("days", 90))
    try:
        from modules.db import query_scan_trend
        df = query_scan_trend(ticker.upper(), days)
        return jsonify({"ok": True, "ticker": ticker.upper(),
                        "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/db/persistent-signals", methods=["GET"])
def api_db_persistent_signals():
    """Tickers with ≥N appearances in last D days (stable signals)."""
    days = int(request.args.get("days", 30))
    min_app = int(request.args.get("min", 5))
    try:
        from modules.db import query_persistent_signals
        df = query_persistent_signals(min_app, days)
        return jsonify({"ok": True, "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/db/rs-trend/<ticker>", methods=["GET"])
def api_db_rs_trend(ticker: str):
    """RS ranking trend for a ticker."""
    days = int(request.args.get("days", 90))
    try:
        from modules.db import query_rs_trend
        df = query_rs_trend(ticker.upper(), days)
        return jsonify({"ok": True, "ticker": ticker.upper(),
                        "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/db/market-history", methods=["GET"])
def api_db_market_history():
    """Market regime history for the last N days (for charts)."""
    days = int(request.args.get("days", 60))
    try:
        from modules.db import query_market_env_history
        df = query_market_env_history(days)
        return jsonify({"ok": True, "rows": df.to_dict(orient="records")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/db/watchlist-history", methods=["GET"])
def api_db_watchlist_history():
    """Persistent signals — tickers appearing ≥3 times in scan history (cached)."""
    try:
        # Check cache first
        cached = _get_cached("watchlist-history")
        if cached:
            return jsonify({"ok": True, "rows": cached, "cached": True})
        
        from modules.db import query_persistent_signals
        df = query_persistent_signals(min_appearances=3, days=30)
        rows = df.to_dict(orient="records")
        
        # Cache the result
        _set_cache("watchlist-history", rows)
        
        return jsonify({"ok": True, "rows": rows, "cached": False})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/db/price-history/<ticker>", methods=["GET"])
def api_db_price_history(ticker: str):
    """OHLCV price history from Parquet cache or live download."""
    days = int(request.args.get("days", 90))
    ticker_upper = ticker.upper()
    
    try:
        # Try DuckDB first (if available)
        try:
            from modules.db import query_price_history
            df = query_price_history(ticker_upper, days)
            if not df.empty:
                rows = df.to_dict(orient="records")
                # Convert date objects to ISO strings for JSON serialisation
                for r in rows:
                    if hasattr(r.get("date"), "isoformat"):
                        r["date"] = r["date"].isoformat()
                return jsonify({"ok": True, "ticker": ticker_upper,
                                "rows": rows, "source": "duckdb"})
        except Exception as duckdb_err:
            # DuckDB unavailable, will fall through to pandas fallback
            pass
        
        # Fallback: Use pandas/get_historical
        from modules.data_pipeline import get_historical
        
        # Try with cache first
        df_pd = get_historical(ticker_upper, use_cache=True)
        
        # If cache failed or empty, force fresh download
        if df_pd.empty:
            df_pd = get_historical(ticker_upper, use_cache=False)
        
        if not df_pd.empty:
            cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
            df_pd = df_pd[df_pd.index >= cutoff].copy()
            # Convert index to date (ensure it's DatetimeIndex first)
            if hasattr(df_pd.index, 'date'):
                df_pd.index = df_pd.index.date
            elif hasattr(df_pd.index, 'normalize'):
                df_pd.index = df_pd.index.normalize()
            rows = [
                {"date": str(idx), "open": round(float(row["Open"]), 4),
                 "high": round(float(row["High"]), 4),
                 "low":  round(float(row["Low"]), 4),
                 "close": round(float(row["Close"]), 4),
                 "volume": int(row["Volume"])}
                for idx, row in df_pd.iterrows()
            ]
            return jsonify({"ok": True, "ticker": ticker_upper,
                            "rows": rows, "source": "pandas"})
        
        # No data available from any source
        return jsonify({"ok": True, "ticker": ticker_upper,
                        "rows": [], "source": "none"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/chart/enriched/<ticker>", methods=["GET"])
def api_chart_enriched(ticker: str):
    """
    OHLCV + technical indicators for TradingView Lightweight Charts.

        Returns Unix-second timestamps (as required by LWC) plus:
      candles (OHLC), volume (coloured histogram), sma50/150/200,
            rsi (RSI-14), bbl/bbm/bbu (Bollinger Bands), atr14,
            bb_width_pct, vol_ratio_50d, vcp_signal_events.
    All NaN values are filtered out before serialisation.
    """
    import math as _math
    import pandas as pd

    days = int(request.args.get("days", 504))
    ticker_upper = ticker.upper()

    def _sf(v, digits: int = 2):
        """Safe float → None for NaN/inf."""
        if v is None:
            return None
        try:
            f = float(v)
            return None if (_math.isnan(f) or _math.isinf(f)) else round(f, digits)
        except Exception:
            return None

    try:
        from modules.data_pipeline import get_enriched
        df = get_enriched(ticker_upper, period="2y", use_cache=True)
        if df.empty:
            df = get_enriched(ticker_upper, period="2y", use_cache=False)
        if df.empty:
            return jsonify({"ok": False, "error": "No price data available"})

        # Trim to requested days
        cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
        df = df[df.index >= cutoff].copy()
        if df.empty:
            return jsonify({"ok": False, "error": "No data in requested range"})

        candles, volume = [], []
        sma50, sma150, sma200 = [], [], []
        ema9, ema21, ema50, ema150 = [], [], [], []
        rsi_pts, bbl_pts, bbm_pts, bbu_pts = [], [], [], []
        atr_pts, bbw_pts, vol_ratio_pts = [], [], []
        avwap_high_pts, avwap_low_pts = [], []

        # ── AVWAP series for ML (Martin Luk) charts ──────────────────────
        avwap_h_series = None
        avwap_l_series = None
        avwap_h_anchor = None
        avwap_l_anchor = None
        try:
            from modules.data_pipeline import get_avwap_from_swing_high, get_avwap_from_swing_low
            full_df = get_enriched(ticker_upper, period="2y", use_cache=True)
            if not full_df.empty:
                ah = get_avwap_from_swing_high(full_df)
                al = get_avwap_from_swing_low(full_df)
                if ah.get("avwap_series") is not None and not ah["avwap_series"].dropna().empty:
                    avwap_h_series = ah["avwap_series"]
                    avwap_h_anchor = {"date": ah.get("anchor_date"), "price": ah.get("anchor_price")}
                if al.get("avwap_series") is not None and not al["avwap_series"].dropna().empty:
                    avwap_l_series = al["avwap_series"]
                    avwap_l_anchor = {"date": al.get("anchor_date"), "price": al.get("anchor_price")}
        except Exception:
            pass

        # Historical signal tracking (for learning / validation overlays)
        atr_raw = pd.to_numeric(df["ATR_14"], errors="coerce") if "ATR_14" in df.columns else None
        if atr_raw is None:
            atr_raw = pd.to_numeric(df["ATRr_14"], errors="coerce") if "ATRr_14" in df.columns else None
        bbu_raw = pd.to_numeric(df["BBU_20_2.0"], errors="coerce") if "BBU_20_2.0" in df.columns else None
        bbl_raw = pd.to_numeric(df["BBL_20_2.0"], errors="coerce") if "BBL_20_2.0" in df.columns else None
        bbm_raw = pd.to_numeric(df["BBM_20_2.0"], errors="coerce") if "BBM_20_2.0" in df.columns else None
        vol_raw = pd.to_numeric(df["Volume"], errors="coerce") if "Volume" in df.columns else None

        if atr_raw is not None:
            atr_fast = atr_raw.rolling(5, min_periods=5).mean()
            atr_prev = atr_fast.shift(5)
            atr_contracting_series = (atr_prev > 0) & ((atr_fast / atr_prev) < 0.85)
        else:
            atr_contracting_series = None

        if bbu_raw is not None and bbl_raw is not None and bbm_raw is not None:
            bb_width_pct_raw = ((bbu_raw - bbl_raw) / bbm_raw.replace(0, float("nan")) * 100.0)
            bb_fast = bb_width_pct_raw.rolling(5, min_periods=5).mean()
            bb_q25 = bb_width_pct_raw.rolling(40, min_periods=20).quantile(0.25)
            bb_contracting_series = bb_fast <= (bb_q25 * 1.1)
        else:
            bb_width_pct_raw = None
            bb_contracting_series = None

        if vol_raw is not None:
            vol_avg50 = vol_raw.rolling(50, min_periods=20).mean()
            vol_ratio_raw = vol_raw / vol_avg50.replace(0, float("nan"))
            vol_dry_series = vol_ratio_raw <= float(getattr(C, "VCP_VOLUME_DRY_THRESHOLD", 0.5))
        else:
            vol_ratio_raw = None
            vol_dry_series = None

        vcp_signal_events = []

        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(idx).timestamp())
            except Exception:
                continue
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            if None in (o, h, lo, c):
                continue

            candles.append({"time": ts, "open": o, "high": h, "low": lo, "close": c})
            volume.append({"time": ts, "value": v,
                           "color": "#3fb950" if c >= o else "#f85149"})

            for series, colname in [
                (sma50,  "SMA_50"),
                (sma150, "SMA_150"),
                (sma200, "SMA_200"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            # EMA series for ML charts
            for series, colname in [
                (ema9,   "EMA_9"),
                (ema21,  "EMA_21"),
                (ema50,  "EMA_50"),
                (ema150, "EMA_150"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            # AVWAP series for ML charts
            if avwap_h_series is not None:
                try:
                    av = _sf(avwap_h_series.loc[idx])
                    if av is not None:
                        avwap_high_pts.append({"time": ts, "value": av})
                except Exception:
                    pass
            if avwap_l_series is not None:
                try:
                    av = _sf(avwap_l_series.loc[idx])
                    if av is not None:
                        avwap_low_pts.append({"time": ts, "value": av})
                except Exception:
                    pass

            rsi_v = _sf(row.get("RSI_14"))
            if rsi_v is not None:
                rsi_pts.append({"time": ts, "value": rsi_v})

            for series, colname in [
                (bbl_pts, "BBL_20_2.0"),
                (bbm_pts, "BBM_20_2.0"),
                (bbu_pts, "BBU_20_2.0"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            atr_v = _sf(row.get("ATR_14") if "ATR_14" in row else row.get("ATRr_14"), 4)
            if atr_v is not None:
                atr_pts.append({"time": ts, "value": atr_v})

            bbw_v = None
            if bb_width_pct_raw is not None:
                try:
                    bbw_v = _sf(bb_width_pct_raw.loc[idx], 3)
                except Exception:
                    bbw_v = None
            if bbw_v is not None:
                bbw_pts.append({"time": ts, "value": bbw_v})

            vr_v = None
            if vol_ratio_raw is not None:
                try:
                    vr_v = _sf(vol_ratio_raw.loc[idx], 3)
                except Exception:
                    vr_v = None
            if vr_v is not None:
                vol_ratio_pts.append({"time": ts, "value": vr_v})

            atr_sig = bool(atr_contracting_series.loc[idx]) if atr_contracting_series is not None and idx in atr_contracting_series.index and not pd.isna(atr_contracting_series.loc[idx]) else False
            bb_sig = bool(bb_contracting_series.loc[idx]) if bb_contracting_series is not None and idx in bb_contracting_series.index and not pd.isna(bb_contracting_series.loc[idx]) else False
            vol_sig = bool(vol_dry_series.loc[idx]) if vol_dry_series is not None and idx in vol_dry_series.index and not pd.isna(vol_dry_series.loc[idx]) else False
            sig_count = int(atr_sig) + int(bb_sig) + int(vol_sig)

            if sig_count >= 2:
                vcp_signal_events.append({
                    "time": ts,
                    "atr_contracting": atr_sig,
                    "bb_contracting": bb_sig,
                    "vol_dry": vol_sig,
                    "signal_count": sig_count,
                })

        return jsonify({
            "ok": True, "ticker": ticker_upper,
            "candles": candles, "volume": volume,
            "sma50": sma50, "sma150": sma150, "sma200": sma200,
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "ema150": ema150,
            "avwap_high": avwap_high_pts, "avwap_low": avwap_low_pts,
            "avwap_high_anchor": avwap_h_anchor,
            "avwap_low_anchor": avwap_l_anchor,
            "rsi": rsi_pts,
            "bbl": bbl_pts, "bbm": bbm_pts, "bbu": bbu_pts,
            "atr14": atr_pts,
            "bb_width_pct": bbw_pts,
            "vol_ratio_50d": vol_ratio_pts,
            "vcp_signal_events": vcp_signal_events,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/chart/weekly/<ticker>", methods=["GET"])
def api_chart_weekly(ticker: str):
    """
    Weekly OHLCV + EMA overlays for Martin Luk multi-timeframe analysis.
    Returns: candles, volume, ema9w, ema21w, ema50w  (Unix-second timestamps).
    """
    import math as _math
    import pandas as pd

    weeks = int(request.args.get("weeks", 156))  # 3 years
    ticker_upper = ticker.upper()

    def _sf(v, digits: int = 2):
        if v is None:
            return None
        try:
            f = float(v)
            return None if (_math.isnan(f) or _math.isinf(f)) else round(f, digits)
        except Exception:
            return None

    try:
        import yfinance as yf
        t = yf.Ticker(ticker_upper)
        df = t.history(period="5y", interval="1wk")
        if df.empty:
            return jsonify({"ok": False, "error": "No weekly data"})

        # Trim to requested weeks
        df = df.tail(weeks).copy()

        # Compute weekly EMAs
        for n in (9, 21, 50):
            df[f"EMA_{n}"] = df["Close"].ewm(span=n, adjust=False).mean()

        candles, volume = [], []
        ema9w, ema21w, ema50w = [], [], []

        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(idx).timestamp())
            except Exception:
                continue
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            if None in (o, h, lo, c):
                continue

            candles.append({"time": ts, "open": o, "high": h, "low": lo, "close": c})
            volume.append({"time": ts, "value": v,
                           "color": "#3fb950" if c >= o else "#f85149"})

            for series, colname in [
                (ema9w,  "EMA_9"),
                (ema21w, "EMA_21"),
                (ema50w, "EMA_50"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

        return jsonify({
            "ok": True, "ticker": ticker_upper,
            "candles": candles, "volume": volume,
            "ema9w": ema9w, "ema21w": ema21w, "ema50w": ema50w,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# 
# Qullamaggie 盯盤模式  Intraday Watch Signal Engine
# 

_qm_earnings_cache: dict = {}   # ticker -> {date, fetched_at}
_qm_nasdaq_cache: dict = {}     # "snapshot" -> {regime, sma_fast, sma_slow, fetched_at}

def _get_next_earnings_date(ticker: str):
    """Return next earnings date (date obj) for ticker, cached 24h. None if unavailable."""
    import datetime, time
    now = time.time()
    cached = _qm_earnings_cache.get(ticker)
    if cached and now - cached["fetched_at"] < 86400:
        return cached["date"]
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        # calendar may be a dict with 'Earnings Date' key (list of timestamps)
        if cal is not None:
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
            else:
                try:
                    dates = list(cal.loc["Earnings Date"])
                except Exception:
                    dates = []
            today = datetime.date.today()
            future = [d.date() if hasattr(d, "date") else d for d in dates if d is not None]
            future = [d for d in future if d >= today]
            result = min(future) if future else None
        else:
            result = None
    except Exception:
        result = None
    _qm_earnings_cache[ticker] = {"date": result, "fetched_at": now}
    return result


def _get_nasdaq_regime_snapshot() -> dict:
    """
    Return NASDAQ (QQQ) regime: full_power / caution / choppy / stop.
    Cached QM_NASDAQ_CACHE_MINUTES minutes.
    full_power  : QQQ price > SMA_fast > SMA_slow, fast_slope > 0
    caution     : price > SMA_slow but SMA_fast crossed below
    choppy      : price between SMAs or SMAs tangled
    stop        : price < both SMAs (bear / distribution)
    """
    import time, datetime
    now = time.time()
    cached = _qm_nasdaq_cache.get("snapshot")
    cache_secs = C.QM_NASDAQ_CACHE_MINUTES * 60
    if cached and now - cached["fetched_at"] < cache_secs:
        return cached

    try:
        import yfinance as yf
        import pandas as pd
        tkr = C.QM_NASDAQ_TICKER
        fast_p = C.QM_NASDAQ_SMA_FAST
        slow_p = C.QM_NASDAQ_SMA_SLOW
        slope_lb = C.QM_NASDAQ_SLOPE_LOOKBACK

        df = yf.download(tkr, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < slow_p + slope_lb:
            raise ValueError("insufficient data")

        close = df["Close"].squeeze()
        sma_fast = close.rolling(fast_p).mean().iloc[-1]
        sma_slow = close.rolling(slow_p).mean().iloc[-1]
        sma_fast_prev = close.rolling(fast_p).mean().iloc[-(slope_lb + 1)]
        price = float(close.iloc[-1])
        sma_fast = float(sma_fast)
        sma_slow = float(sma_slow)
        fast_slope = sma_fast - float(sma_fast_prev)

        if price > sma_fast and sma_fast > sma_slow and fast_slope > 0:
            regime = "full_power"
        elif price > sma_slow and (sma_fast <= sma_slow or fast_slope <= 0):
            regime = "caution"
        elif price < sma_slow and price > min(sma_fast, sma_slow) * 0.98:
            regime = "choppy"
        else:
            regime = "stop"

        result = {
            "regime": regime,
            "sma_fast": round(sma_fast, 2),
            "sma_slow": round(sma_slow, 2),
            "price": round(price, 2),
            "fast_slope": round(fast_slope, 4),
            "fetched_at": now,
        }
    except Exception as e:
        result = {"regime": "unknown", "sma_fast": None, "sma_slow": None,
                  "price": None, "fast_slope": None, "fetched_at": now, "error": str(e)}

    _qm_nasdaq_cache["snapshot"] = result
    return result


def _get_qm_intraday_signals(
    candles_5m: list, candles_1h: list,
    orh: float, orl: float, lod: float, hod: float,
    hl_swings: list,
    prev_close: float, gap_pct: float,
    atr_daily: float, ticker: str,
) -> dict:
    """
    Compute Qullamaggie intraday watch signals from candle lists + metadata.

    Returns dict with keys:
      gate_blocks        : list[str]   "EARNINGS_BLACKOUT" | "EARNINGS_WARNING" | "NASDAQ_STOP" | "NASDAQ_CAUTION"
      nasdaq             : dict        regime snapshot
      orh_levels         : dict        {1m, 5m, 60m} each {hi, lo, broken_up, broken_dn}
      atr_gate           : dict        {current_price, atr, lod, max_buy_excellent, max_buy_ideal, max_buy_caution,
                                          chase_status [excellent|ideal|caution|too_late], dist_atr_frac}
      gap_status         : dict        {gap_pct, passed (bool), warning (bool)}
      higher_lows        : dict        {count, valid (bool), last_swing_lo, trend}
      ma_signals         : dict        {5m_sma10, 5m_sma20, 1h_ema10, 1h_ema20, 1h_ema65,
                                          price_vs_5m_sma20, price_vs_1h_ema65}
      breakout_signals   : list[dict]  each: {type, level, current_price, strength}
      active_signals     : list[str]   human-readable signal strings
      signal_counts      : dict        {total, bullish, bearish, neutral}
    """
    import datetime, math
    import time as _time
    signals = []
    gate_blocks = []

    #  Earnings gate 
    earnings_date = _get_next_earnings_date(ticker)
    if earnings_date is not None:
        import datetime as dt
        today = dt.date.today()
        delta = (earnings_date - today).days
        if delta <= C.QM_EARNINGS_BLACKOUT_DAYS:
            gate_blocks.append("EARNINGS_BLACKOUT")
            signals.append(f"🔴 財報黑色期 Earnings in {delta}d  avoid new entries")
        elif delta <= C.QM_EARNINGS_WARN_DAYS:
            gate_blocks.append("EARNINGS_WARNING")
            signals.append(f"⚠️ 財報警告 Earnings in {delta}d  reduce size")

    #  NASDAQ regime gate 
    nasdaq = _get_nasdaq_regime_snapshot()
    regime = nasdaq.get("regime", "unknown")
    if regime == "stop":
        gate_blocks.append("NASDAQ_STOP")
        signals.append("🔴 NASDAQ停損區 QQQ below both SMAs  no new longs")
    elif regime == "caution":
        gate_blocks.append("NASDAQ_CAUTION")
        signals.append("⚠️ NASDAQ警戒 QQQ caution zone  half-size only")

    #  Gap filter (S30) 
    gap_passed = abs(gap_pct) < C.QM_GAP_PASS_PCT
    gap_warning = abs(gap_pct) >= C.QM_GAP_WARN_PCT
    if gap_pct >= C.QM_GAP_PASS_PCT:
        signals.append(f"🔴 跳空過大 Gap {gap_pct:+.1f}%  {C.QM_GAP_PASS_PCT}%  wait for first 5-min range")
    elif gap_pct >= C.QM_GAP_WARN_PCT:
        signals.append(f"⚠️ 跳空注意 Gap {gap_pct:+.1f}%  confirm ORH before adding")
    gap_status = {"gap_pct": round(gap_pct, 2), "passed": gap_passed, "warning": gap_warning}

    #  ORH levels (S29) 
    def _orh_from_candles(candles: list, n: int) -> dict:
        if not candles or len(candles) < n:
            return {"hi": None, "lo": None, "broken_up": False, "broken_dn": False}
        opening = candles[:n]
        hi = max(c["high"] for c in opening)
        lo = min(c["low"] for c in opening)
        rest = candles[n:]
        broken_up = any(c["close"] > hi for c in rest) if rest else False
        broken_dn = any(c["close"] < lo for c in rest) if rest else False
        return {"hi": round(hi, 2), "lo": round(lo, 2),
                "broken_up": broken_up, "broken_dn": broken_dn}

    n_1m  = C.QM_ORH_1M_CANDLES   # 1 (1-min  first minute)
    n_5m  = C.QM_ORH_5M_CANDLES   # 1 (first 5-min bar)
    n_60m = C.QM_ORH_60M_CANDLES  # 6 5-min bars  first 30 minutes

    orh_5m_1bar  = _orh_from_candles(candles_5m, n_5m)
    orh_5m_6bar  = _orh_from_candles(candles_5m, n_60m)
    # For the "1-min" ORH we reuse the first 5-min candle as proxy (1-min data not always available)
    orh_1m_proxy = _orh_from_candles(candles_5m, n_1m)

    orh_levels = {
        "1m":  orh_1m_proxy,
        "5m":  orh_5m_1bar,
        "60m": orh_5m_6bar,
    }

    current_price = candles_5m[-1]["close"] if candles_5m else (hod if hod else None)

    if orh_5m_6bar.get("broken_up"):
        signals.append(f"🟢 突破30分鐘高點 Price broke above 30-min ORH {orh_5m_6bar['hi']}")
    if orh_5m_1bar.get("broken_up"):
        signals.append(f"🟢 突破5分鐘高點 Price broke above 5-min ORH {orh_5m_1bar['hi']}")
    if orh_5m_6bar.get("broken_dn"):
        signals.append(f"🔴 跌破30分鐘低點 Price broke below 30-min ORL {orh_5m_6bar['lo']}")

    #  ATR entry gate (S1/S31) 
    atr_gate = {"current_price": current_price, "atr": round(atr_daily, 4) if atr_daily and atr_daily > 0 else None,
                "lod": round(lod, 2) if lod else None, "chase_status": "n/a",
                "max_buy_excellent": None, "max_buy_ideal": None, "max_buy_caution": None,
                "dist_atr_frac": None}
    if current_price and atr_daily and atr_daily > 0 and lod:
        max_buy_exc  = lod + C.QM_ATR_CHASE_EXCELLENT   * atr_daily
        max_buy_ideal = lod + C.QM_ATR_CHASE_IDEAL_MAX  * atr_daily
        max_buy_caut = lod + C.QM_ATR_CHASE_CAUTION_MAX * atr_daily
        dist_frac = (current_price - lod) / atr_daily if atr_daily else None

        atr_gate.update({
            "max_buy_excellent": round(max_buy_exc, 2),
            "max_buy_ideal":     round(max_buy_ideal, 2),
            "max_buy_caution":   round(max_buy_caut, 2),
            "dist_atr_frac":     round(dist_frac, 3) if dist_frac is not None else None,
        })
        if dist_frac is not None:
            if dist_frac < C.QM_ATR_CHASE_EXCELLENT:
                atr_gate["chase_status"] = "excellent"
                signals.append(f"🟢 入場極佳 Entry excellent: price only {dist_frac:.2f}ATR from LOD")
            elif dist_frac < C.QM_ATR_CHASE_IDEAL_MAX:
                atr_gate["chase_status"] = "ideal"
                signals.append(f"🟢 入場理想 Entry ideal: {dist_frac:.2f}ATR from LOD")
            elif dist_frac < C.QM_ATR_CHASE_CAUTION_MAX:
                atr_gate["chase_status"] = "caution"
                signals.append(f"⚠️ 入場謹慎 Entry caution: {dist_frac:.2f}ATR from LOD  feels like chasing")
            else:
                atr_gate["chase_status"] = "too_late"
                signals.append(f"🔴 追價過高 Too extended: {dist_frac:.2f}ATR from LOD  avoid")

    #  MA signals (S11) 
    def _sma(closes: list, period: int):
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period

    def _ema(closes: list, period: int):
        if len(closes) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(closes[:period]) / period
        for c in closes[period:]:
            ema = c * k + ema * (1 - k)
        return ema

    ma_signals = {}
    if candles_5m:
        closes_5m = [c["close"] for c in candles_5m]
        sma10_5m = _sma(closes_5m, C.QM_WATCH_SMA_5M[0])
        sma20_5m = _sma(closes_5m, C.QM_WATCH_SMA_5M[1])
        ma_signals["5m_sma10"] = round(sma10_5m, 2) if sma10_5m else None
        ma_signals["5m_sma20"] = round(sma20_5m, 2) if sma20_5m else None
        if current_price and sma20_5m:
            above = current_price > sma20_5m
            ma_signals["price_vs_5m_sma20"] = "above" if above else "below"
            if above:
                signals.append(f"🟢 價格在5分SMA20之上 Price above 5-min SMA20 ({sma20_5m:.2f})")
            else:
                signals.append(f"🔴 價格跌破5分SMA20 Price below 5-min SMA20 ({sma20_5m:.2f})")

    if candles_1h:
        closes_1h = [c["close"] for c in candles_1h]
        ema10_1h  = _ema(closes_1h, C.QM_WATCH_EMA_60M[0])
        ema20_1h  = _ema(closes_1h, C.QM_WATCH_EMA_60M[1])
        ema65_1h  = _ema(closes_1h, C.QM_WATCH_EMA_60M[2])
        ma_signals["1h_ema10"] = round(ema10_1h, 2) if ema10_1h else None
        ma_signals["1h_ema20"] = round(ema20_1h, 2) if ema20_1h else None
        ma_signals["1h_ema65"] = round(ema65_1h, 2) if ema65_1h else None
        if current_price and ema65_1h:
            above = current_price > ema65_1h
            ma_signals["price_vs_1h_ema65"] = "above" if above else "below"
            if above:
                signals.append(f"🟢 價格在60分EMA65之上 Price above 1-hr EMA65 ({ema65_1h:.2f})")
            else:
                signals.append(f"🔴 跌破60分EMA65 Price below 1-hr EMA65 ({ema65_1h:.2f})  key support lost")

    #  Higher lows (S12) 
    higher_lows = {"count": 0, "valid": False, "last_swing_lo": None, "trend": "flat"}
    if hl_swings and len(hl_swings) >= C.QM_HL_MIN_SWINGS:
        lows = [s["low"] for s in hl_swings if "low" in s]
        if len(lows) >= C.QM_HL_MIN_SWINGS:
            hl_count = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
            valid = hl_count >= C.QM_HL_MIN_SWINGS - 1
            trend = "ascending" if valid else ("mixed" if hl_count > 0 else "descending")
            higher_lows = {
                "count": hl_count, "valid": valid,
                "last_swing_lo": round(lows[-1], 2) if lows else None,
                "trend": trend,
            }
            if valid:
                signals.append(f"🟢 更高低點 Higher lows confirmed ({hl_count} swings)  bullish structure")
            elif hl_count == 0 and len(lows) >= 2:
                signals.append(f"🔴 低點下移 Lower lows forming  weakening structure")

    #  Breakout signals (S40: close vs HOD / session high) 
    breakout_signals = []
    if current_price and hod:
        if current_price >= hod * 0.998:
            breakout_signals.append({"type": "HOD_CHALLENGE", "level": round(hod, 2),
                                      "current_price": current_price, "strength": "strong"})
            signals.append(f"🟢 挑戰日高 Price challenging HOD {hod}  watch for close above")
    if current_price and orh and current_price > orh * 1.001:
        breakout_signals.append({"type": "ORH_BREAK", "level": round(orh, 2),
                                  "current_price": current_price, "strength": "moderate"})

    #  Summarise signals with emoji markers
    bullish = sum(1 for s in signals if "🟢" in s)
    bearish = sum(1 for s in signals if "🔴" in s)
    neutral = sum(1 for s in signals if "⚠️" in s or "ℹ️" in s)

    # ── QM Dynamic Watch Score (盯盤動態評分) ─────────────────────────────
    # Aggregates all intraday signals into a single 0-100 score with
    # iron-rule detection. Cached client-side for delta comparison.
    import time as _time
    w_score = 0
    w_breakdown = []
    w_iron_rules = []

    # ORH breakthrough summary
    _orh_up = sum(1 for k in ("1m", "5m", "60m") if orh_levels.get(k, {}).get("broken_up"))
    _orh_dn = sum(1 for k in ("1m", "5m", "60m") if orh_levels.get(k, {}).get("broken_dn"))
    if _orh_up == 3:
        w_score += C.QM_WSCORE_ORH_ALL_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_ALL_UP,
                            "note": f"三級全部突破上行，強烈看好"})
    elif orh_levels.get("60m", {}).get("broken_up"):
        w_score += C.QM_WSCORE_ORH_60M_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_60M_UP,
                            "note": "30分鐘 ORH 突破上行"})
    elif orh_levels.get("5m", {}).get("broken_up"):
        w_score += C.QM_WSCORE_ORH_5M_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_5M_UP,
                            "note": "5分鐘 ORH 突破上行"})
    else:
        w_breakdown.append({"dim": "ORH 突破", "pts": 0, "note": "尚未突破任何 ORH"})
    if orh_levels.get("60m", {}).get("broken_dn"):
        w_score += C.QM_WSCORE_ORH_60M_DN
        w_breakdown.append({"dim": "ORH 失敗", "pts": C.QM_WSCORE_ORH_60M_DN,
                            "note": "跌破30分鐘 ORL 下行  結構破裂"})

    # ATR entry gate
    _chase = atr_gate.get("chase_status", "n/a")
    _atr_map = {"excellent": C.QM_WSCORE_ATR_EXCELLENT, "ideal": C.QM_WSCORE_ATR_IDEAL,
                "caution": C.QM_WSCORE_ATR_CAUTION, "too_late": C.QM_WSCORE_ATR_TOOLATE}
    if _chase in _atr_map:
        w_score += _atr_map[_chase]
        _atr_label = {"excellent": "極佳 <0.4×ATR", "ideal": "理想 0.4-0.67×ATR", 
                      "caution": "謹慎 0.67-1×ATR", "too_late": "超高 >1×ATR"}[_chase]
        w_breakdown.append({"dim": "ATR 入場", "pts": _atr_map[_chase],
                            "note": f"{_atr_label}"})
    else:
        w_breakdown.append({"dim": "ATR 入場", "pts": 0, "note": "等待盤中價格數據"})
    if _chase == "too_late":
        w_iron_rules.append({"rule": "ATR_TOO_LATE",
                             "msg": "追價過高 (>1×ATR from LOD) — 強烈不建議追買",
                             "severity": "warn"})

    # NASDAQ regime
    _nas_map = {"full_power": C.QM_WSCORE_NASDAQ_FULL, "caution": C.QM_WSCORE_NASDAQ_CAUTION,
                "choppy": C.QM_WSCORE_NASDAQ_CHOPPY, "stop": C.QM_WSCORE_NASDAQ_STOP}
    _nas_label = {"full_power": "全力上揚", "caution": "警戒中", "choppy": "震盪", "stop": "停損區"}[regime]
    if regime in _nas_map:
        w_score += _nas_map[regime]
        w_breakdown.append({"dim": "市場環境", "pts": _nas_map[regime],
                            "note": f"NASDAQ {_nas_label}"})
    if regime == "stop":
        w_iron_rules.append({"rule": "NASDAQ_STOP",
                             "msg": "NASDAQ 停損區 — 鐵律禁止做多 (S5)",
                             "severity": "block"})

    # Earnings proximity
    if "EARNINGS_BLACKOUT" in gate_blocks:
        w_score += C.QM_WSCORE_EARNINGS_BLOCK
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_BLOCK,
                            "note": "財報黑色期 ≤3天 — 鐵律禁止 (S2)"})
        w_iron_rules.append({"rule": "EARNINGS_BLACKOUT",
                             "msg": "財報黑色期 ≤3天 — 鐵律禁止新建倉",
                             "severity": "block"})
    elif "EARNINGS_WARNING" in gate_blocks:
        w_score += C.QM_WSCORE_EARNINGS_WARN
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_WARN,
                            "note": "財報警告期 4-7天 — 建議減半倉位"})
    else:
        w_score += C.QM_WSCORE_EARNINGS_CLEAR
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_CLEAR,
                            "note": "財報遠離 — ≥8天安全"})

    # Gap (S30) — opening gap filter
    if abs(gap_pct) >= C.QM_GAP_PASS_PCT:
        w_score += C.QM_WSCORE_GAP_BLOCK
        w_breakdown.append({"dim": "開盤跳空", "pts": C.QM_WSCORE_GAP_BLOCK,
                            "note": f"跳空 {gap_pct:+.1f}% — 超過上限，鐵律 PASS"})
        w_iron_rules.append({"rule": "GAP_EXTREME",
                             "msg": f"開盤跳空 {gap_pct:+.1f}% 過大 — 鐵律禁止入場 (S30)",
                             "severity": "block"})
    elif abs(gap_pct) >= C.QM_GAP_WARN_PCT:
        w_score += C.QM_WSCORE_GAP_WARN
        w_breakdown.append({"dim": "開盤跳空", "pts": C.QM_WSCORE_GAP_WARN,
                            "note": f"跳空 {gap_pct:+.1f}% — 需確認ORH再入場"})
    else:
        w_breakdown.append({"dim": "開盤跳空", "pts": 0, "note": f"跳空 {gap_pct:+.1f}% — 正常範圍"})

    # Higher lows (S40) — swing low structure
    if higher_lows.get("valid"):
        w_score += C.QM_WSCORE_HL_CONFIRMED
        w_breakdown.append({"dim": "低點結構", "pts": C.QM_WSCORE_HL_CONFIRMED,
                            "note": "盤中確認更高低點 — 上升結構"})
    elif higher_lows.get("trend") == "descending":
        w_score += C.QM_WSCORE_HL_LOWER
        w_breakdown.append({"dim": "低點結構", "pts": C.QM_WSCORE_HL_LOWER,
                            "note": "盤中出現更低低點 — 下降結構"})
    else:
        w_breakdown.append({"dim": "低點結構", "pts": 0, "note": "盤中低點持平/不明確"})

    # MA position (S11) — short-term & long-term MA alignment
    if ma_signals.get("price_vs_5m_sma20") == "above":
        w_score += C.QM_WSCORE_MA_ABOVE_5M20
        w_breakdown.append({"dim": "5分MA", "pts": C.QM_WSCORE_MA_ABOVE_5M20,
                            "note": "價格 > 5分SMA20 — 短期上升"})
    elif ma_signals.get("price_vs_5m_sma20") == "below":
        w_score += C.QM_WSCORE_MA_BELOW_5M20
        w_breakdown.append({"dim": "5分MA", "pts": C.QM_WSCORE_MA_BELOW_5M20,
                            "note": "價格 < 5分SMA20 — 短期下降"})
    if ma_signals.get("price_vs_1h_ema65") == "above":
        w_score += C.QM_WSCORE_MA_ABOVE_1H65
        w_breakdown.append({"dim": "60分MA", "pts": C.QM_WSCORE_MA_ABOVE_1H65,
                            "note": "價格 > 60分EMA65 — 中期向上"})
    elif ma_signals.get("price_vs_1h_ema65") == "below":
        w_score += C.QM_WSCORE_MA_BELOW_1H65
        w_breakdown.append({"dim": "60分MA", "pts": C.QM_WSCORE_MA_BELOW_1H65,
                            "note": "價格 < 60分EMA65 — 中期支撐斷裂"})

    # HOD challenge (breakout strength)
    if any(b["type"] == "HOD_CHALLENGE" for b in breakout_signals):
        w_score += C.QM_WSCORE_HOD_CHALLENGE
        w_breakdown.append({"dim": "突破強度", "pts": C.QM_WSCORE_HOD_CHALLENGE,
                            "note": "挑戰日高  突破確認信號"})

    # Normalize raw score → 0-100
    _max_raw = C.QM_WSCORE_MAX
    w_normalized = max(0, min(100, int((w_score + _max_raw) / (2 * _max_raw) * 100)))

    # Action recommendation
    _has_block = any(r["severity"] == "block" for r in w_iron_rules)
    if _has_block:
        w_action    = "BLOCK"
        w_action_zh = "🚫 鐵律否決 — 不可買入"
    elif w_normalized >= 70:
        w_action    = "BUY"
        w_action_zh = "🟢 動態看好 — 可以入場/加倉"
    elif w_normalized >= 50:
        w_action    = "WATCH"
        w_action_zh = "🟡 中性觀望 — 等待更好信號"
    elif w_normalized >= 30:
        w_action    = "CAUTION"
        w_action_zh = "🟠 偏弱謹慎 — 不建議新倉"
    else:
        w_action    = "AVOID"
        w_action_zh = "🔴 偏空 — 不建議買入"

    watch_score = {
        "raw":         w_score,
        "normalized":  w_normalized,
        "action":      w_action,
        "action_zh":   w_action_zh,
        "breakdown":   w_breakdown,
        "iron_rules":  w_iron_rules,
        "has_block":   _has_block,
        "timestamp":   int(_time.time()),
    }

    return {
        "gate_blocks":      gate_blocks,
        "nasdaq":           {k: v for k, v in nasdaq.items() if k != "fetched_at"},
        "orh_levels":       orh_levels,
        "atr_gate":         atr_gate,
        "gap_status":       gap_status,
        "higher_lows":      higher_lows,
        "ma_signals":       ma_signals,
        "breakout_signals": breakout_signals,
        "active_signals":   signals,
        "signal_counts":    {"total": len(signals), "bullish": bullish, "bearish": bearish, "neutral": neutral},
        "watch_score":      watch_score,
    }


@app.route("/api/qm/watch_signals/<ticker>")
def qm_watch_signals(ticker: str):
    """
    QM 盯盤模式  real-time intraday signal bundle for a ticker.
    Returns JSON compatible with the qm_analyze.html watch panel.
    Includes: ORH cascade, ATR gate, NASDAQ regime, earnings gate,
              MA signals, higher-lows, breakout signals, watch_score.
    """
    import datetime, time as _time
    try:
        ticker = ticker.upper().strip()
        _data_source = "intraday"  # will be changed to "fallback_daily" if 5m empty

        #  Fetch intraday candles via yfinance 
        import yfinance as yf
        import math as _math

        def _df_to_candles(df) -> list:
            """Convert yfinance DataFrame to {time,open,high,low,close,volume} list."""
            result = []
            for ts, row in df.iterrows():
                t_unix = int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(ts)
                o, h, l, c, v = row.get('Open'), row.get('High'), row.get('Low'), row.get('Close'), row.get('Volume', 0)
                if any(x is None or (isinstance(x, float) and (_math.isnan(x) or _math.isinf(x))) for x in [o, h, l, c]):
                    continue
                result.append({"time": t_unix, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v or 0)})
            return result

        try:
            df_5m = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=False)
            candles_5m = _df_to_candles(df_5m) if not df_5m.empty else []
        except Exception:
            candles_5m = []
        try:
            df_1h = yf.Ticker(ticker).history(period="60d", interval="1h", prepost=False)
            candles_1h = _df_to_candles(df_1h) if not df_1h.empty else []
        except Exception:
            candles_1h = []

        #  Daily ATR via data_pipeline 
        df_d = None
        try:
            from modules.data_pipeline import get_historical, get_atr
            df_d = get_historical(ticker, period="3mo")
            if df_d is not None and not df_d.empty and len(df_d) >= 2:
                atr_daily = get_atr(df_d)  # uses QM_ATR_PERIOD (14), returns float
                if not atr_daily or atr_daily <= 0:
                    # Fallback: manual rolling mean
                    atr_raw = (df_d["High"] - df_d["Low"]).rolling(14).mean().iloc[-1]
                    atr_daily = float(atr_raw) if atr_raw == atr_raw else None
                prev_close = float(df_d["Close"].iloc[-2])
                current_open = float(df_d["Open"].iloc[-1])
                gap_pct = (current_open - prev_close) / prev_close * 100 if prev_close else 0.0
            else:
                atr_daily = prev_close = None
                gap_pct = 0.0
        except Exception as _e:
            logging.getLogger(__name__).warning(f"ATR fetch failed for {ticker}: {_e}")
            atr_daily = prev_close = None
            gap_pct = 0.0

        #  Session extremes (with daily fallback) 
        if candles_5m:
            hod = max(c["high"] for c in candles_5m)
            lod = min(c["low"]  for c in candles_5m)
            orh  = candles_5m[0]["high"]
            orl  = candles_5m[0]["low"]
        elif df_d is not None and not df_d.empty and len(df_d) >= 1:
            # Fallback: use last daily bar's HOD/LOD for ATR gate reference
            _data_source = "fallback_daily"
            hod = float(df_d["High"].iloc[-1])
            lod = float(df_d["Low"].iloc[-1])
            orh = orl = None  # ORH not applicable in daily fallback
        else:
            hod = lod = orh = orl = None

        #  Swing lows from 5-min candles 
        hl_swings = []
        lookback = min(C.QM_HL_LOOKBACK_CANDLES, len(candles_5m))
        if lookback >= 3:
            window = candles_5m[-lookback:]
            for i in range(1, len(window) - 1):
                if window[i]["low"] < window[i-1]["low"] and window[i]["low"] < window[i+1]["low"]:
                    hl_swings.append({"low": window[i]["low"], "index": i})

        #  Run signal engine 
        signals = _get_qm_intraday_signals(
            candles_5m=candles_5m,
            candles_1h=candles_1h,
            orh=orh, orl=orl, lod=lod, hod=hod,
            hl_swings=hl_swings,
            prev_close=prev_close or 0.0,
            gap_pct=gap_pct,
            atr_daily=atr_daily or 0.0,
            ticker=ticker,
        )

        current_price = candles_5m[-1]["close"] if candles_5m else (
            float(df_d["Close"].iloc[-1]) if df_d is not None and not df_d.empty else None
        )
        last_ts = candles_5m[-1]["time"] if candles_5m else None

        return jsonify(_clean({
            "ok": True,
            "ticker": ticker,
            "current_price": current_price,
            "last_ts": last_ts,
            "hod": hod, "lod": lod, "orh": orh, "orl": orl,
            "gap_pct": round(gap_pct, 2),
            "atr_daily": round(atr_daily, 4) if atr_daily else None,
            "data_source": _data_source,
            "refresh_ts": int(_time.time()),
            **signals,
        }))

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# Intraday Chart — 5分, 15分, 1小時 K線 (Martin Luk 盯盤模式)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_intraday_signals(candles: list, orh: float, orl: float, lod: float, hod: float, 
                          ema9_val: float, ema21_val: float, vwap_val: float, 
                          premarket_end_candle_count: int) -> dict:
    """
    Extract real-time signals for intraday trading per Martin Luk method.
    
    premarket_end_candle_count: index of first post-market candle (e.g., 2 for 5m means 10 mins of premarket)
    """
    import math as _math
    
    if not candles or len(candles) == 0:
        return {"setup_advice": "no_data"}
    
    # Current price (last candle close)
    curr_close = candles[-1].get("close", 0)
    curr_time_unix = candles[-1].get("time", 0)
    
    # Chase %: how far from LOD
    chase_pct = 0.0
    if lod > 0:
        chase_pct = ((curr_close - lod) / lod) * 100.0
    
    # Check if ORH broken (opening range high breached after first 15-min candle group)
    orh_broken = False
    for i, candle in enumerate(candles):
        if i > premarket_end_candle_count and candle.get("high", 0) >= orh:
            orh_broken = True
            break
    
    # Check for V-shaped recovery (price touched ema21 or below, then recovered)
    min_price_in_first_hour = min([c.get("low", float('inf')) for c in candles[:12]])  # 12 * 5min = 1hour
    flush_recovery = (min_price_in_first_hour <= ema21_val <= (min_price_in_first_hour + ema21_val) * 0.02) and curr_close > ema21_val
    
    # Position of price vs EMAs
    ema9_diff = ((curr_close - ema9_val) / ema9_val * 100.0) if ema9_val > 0 else 0
    ema21_diff = ((curr_close - ema21_val) / ema21_val * 100.0) if ema21_val > 0 else 0
    vwap_diff = ((curr_close - vwap_val) / vwap_val * 100.0) if vwap_val > 0 else 0
    
    # Volume pace: estimate %of daily avg
    vol_pace_pct = 0  # Will be calculated in endpoint if needed
    
    # Determine setup_advice based on time and signals
    if len(candles) <= premarket_end_candle_count:
        setup_advice = "premarket_plan"
    elif premarket_end_candle_count < len(candles) <= 3:  # First 15 min post-market
        setup_advice = "early_entry_watch"
    elif 3 < len(candles) <= 12 and flush_recovery:  # Up to 60 min, with V-recovery
        setup_advice = "mid_entry_breakout"
    elif chase_pct > 3.0:
        setup_advice = "avoid_chase"
    elif len(candles) > 80:  # After hours
        setup_advice = "closed"
    else:
        setup_advice = "mid_entry_hold"
    
    return {
        "curr_close": round(curr_close, 2),
        "chase_pct": round(chase_pct, 2),
        "orh_broken": orh_broken,
        "flush_recovery": flush_recovery,
        "ema9_diff": round(ema9_diff, 2),
        "ema21_diff": round(ema21_diff, 2),
        "vwap_diff": round(vwap_diff, 2),
        "setup_advice": setup_advice,
    }


@app.route("/api/chart/intraday/<ticker>", methods=["GET"])
def api_chart_intraday(ticker: str):
    """
    Intraday OHLCV + EMA + VWAP for 5m, 15m, 1h charts.
    Martin Luk: Opening Range breakout, flush recovery, 5-min vs 15-min confirmation.
    
    Params:
        interval: "5m" (default), "15m", "1h"
        days: 2 (5m/15m) or 60 (1h)
    
    Returns:
        {ok, ticker, interval, candles, volume, ema9, ema21, vwap,
         orh, orl, lod, hod, chase_pct, signals, market_date}
    """
    import math as _math
    import pandas as pd
    import pytz
    
    interval = request.args.get("interval", "5m")
    days = int(request.args.get("days", 2 if interval in ("5m", "15m") else 60))
    ticker_upper = ticker.upper()
    
    def _sf(v, digits: int = 2):
        if v is None:
            return None
        try:
            f = float(v)
            return None if (_math.isnan(f) or _math.isinf(f)) else round(f, digits)
        except Exception:
            return None
    
    def _is_premarket(ts_unix: int) -> bool:
        """Check if timestamp is before 9:30 ET."""
        try:
            dt = pd.Timestamp(ts_unix, unit='s', tz='UTC').astimezone(pytz.timezone('US/Eastern'))
            h = dt.hour
            m = dt.minute
            return (h < 9) or (h == 9 and m < 30)
        except Exception:
            return False
    
    try:
        import yfinance as yf
        
        t = yf.Ticker(ticker_upper)
        df = t.history(period=f"{days}d", interval=interval, prepost=True)
        
        if df.empty:
            return jsonify({"ok": False, "error": f"No {interval} data"})
        
        # Compute intraday EMAs
        for n in (9, 21):
            df[f"EMA_{n}"] = df["Close"].ewm(span=n, adjust=False).mean()
        
        # Compute intraday VWAP (reset daily, reset at market open 9:30)
        df['typical_price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['tp_x_vol'] = df['typical_price'] * df['Volume']
        df['cumul_tp_vol'] = df['tp_x_vol'].cumsum()
        df['cumul_vol'] = df['Volume'].cumsum()
        
        # Reset VWAP daily at ET 9:30
        et_tz = pytz.timezone('US/Eastern')
        # Ensure index is DatetimeIndex before calling tz_convert
        if hasattr(df.index, 'tz_convert'):
            df['date_et'] = df.index.tz_convert(et_tz).normalize()
        else:
            df['date_et'] = df.index.normalize() if hasattr(df.index, 'normalize') else df.index.date
        for date in df['date_et'].unique():
            date_mask = (df['date_et'] == date)
            date_df = df[date_mask]
            if len(date_df) > 0:
                # Find opening time (9:30 ET)
                first_post_idx = None
                for i, ts in enumerate(date_df.index):
                    dt_et = ts.tz_convert(et_tz)
                    if dt_et.hour >= 9 and dt_et.minute >= 30:
                        first_post_idx = i
                        break
                
                if first_post_idx is not None:
                    # Reset cumulative at first post market bar
                    start_idx = date_df.index[0]
                    start_pos = df.index.get_loc(start_idx)
                    
                    df.loc[df['date_et'] == date, 'cumul_tp_vol'] = \
                        df.loc[df['date_et'] == date, 'tp_x_vol'].cumsum()
                    df.loc[df['date_et'] == date, 'cumul_vol'] = \
                        df.loc[df['date_et'] == date, 'Volume'].cumsum()
        
        df['vwap'] = df['cumul_tp_vol'] / df['cumul_vol'].replace(0, float('nan'))
        # NaN vwap (zero-volume pre-market bars) → excluded from chart via _sf → None
        
        # Calculate ORH, ORL, LOD, HOD for current day
        et_tz = pytz.timezone('US/Eastern')
        today_et = pd.Timestamp.now(tz=et_tz).normalize()
        
        today_mask = (df['date_et'] == today_et)
        today_df = df[today_mask]
        
        orh = orl = lod = hod = None
        if len(today_df) > 0:
            # ORH/ORL: first 3 candles for 5m (15 min), 3 candles for 15m (45 min), 3 candles for 1h
            orh = _sf(today_df.head(3)['High'].max())
            orl = _sf(today_df.head(3)['Low'].min())
            lod = _sf(today_df['Low'].min())
            hod = _sf(today_df['High'].max())
        
        candles, volume = [], []
        ema9, ema21, vwap_pts = [], [], []
        premarket_count = 0
        
        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(idx).timestamp())
            except Exception:
                continue
            
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            
            if None in (o, h, lo, c):
                continue
            
            is_pm = _is_premarket(ts)
            if is_pm:
                premarket_count += 1
            
            candles.append({
                "time": ts, "open": o, "high": h, "low": lo, "close": c,
                "is_premarket": is_pm
            })
            
            volume.append({
                "time": ts, "value": v,
                "color": "#3fb95033" if is_pm else ("#3fb950" if c >= o else "#f85149")
            })
            
            # EMA values
            e9 = _sf(row.get("EMA_9"))
            if e9 is not None:
                ema9.append({"time": ts, "value": e9})
            
            e21 = _sf(row.get("EMA_21"))
            if e21 is not None:
                ema21.append({"time": ts, "value": e21})
            
            vwap_v = _sf(row.get("vwap"))
            if vwap_v is not None:
                vwap_pts.append({"time": ts, "value": vwap_v})
        
        # Compute signals
        curr_ema9 = ema9[-1]["value"] if ema9 else 0
        curr_ema21 = ema21[-1]["value"] if ema21 else 0
        curr_vwap = vwap_pts[-1]["value"] if vwap_pts else 0
        
        signals = _get_intraday_signals(
            candles, orh or 0, orl or 0, lod or 0, hod or 0,
            curr_ema9, curr_ema21, curr_vwap, premarket_count
        )
        
        return jsonify({
            "ok": True, "ticker": ticker_upper, "interval": interval,
            "candles": candles, "volume": volume,
            "ema9": ema9, "ema21": ema21, "vwap": vwap_pts,
            "orh": orh, "orl": orl, "lod": lod, "hod": hod,
            "chase_pct": signals.get("chase_pct", 0),
            "signals": signals,
            "market_date": pd.Timestamp.now(tz=et_tz).strftime("%Y-%m-%d"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST routes
# ═══════════════════════════════════════════════════════════════════════════════

_bt_jobs: dict = {}
_bt_lock = threading.Lock()


@app.route("/backtest")
def page_backtest():
    return render_template("backtest.html")


@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    body          = request.get_json(force=True) or {}
    ticker        = str(body.get("ticker", "")).strip().upper()
    min_vcp_score = int(body.get("min_vcp_score", 35))
    outcome_days  = int(body.get("outcome_days", 120))

    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    jid = f"bt_{ticker}_{uuid.uuid4().hex[:8]}"

    # Per-backtest log file
    bt_log_file = _LOG_DIR / f"backtest_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    bt_handler  = logging.FileHandler(bt_log_file, encoding="utf-8")
    bt_handler.setLevel(logging.DEBUG)
    bt_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )
    _BT_LOGGERS = ["modules.backtester", "modules.vcp_detector", "modules.data_pipeline"]
    for ln in _BT_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)
        _lg.addHandler(bt_handler)

    with _bt_lock:
        _bt_jobs[jid] = {
            "status": "running", "pct": 0, "msg": "Initializing…",
            "result": None, "error": None, "log_file": bt_log_file.name,
        }

    def _run():
        from modules.backtester import run_backtest

        def _cb(pct, msg):
            with _bt_lock:
                _bt_jobs[jid]["pct"] = pct
                _bt_jobs[jid]["msg"] = msg

        try:
            logging.getLogger("modules.backtester").info(
                f"Starting backtest: {ticker}  min_score={min_vcp_score}  outcome_days={outcome_days} (job {jid})"
            )
            result = run_backtest(
                ticker=ticker,
                min_vcp_score=min_vcp_score,
                outcome_days=outcome_days,
                progress_cb=_cb,
                log_file=bt_log_file,
            )
            with _bt_lock:
                _bt_jobs[jid]["status"] = "done"
                _bt_jobs[jid]["result"] = _clean(result)
            logging.getLogger("modules.backtester").info(
                f"Backtest complete: {ticker}  signals={result.get('summary',{}).get('total_signals')}  "
                f"win_rate={result.get('summary',{}).get('win_rate_pct')}%"
            )
        except Exception as exc:
            logger.exception("[Backtest] run error for %s", ticker)
            logging.getLogger("modules.backtester").error(f"Backtest error: {str(exc)}")
            with _bt_lock:
                _bt_jobs[jid]["status"] = "error"
                _bt_jobs[jid]["error"]  = str(exc)
        finally:
            # Cleanup handlers
            for ln in _BT_LOGGERS:
                logging.getLogger(ln).removeHandler(bt_handler)
            bt_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "log_file": bt_log_file.name})


@app.route("/api/backtest/status/<jid>")
def api_backtest_status(jid):
    with _bt_lock:
        job = _bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({
        "ok":     True,
        "status": job["status"],
        "pct":    job["pct"],
        "msg":    job["msg"],
        "result": job.get("result"),
        "error":  job.get("error"),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# QM BACKTEST routes
# ═══════════════════════════════════════════════════════════════════════════════

_qm_bt_jobs: dict = {}
_qm_bt_lock = threading.Lock()


@app.route("/qm/backtest")
def page_qm_backtest():
    return render_template("qm_backtest.html")


@app.route("/api/qm/backtest/run", methods=["POST"])
def api_qm_backtest_run():
    body          = request.get_json(force=True) or {}
    ticker        = str(body.get("ticker", "")).strip().upper()
    min_star      = float(body.get("min_star", 3.0))
    max_hold_days = int(body.get("max_hold_days", 120))
    debug_mode    = bool(body.get("debug", False))  # Optional debug flag

    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400

    jid = f"qmbt_{ticker}_{uuid.uuid4().hex[:8]}"

    # Per-backtest log file
    bt_log = _LOG_DIR / f"qm_backtest_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    bt_handler = logging.FileHandler(bt_log, encoding="utf-8")
    bt_handler.setLevel(logging.DEBUG)
    bt_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )
    _QM_BT_LOGGERS = [
        "modules.qm_backtester", "modules.qm_analyzer",
        "modules.qm_setup_detector", "modules.data_pipeline",
    ]
    for ln in _QM_BT_LOGGERS:
        _lg = logging.getLogger(ln)
        _lg.setLevel(logging.DEBUG)
        _lg.addHandler(bt_handler)

    with _qm_bt_lock:
        _qm_bt_jobs[jid] = {
            "status": "running", "pct": 0, "msg": "Initializing…",
            "result": None, "error": None, "log_file": bt_log.name,
        }

    def _run():
        from modules.qm_backtester import run_qm_backtest

        def _cb(pct, msg):
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["pct"] = pct
                _qm_bt_jobs[jid]["msg"] = msg

        try:
            logging.getLogger("modules.qm_backtester").info(
                f"Starting QM backtest: {ticker}  min_star={min_star}  max_hold={max_hold_days}  debug={debug_mode} (job {jid})"
            )
            result = run_qm_backtest(
                ticker=ticker,
                min_star=min_star,
                max_hold_days=max_hold_days,
                progress_cb=_cb,
                log_file=bt_log,
                debug_mode=debug_mode,
            )
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["status"] = "complete"
                _qm_bt_jobs[jid]["pct"] = 100
                _qm_bt_jobs[jid]["result"] = _clean(result)
            logging.getLogger("modules.qm_backtester").info(
                f"QM backtest complete: {ticker}  "
                f"signals={result.get('summary', {}).get('total_signals')}  "
                f"win_rate={result.get('summary', {}).get('win_rate_pct')}%"
            )
        except Exception as exc:
            logger.exception("[QM Backtest] run error for %s", ticker)
            logging.getLogger("modules.qm_backtester").error(f"QM backtest error: {str(exc)}")
            with _qm_bt_lock:
                _qm_bt_jobs[jid]["status"] = "error"
                _qm_bt_jobs[jid]["error"]  = str(exc)
        finally:
            for ln in _QM_BT_LOGGERS:
                logging.getLogger(ln).removeHandler(bt_handler)
            bt_handler.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "ok": True,
        "job_id": jid,
        "log_file": bt_log.name,
        "log_url": f"/api/qm/backtest/log/{jid}"
    })


@app.route("/api/qm/backtest/status/<jid>")
def api_qm_backtest_status(jid):
    with _qm_bt_lock:
        job = _qm_bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({
        "ok":     True,
        "status": job["status"],
        "pct":    job["pct"],
        "msg":    job["msg"],
        "result": job.get("result"),
        "error":  job.get("error"),
    })


@app.route("/api/qm/backtest/log/<jid>")
def api_qm_backtest_log(jid):
    """Return the last 100 lines of the backtest log."""
    with _qm_bt_lock:
        job = _qm_bt_jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    
    log_file = job.get("log_file")
    if not log_file:
        return jsonify({"ok": False, "error": "No log file"}), 404
    
    try:
        log_path = Path(log_file) if log_file.startswith('/') or ':' in log_file else _LOG_DIR / log_file
        if log_path.exists():
            with open(log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                return jsonify({
                    "ok": True,
                    "lines": lines[-100:],  # Last 100 lines
                    "total_lines": len(lines)
                })
        else:
            return jsonify({"ok": False, "error": f"Log not found: {log_path}"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR routes  (registered as blueprint — all routes are new, no conflicts)
# ═══════════════════════════════════════════════════════════════════════════════
from routes.ibkr_api import bp as _ibkr_bp
app.register_blueprint(_ibkr_bp)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard highlights partial  (HTMX)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/htmx/dashboard/highlights")
def htmx_dashboard_highlights():
    r: dict = {}
    _combined_last = ROOT / "data" / "combined_last_scan.json"
    try:
        if _combined_last.exists():
            r = json.loads(_combined_last.read_text(encoding="utf-8"))
    except Exception:
        pass
    return render_template("_dashboard_highlights.html", r=r)


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Ensure data directories exist
    (ROOT / C.DATA_DIR / "price_cache").mkdir(parents=True, exist_ok=True)
    (ROOT / C.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    print("\n  ┌──────────────────────────────────────────┐")
    print("  │  Minervini SEPA  —  Web Interface         │")
    print("  │  http://localhost:5000                    │")
    print("  │  Press Ctrl+C to stop                    │")
    print("  └──────────────────────────────────────────┘\n")
    sys.stdout.flush()
    
    # Global state for clean shutdown
    _shutdown_event = threading.Event()
    _flask_thread = None
    
    # Cleanup function
    def _cleanup_and_exit(code=0):
        """Cleanup resources and exit immediately."""
        print("\n\n  ⏹  關閉伺服器... Shutting down server...", flush=True)
        
        # Stop Telegram polling if running
        try:
            if _tg_thread and _tg_thread.is_alive():
                from modules.telegram_bot import stop_polling
                stop_polling()
                _tg_thread.join(timeout=1)
        except:
            pass
        
        # Signal all jobs to cancel
        try:
            with _jobs_lock:
                for jid in list(_cancel_events.keys()):
                    _cancel_events[jid].set()
        except:
            pass
        
        # Signal shutdown
        _shutdown_event.set()
        
        # Give threads a moment to clean up
        time.sleep(0.1)
        
        # Force exit - this will kill daemon threads
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    
    # Signal handler - will be called on Ctrl+C (SIGINT) or termination (SIGTERM)
    def _signal_handler(signum, frame):
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n  📡 收到信號 Received {signal_name}", flush=True)
        _cleanup_and_exit(0)
    
    # Disable buffering for immediate output
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True
    )
    
    # Register signal handlers BEFORE starting the server
    # This can be called multiple times - last registration wins
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    # Try to set wakeup FD to ensure signals are processed
    try:
        import io
        if hasattr(signal, 'set_wakeup_fd'):
            signal.set_wakeup_fd(sys.stderr.fileno())
    except (AttributeError, ValueError):
        # Not available or already set
        pass
    
    # Auto-open browser
    def _open_browser():
        try:
            time.sleep(2)
            webbrowser.open("http://localhost:5000")
        except:
            pass
    
    browser_thread = threading.Thread(target=_open_browser, daemon=True)
    browser_thread.start()
    
    # Run Flask server in a separate thread
    def _run_flask():
        try:
            app.run(
                debug=False,
                host="127.0.0.1",
                port=5000,
                threaded=True,
                use_reloader=False,
                use_debugger=False
            )
        except Exception as e:
            print(f"❌ Flask server error: {e}", flush=True)
    
    # Start Flask in a daemon thread
    _flask_thread = threading.Thread(target=_run_flask, daemon=True)
    _flask_thread.start()
    
    print(" * Serving Flask app 'app'")
    print(" * Debug mode: off")
    
    # ── Start Telegram Bot Polling (if enabled) ──────────────────────────────
    if C.TG_ENABLED:
        try:
            from modules.telegram_bot import start_polling
            _tg_thread = threading.Thread(target=start_polling, daemon=True)
            _tg_thread.start()
            _tg_enabled = True
            print(" * Telegram Bot Polling: ON")
        except Exception as e:
            print(f" ⚠️  Telegram Bot 啟動失敗: {e}")
            _tg_enabled = False
    
    # Main thread: wait for signals
    # Keep the main thread alive to receive signals
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _cleanup_and_exit(0)
    except Exception as e:
        print(f"\n\n  ❌ 錯誤 Error: {e}\n", flush=True)
        _cleanup_and_exit(1)
