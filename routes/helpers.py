"""
routes.helpers — Shared state, job management and utility functions.

Every blueprint imports helpers it needs from here so that mutable state
(job dicts, caches, locks) lives in a single authoritative location.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from flask import render_template, make_response

import sys, os

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_DIR = ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# In-memory job store  (scan / market / analyze are slow — run in background)
# ═══════════════════════════════════════════════════════════════════════════════

_jobs: dict = {}                  # { jid: {"status": ..., "result": ..., "error": ...} }
_jobs_lock = threading.Lock()
_cancel_events: dict = {}         # { jid: threading.Event }

_qm_analyze_cache: dict = {}      # ticker -> clean result dict
_ml_analyze_cache: dict = {}      # ticker -> clean result dict

_market_job_ids: set = set()
_qm_job_ids: set = set()
_ml_job_ids: set = set()

_scan_file_handlers: dict = {}    # { jid: FileHandler }
_scan_log_paths: dict = {}        # { jid: Path }

# ── API response cache ────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str):
    """Get cached response if still fresh (within TTL)."""
    import time
    with _cache_lock:
        if key in _cache:
            age = time.time() - _cache[key]["time"]
            if age < _CACHE_TTL:
                return _cache[key]["data"]
            else:
                del _cache[key]
    return None


def _set_cache(key: str, data):
    """Cache a response."""
    import time
    with _cache_lock:
        _cache[key] = {"data": data, "time": time.time()}


# ═══════════════════════════════════════════════════════════════════════════════
# Job lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

def _new_job() -> str:
    jid = str(uuid.uuid4())[:8]
    ev = threading.Event()
    with _jobs_lock:
        _jobs[jid] = {
            "status": "pending", "result": None, "error": None,
            "started": datetime.now().isoformat(),
            "progress": {"stage": "Initialising…", "pct": 0, "msg": "", "ticker": ""},
        }
        _cancel_events[jid] = ev
    return jid


def _get_cancel(jid: str) -> threading.Event:
    return _cancel_events.get(jid, threading.Event())


def _finish_job(jid: str, result: Any = None, error: Optional[str] = None,
                log_file: str = ""):
    if result is not None:
        try:
            result = _sanitize_for_json(result)
        except Exception as e:
            logging.error(f"[FINISH_JOB {jid}] Error sanitizing result: {e}", exc_info=True)
            result = {"error": "Result sanitization failed: " + str(e)}

    with _jobs_lock:
        _jobs[jid]["status"] = "done" if error is None else "error"
        _jobs[jid]["result"] = result
        _jobs[jid]["error"] = error
        _jobs[jid]["finished"] = datetime.now().isoformat()
        _jobs[jid]["log_file"] = log_file


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


# ═══════════════════════════════════════════════════════════════════════════════
# JSON sanitization
# ═══════════════════════════════════════════════════════════════════════════════

def _sanitize_for_json(obj, depth: int = 0, max_depth: int = 5):
    """Recursively sanitize to ensure JSON-serializable."""
    import numpy as np

    if depth > max_depth:
        return None
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, (float, np.floating)):
        if pd.isna(obj) or np.isnan(obj):
            return None
        if np.isinf(obj):
            return str(obj)
        return float(obj)
    if isinstance(obj, (np.integer, np.bool_)):
        return obj.item()
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    if isinstance(obj, list):
        return [_sanitize_for_json(item, depth + 1, max_depth) for item in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_sanitize_for_json(item, depth + 1, max_depth) for item in obj]
    return str(obj)


def _clean(obj):
    """Recursively convert numpy/NaN/DataFrame values to JSON-safe Python types."""
    if obj is None:
        return None
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return None
    # numpy scalar extraction
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        try:
            val = obj.item()
            return _clean(val)
        except (TypeError, ValueError, AttributeError):
            return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str)):
        if isinstance(obj, float):
            try:
                if obj != obj:  # NaN
                    return None
            except (TypeError, ValueError):
                pass
        return obj
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if isinstance(v, (pd.DataFrame, pd.Series)):
                continue
            cleaned_v = _clean(v)
            if cleaned_v is not None or v is None:
                cleaned[k] = cleaned_v
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_clean(i) for i in obj]
    try:
        return str(obj)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR NAV caching
# ═══════════════════════════════════════════════════════════════════════════════

_NAV_CACHE_FILE = ROOT / C.DATA_DIR / "ibkr_nav_cache.json"
_nav_lock = threading.Lock()


def _detect_ibkr_account_currency() -> str:
    if not C.IBKR_ENABLED:
        return C.DEFAULT_CURRENCY
    try:
        from modules import ibkr_client
        status = ibkr_client.get_status()
        if status.get("connected") and "account_currency" in status:
            return status["account_currency"]
    except Exception:
        pass
    return C.DEFAULT_CURRENCY


def _save_nav_cache(nav: float, buying_power: float = 0,
                    account: str = "", currency: Optional[str] = None):
    try:
        with _nav_lock:
            if currency is None:
                currency = _detect_ibkr_account_currency()
            cache_data = {
                "nav": nav, "buying_power": buying_power,
                "account": account, "account_currency": currency,
                "last_sync": datetime.now().isoformat(),
                "formatted_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _NAV_CACHE_FILE.write_text(
                json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save NAV cache: {e}")


def _load_nav_cache() -> dict:
    try:
        with _nav_lock:
            if _NAV_CACHE_FILE.exists():
                return json.loads(_NAV_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to load NAV cache: {e}")
    return {}


def _get_account_size() -> tuple:
    """Returns (nav, last_sync_time, sync_status)."""
    if C.IBKR_ENABLED:
        try:
            from modules import ibkr_client
            status = ibkr_client.get_status()
            if status.get("connected") and status.get("nav", 0) > 0:
                nav = float(status["nav"])
                buying_power = float(status.get("buying_power", 0))
                account = status.get("account", "")
                currency = (status.get("account_currency") or C.ACCOUNT_BASE_CURRENCY)
                _save_nav_cache(nav, buying_power, account, currency)
                return nav, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "LIVE"
        except Exception as e:
            logger.warning(f"Failed to get live IBKR NAV: {e}")

    cached = _load_nav_cache()
    if cached and cached.get("nav", 0) > 0:
        return float(cached["nav"]), cached.get("formatted_time", "Unknown"), "CACHED"

    return C.ACCOUNT_SIZE, "", "DEFAULT"


# ═══════════════════════════════════════════════════════════════════════════════
# Currency settings
# ═══════════════════════════════════════════════════════════════════════════════

_CURRENCY_SETTINGS_FILE = ROOT / C.DATA_DIR / "currency_settings.json"
_currency_lock = threading.Lock()


def _save_currency_setting(currency: str, usd_hkd_rate: Optional[float] = None):
    try:
        with _currency_lock:
            effective_rate = (
                float(usd_hkd_rate) if (usd_hkd_rate and usd_hkd_rate > 0)
                else float(C.USD_TO_HKD_RATE)
            )
            settings = {
                "currency": currency.upper(),
                "usd_hkd_rate": effective_rate,
                "last_updated": datetime.now().isoformat(),
            }
            _CURRENCY_SETTINGS_FILE.write_text(
                json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save currency setting: {e}")


def _load_currency_setting() -> tuple:
    try:
        with _currency_lock:
            if _CURRENCY_SETTINGS_FILE.exists():
                data = json.loads(
                    _CURRENCY_SETTINGS_FILE.read_text(encoding="utf-8"))
                currency = data.get("currency", C.DEFAULT_CURRENCY)
                rate = data.get("usd_hkd_rate") or C.USD_TO_HKD_RATE
                return currency, float(rate)
    except Exception as e:
        logger.warning(f"Failed to load currency setting: {e}")
    return C.DEFAULT_CURRENCY, float(C.USD_TO_HKD_RATE)


def _get_account_base_currency() -> str:
    config_currency = C.ACCOUNT_BASE_CURRENCY.upper()
    if config_currency != "USD":
        return config_currency
    try:
        cached = _load_nav_cache()
        cached_currency = cached.get("account_currency", "").upper()
        if cached_currency and cached_currency != "USD":
            return cached_currency
    except Exception:
        pass
    return "USD"


def _convert_amount(amount: float, target_currency: Optional[str] = None) -> tuple:
    """Returns (converted_amount, currency_symbol, display_format)."""
    if target_currency is None:
        target_currency, rate = _load_currency_setting()
    else:
        _, rate = _load_currency_setting()
    target_currency = (target_currency or "USD").upper()
    account_base = _get_account_base_currency()

    if account_base == "HKD":
        amount_usd = amount / rate
    else:
        amount_usd = amount

    if target_currency == "HKD":
        return amount_usd * rate, "HK$", f"HK${amount_usd * rate:,.2f}"
    return amount_usd, "$", f"${amount_usd:,.2f}"


# ═══════════════════════════════════════════════════════════════════════════════
# Last-scan persistence (SEPA / QM / ML / Combined)
# ═══════════════════════════════════════════════════════════════════════════════

_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "last_scan.json"
_QM_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "qm_last_scan.json"
_ML_LAST_SCAN_FILE = ROOT / C.DATA_DIR / "ml_last_scan.json"
_COMBINED_LAST_FILE = ROOT / C.DATA_DIR / "combined_last_scan.json"


def _save_last_scan(rows: list, all_rows: Optional[list] = None):
    try:
        data = {
            "saved_at": datetime.now().isoformat(),
            "count": len(rows), "rows": rows,
            "all_scored_count": len(all_rows) if all_rows else len(rows),
            "all_scored": all_rows or [],
        }
        _LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        import csv, io
        if rows:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=[
                k for k in rows[0] if not isinstance(rows[0][k], dict)])
            writer.writeheader()
            for r in rows:
                writer.writerow({k: v for k, v in r.items()
                                 if not isinstance(v, dict)})
            csv_path = ROOT / C.DATA_DIR / f"sepa_scan_{date.today().isoformat()}.csv"
            csv_path.write_text(buf.getvalue(), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save last_scan: {exc}")

    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_scan_history
            all_to_save = (all_rows or []) + rows
            seen: set = set()
            deduped = [r for r in all_to_save
                       if r.get("ticker") not in seen and not seen.add(r.get("ticker"))]  # type: ignore[func-returns-value]
            append_scan_history(deduped)
            with _cache_lock:
                _cache.pop("watchlist-history", None)
        except Exception as exc:
            logging.warning(f"DB scan_history write skipped: {exc}")


def _load_last_scan() -> dict:
    try:
        if _LAST_SCAN_FILE.exists():
            return json.loads(_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_qm_last_scan(rows: list, all_rows: Optional[list] = None):
    try:
        data = {
            "saved_at": datetime.now().isoformat(),
            "count": len(rows), "rows": rows,
            "all_scored_count": len(all_rows) if all_rows else len(rows),
            "all_scored": all_rows or [],
        }
        _QM_LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save qm_last_scan: {exc}")

    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_qm_scan_history
            all_to_save = (all_rows or []) + rows
            seen: set = set()
            deduped = [r for r in all_to_save
                       if r.get("ticker") not in seen and not seen.add(r.get("ticker"))]  # type: ignore[func-returns-value]
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


def _save_ml_last_scan(rows: list, all_rows: Optional[list] = None,
                       triple_summary: Optional[dict] = None):
    try:
        data = {
            "saved_at": datetime.now().isoformat(),
            "count": len(rows), "rows": rows,
            "all_scored_count": len(all_rows) if all_rows else len(rows),
            "all_scored": all_rows or [],
        }
        if triple_summary:
            data["triple_summary"] = triple_summary
        _ML_LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        logging.warning(f"Could not save ml_last_scan: {exc}")

    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_ml_scan_history
            all_to_save = (all_rows or []) + rows
            seen: set = set()
            deduped = [r for r in all_to_save
                       if r.get("ticker") not in seen and not seen.add(r.get("ticker"))]  # type: ignore[func-returns-value]
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


def _save_combined_last(sepa_rows, qm_rows, market_env, timing,
                        sepa_csv="", qm_csv=""):
    try:
        _default_star = 4.0
        qm_count_4star = sum(
            1 for r in qm_rows
            if float(r.get("qm_star") or r.get("stars") or 0) >= _default_star
        )
        data = {
            "saved_at": datetime.now().isoformat(),
            "sepa_count": len(sepa_rows),
            "qm_count": len(qm_rows),
            "qm_count_4star": qm_count_4star,
            "sepa_rows": sepa_rows[:20],
            "qm_rows": qm_rows[:20],
            "market_env": market_env,
            "timing": timing,
            "sepa_csv": sepa_csv,
            "qm_csv": qm_csv,
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


def _save_combined_scan_csv(sepa_df, qm_df, scan_ts=None) -> tuple:
    from datetime import datetime as _dt
    sepa_path = qm_path = ""
    try:
        out_dir = ROOT / "scan_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts_str = (scan_ts or _dt.now()).strftime("%Y%m%d_%H%M%S")
        keep = getattr(C, "QM_SCAN_RESULTS_KEEP", 30)
        for label, df in [("combined_sepa", sepa_df), ("combined_qm", qm_df)]:
            fpath = out_dir / f"{label}_{ts_str}.csv"
            if df is not None and hasattr(df, "to_csv") and not df.empty:
                df.to_csv(fpath, index=False)
            else:
                fpath.write_text("(no results)\n", encoding="utf-8")
            if label == "combined_sepa":
                sepa_path = f"scan_results/{fpath.name}"
            else:
                qm_path = f"scan_results/{fpath.name}"
            existing = sorted(out_dir.glob(f"{label}_*.csv"), reverse=True)
            for old in existing[keep:]:
                try:
                    old.unlink()
                except Exception:
                    pass
    except Exception as exc:
        logging.warning("Could not save combined scan CSVs: %s", exc)
    return sepa_path, qm_path


# ═══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ═══════════════════════════════════════════════════════════════════════════════

def _load_watchlist() -> dict:
    try:
        from modules.watchlist import _load
        return _load()
    except Exception as exc:
        logger.warning("Failed to load watchlist from modules: %s", exc)
        p = ROOT / C.DATA_DIR / "watchlist.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"A": {}, "B": {}, "C": {}}


def _load_positions() -> dict:
    try:
        from modules.position_monitor import _load
        data = _load()
        return data.get("positions", {})
    except Exception as exc:
        logger.warning("Failed to load positions from modules: %s", exc)
        p = ROOT / C.DATA_DIR / "positions.json"
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                return raw.get("positions", {})
            except Exception:
                pass
    return {}


def _latest_report() -> Optional[Path]:
    reports = sorted(
        Path(ROOT / C.REPORTS_DIR).glob("sepa_report_*.html"),
        key=lambda f: f.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


# ═══════════════════════════════════════════════════════════════════════════════
# DataFrame → row list conversion (used by scan routes)
# ═══════════════════════════════════════════════════════════════════════════════

def df_to_rows(df, label: str = "") -> list:
    """Safely convert a pandas DataFrame to a list of clean dicts."""
    if df is None or not hasattr(df, "to_dict"):
        return []
    if hasattr(df, "empty") and df.empty:
        return []
    try:
        records = []
        for _, row in df.iterrows():
            record = {}
            for col, val in row.items():
                if isinstance(val, (pd.DataFrame, pd.Series, dict, list)):
                    continue
                record[col] = None if pd.isna(val) else val
            records.append(record)
        return _clean(records)  # type: ignore[return-value]
    except Exception as e:
        logging.error(f"[df_to_rows] {label} conversion failed: {e}", exc_info=True)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Watchlist HTMX helper
# ═══════════════════════════════════════════════════════════════════════════════

def htmx_wl_rows(wl: dict, trigger_msg: Optional[str] = None):
    """Build HTMX response with OOB badge-count spans + tbody rows."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram bot state  (shared between settings_api and entry point)
# ═══════════════════════════════════════════════════════════════════════════════

_tg_enabled: bool = getattr(C, "TG_ENABLED", False)
_tg_thread = None  # will be set in app.py __main__


# ═══════════════════════════════════════════════════════════════════════════════
# Backtest job stores
# ═══════════════════════════════════════════════════════════════════════════════

_bt_jobs: dict = {}
_bt_lock = threading.Lock()
_qm_bt_jobs: dict = {}
_qm_bt_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# QM / ML watch signal caches  (used by chart_api)
# ═══════════════════════════════════════════════════════════════════════════════

_qm_earnings_cache: dict = {}    # ticker -> {date, fetched_at}
_qm_nasdaq_cache: dict = {}      # "snapshot" -> {regime, sma_fast, sma_slow, fetched_at}
