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

# ── last scan persistence ─────────────────────────────────────────────────────
_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "last_scan.json"

def _save_last_scan(rows: list):
    try:
        data = {"saved_at": datetime.now().isoformat(),
                "count": len(rows), "rows": rows}
        _LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        # Also save dated CSV
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


def _get_job(jid: str) -> dict:
    """Return a copy of the job dict, merging live screener progress if still pending."""
    with _jobs_lock:
        job = dict(_jobs.get(jid, {"status": "not_found"}))
    if job.get("status") == "pending":
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
    
    if isinstance(obj, bool):       # Must check bool before int (bool is subclass of int)
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
            if cleaned_v is not None or v is None:  # Keep None values if original was None
                cleaned[k] = cleaned_v
        return cleaned
    
    if isinstance(obj, (list, tuple)):
        return [_clean(i) for i in obj]
    
    # Handle numpy types
    if hasattr(obj, "item"):        # numpy scalar
        try:
            val = obj.item()
            return _clean(val)  # Recursively clean the extracted value
        except (TypeError, ValueError, AttributeError):
            return None
    
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
    p = ROOT / C.DATA_DIR / "watchlist.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"A": {}, "B": {}, "C": {}}


def _load_positions() -> dict:
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
            results = run_scan(refresh_rs=refresh_rs)
            if results is not None and hasattr(results, "to_dict"):
                rows = _clean(results.where(results.notna(), other=None)
                               .to_dict(orient="records"))
            elif results is not None:
                rows = _clean(list(results))
            else:
                rows = []
            _save_last_scan(rows)
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
    return jsonify(_load_last_scan())


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

    def _run():
        try:
            from modules.stock_analyzer import analyze
            r = analyze(ticker, account_size=acct, print_report=False)
            if r is None:
                _finish_job(jid, error=f"No data returned for {ticker}")
                return
            _finish_job(jid, result=_clean(r))
        except Exception as exc:
            logging.exception(f"Analyze thread error for {ticker}")
            _finish_job(jid, error=str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


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

    def _run():
        try:
            from modules.market_env import assess
            result = assess(verbose=False)
            _finish_job(jid, result=_clean(result))
        except Exception as exc:
            _finish_job(jid, error=str(exc))

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
    data   = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    try:
        from modules.position_monitor import add_position
        add_position(
            ticker,
            float(data["buy_price"]),
            int(data["shares"]),
            float(data["stop_loss"]),
            float(data["target"]) if data.get("target") else None,
            str(data.get("note", "")),
        )
        return jsonify({"ok": True, "positions": _load_positions()})
    except Exception as exc:
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
    """Gracefully restart the Flask development server."""
    def _restart():
        import time
        time.sleep(1)  # Give response time to reach client
        os.execv(sys.executable, [sys.executable, str(ROOT / "app.py")])
    
    try:
        threading.Thread(target=_restart, daemon=False).start()
        return jsonify({"ok": True, "message": "Server restarting..."}), 200
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
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
