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

# Force UTF-8 stdout/stderr on Windows (avoids cp950 encode errors for ✓ ✗ → etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, jsonify, redirect, url_for

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trader_config as C

# ── logging ──────────────────────────────────────────────────────────────────
import math as _math
_LOG_DIR = ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)
# Global root logger (for framework/general events)
_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
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

# Per-scan file logging (attached during api_scan_run)
_scan_file_handlers: dict = {}  # {job_id: FileHandler}
_scan_log_paths: dict = {}      # {job_id: Path} — for post-scan notification

app = Flask(__name__)
app.secret_key = "minervini-sepa-2026"


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory job store  (scan / market are slow — run in background thread)
# ═══════════════════════════════════════════════════════════════════════════════

_jobs: dict = {}          # { job_id: {"status": "pending|done|error", "result": ...} }
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

def _save_last_scan(rows: list, all_rows: list = None):
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


def _finish_job(jid: str, result=None, error: str = None, log_file: str = ""):
    with _jobs_lock:
        _jobs[jid]["status"] = "done" if error is None else "error"
        _jobs[jid]["result"] = result
        _jobs[jid]["error"]  = error
        _jobs[jid]["finished"] = datetime.now().isoformat()
        _jobs[jid]["log_file"] = log_file


_market_job_ids: set = set()  # Track which job IDs are market assessments


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
        else:
            try:
                from modules.screener import get_scan_progress
                job["progress"] = get_scan_progress()
            except Exception:
                pass
    return job


def _clean(obj):
    """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
    if obj is None:
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
            cleaned_v = _clean(v)
            if cleaned_v is not None or v is None:
                cleaned[k] = cleaned_v
        return cleaned
    
    if isinstance(obj, (list, tuple)):
        return [_clean(i) for i in obj]
    
    # Handle pandas DataFrame or Series
    if hasattr(obj, "empty"):
        try:
            if obj.empty:
                return []
            if hasattr(obj, "to_dict"):   # DataFrame
                return [_clean(row) for row in obj.to_dict(orient="records")]
            if hasattr(obj, "tolist"):    # Series
                return [_clean(v) for v in obj.tolist()]
            return []
        except Exception:
            return None
    
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
    guide_path = ROOT / "GUIDE.md"
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "Guide not found."
    return render_template("guide.html", content=content)


# ═══════════════════════════════════════════════════════════════════════════════
# API – Scan
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/scan/run", methods=["POST"])
def api_scan_run():
    data        = request.get_json(silent=True) or {}
    refresh_rs  = data.get("refresh_rs", False)
    jid         = _new_job()

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
            scan_result = run_scan(refresh_rs=refresh_rs)
            # run_scan returns (df_passed, df_all) tuple
            if isinstance(scan_result, tuple):
                df_passed, df_all = scan_result
            else:
                df_passed = scan_result
                df_all = scan_result

            def _to_rows(df):
                if df is not None and hasattr(df, "to_dict") and not df.empty:
                    return _clean(df.where(df.notna(), other=None).to_dict(orient="records"))
                elif df is not None and not hasattr(df, "to_dict"):
                    return _clean(list(df))
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
    return jsonify({"job_id": jid})


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
    return jsonify(_get_job(jid))


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
            ln(f"  Pivot:      ${float(scored.get('pivot')):.2f}")

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
        if hasattr(news_raw, "to_dict"):
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
    def _restart():
        import time
        # Give the current request time to complete and response to reach client
        time.sleep(1.5)
        # Start new process in background, detached (so it survives when parent dies)
        # Windows: use creationflags to detach the process
        # Unix: use preexec_fn=os.setsid (but on Windows this doesn't apply)
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, str(ROOT / "app.py")],
            cwd=str(ROOT),
            creationflags=creationflags,
            start_new_session=(sys.platform != 'win32')  # Unix: detach via new session
        )
        # Now terminate this process to release the port
        time.sleep(0.2)
        os._exit(0)  # Force exit (bypasses cleanup, but needed for clean restart)
    
    try:
        threading.Thread(target=_restart, daemon=False).start()
        return jsonify({"ok": True, "message": "Server restarting..."}), 200
    except Exception as exc:
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
            df_pd.index = df_pd.index.date
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
        rsi_pts, bbl_pts, bbm_pts, bbu_pts = [], [], [], []
        atr_pts, bbw_pts, vol_ratio_pts = [], [], []

        # Historical signal tracking (for learning / validation overlays)
        atr_raw = pd.to_numeric(df.get("ATR_14"), errors="coerce") if "ATR_14" in df.columns else None
        if atr_raw is None:
            atr_raw = pd.to_numeric(df.get("ATRr_14"), errors="coerce") if "ATRr_14" in df.columns else None
        bbu_raw = pd.to_numeric(df.get("BBU_20_2.0"), errors="coerce") if "BBU_20_2.0" in df.columns else None
        bbl_raw = pd.to_numeric(df.get("BBL_20_2.0"), errors="coerce") if "BBL_20_2.0" in df.columns else None
        bbm_raw = pd.to_numeric(df.get("BBM_20_2.0"), errors="coerce") if "BBM_20_2.0" in df.columns else None
        vol_raw = pd.to_numeric(df.get("Volume"), errors="coerce") if "Volume" in df.columns else None

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
            "rsi": rsi_pts,
            "bbl": bbl_pts, "bbm": bbm_pts, "bbu": bbu_pts,
            "atr14": atr_pts,
            "bb_width_pct": bbw_pts,
            "vol_ratio_50d": vol_ratio_pts,
            "vcp_signal_events": vcp_signal_events,
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
    
    # Auto-open browser after a short delay
    def _open_browser():
        import time
        time.sleep(1.5)  # Wait for Flask server to start
        webbrowser.open("http://localhost:5000")
    
    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
