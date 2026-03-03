"""
modules/ml_screener.py  —  Martin Luk Pullback Swing Trading Scanner
══════════════════════════════════════════════════════════════════════
Implements Martin Luk's 3-stage pullback screening funnel:

  Stage 1 (Coarse)  — finvizfinance / NASDAQ FTP: price, volume, basic momentum
  Stage 2 (ML Gate) — ADR + dollar volume + EMA structure + momentum confirmation
  Stage 3 (Quality) — EMA alignment, pullback depth, AVWAP, volume pattern, scoring

Core philosophy: Pullback buying on rising EMA structure.
95% technical — no fundamental filtering.
EMA (not SMA) based; AVWAP as key S/R indicator.

All market data goes exclusively through data_pipeline.py.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ── ANSI colours for terminal output ──────────────────────────────────────────
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"
_STAR   = "⭐"

# ── Universe cache file (shared by all 3 ML scan modes) ───────────────────────
_UNIVERSE_CACHE_FILE = ROOT / "data" / "ml_universe_cache.json"

# ─────────────────────────────────────────────────────────────────────────────
# Progress tracking  (same pattern as qm_screener.py, for web UI polling)
# ─────────────────────────────────────────────────────────────────────────────
_ml_scan_lock   = threading.Lock()
_ml_progress    = {"stage": "idle", "pct": 0, "msg": "", "ticker": "", "log_lines": []}
_ml_log_lines   = []  # Keep running log of all messages
_ml_cancel_flag: Optional[threading.Event] = None


def _progress(stage: str, pct: int, msg: str = "", ticker: str = "") -> None:
    """Update the shared ML scan progress dict (polled by app.py)."""
    import datetime
    with _ml_scan_lock:
        # Build complete log line with timestamp
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if ticker:
            log_line = f"[{ts}] [{stage}] {ticker}: {msg}"
        else:
            log_line = f"[{ts}] [{stage}] {msg}"
        
        # Append to running log (keep last 200 lines to avoid unbounded growth)
        _ml_log_lines.append(log_line)
        if len(_ml_log_lines) > 200:
            _ml_log_lines.pop(0)
        
        # Update progress dict with recent log lines (last 50) for UI
        _ml_progress.update({
            "stage": stage,
            "pct": pct,
            "msg": msg,
            "ticker": ticker,
            "log_lines": list(_ml_log_lines[-50:])  # Last 50 lines for UI
        })
    logger.debug("[ML Progress] %s %d%% — %s %s", stage, pct, ticker, msg)


def get_ml_scan_progress() -> dict:
    """Return current ML scan progress snapshot (thread-safe)."""
    with _ml_scan_lock:
        return dict(_ml_progress)


def set_ml_scan_cancel(ev: threading.Event) -> None:
    """Register a cancel event (set by app.py cancel endpoint)."""
    global _ml_cancel_flag
    _ml_cancel_flag = ev


def _is_cancelled() -> bool:
    return _ml_cancel_flag is not None and _ml_cancel_flag.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# Universe cache helpers  (shared across standard / triple / combined modes)
# ─────────────────────────────────────────────────────────────────────────────

def _save_universe_cache(tickers: list[str], source: str) -> None:
    """Persist the Stage-1 ticker list to disk for reuse across modes."""
    import json as _json
    try:
        payload = {
            "tickers":  tickers,
            "count":    len(tickers),
            "source":   source,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        _UNIVERSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _UNIVERSE_CACHE_FILE.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.info("[ML Universe Cache] Saved %d tickers → %s", len(tickers), _UNIVERSE_CACHE_FILE.name)
    except Exception as exc:
        logger.warning("[ML Universe Cache] Save failed: %s", exc)


def _load_universe_cache() -> dict | None:
    """Load cached ticker list; returns None if file is absent or corrupted."""
    import json as _json
    try:
        if _UNIVERSE_CACHE_FILE.exists():
            data = _json.loads(_UNIVERSE_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("tickers"), list) and data["tickers"]:
                return data
    except Exception as exc:
        logger.warning("[ML Universe Cache] Load failed: %s", exc)
    return None


def get_ml_universe_cache_info() -> dict:
    """
    Public API — returns metadata about the stored universe cache.
    Used by the UI to display last cache timestamp and ticker count.

    Returns dict with keys: exists, saved_at, count, source, age_minutes.
    """
    data = _load_universe_cache()
    if data is None:
        return {"exists": False, "saved_at": None, "count": 0, "source": None, "age_minutes": None}
    saved_at_str = data.get("saved_at", "")
    age_minutes: float | None = None
    try:
        from datetime import timezone
        saved_dt = datetime.fromisoformat(saved_at_str)
        age_minutes = round((datetime.now() - saved_dt).total_seconds() / 60, 1)
    except Exception:
        pass
    return {
        "exists":      True,
        "saved_at":    saved_at_str,
        "count":       data.get("count", len(data.get("tickers", []))),
        "source":      data.get("source", "unknown"),
        "age_minutes": age_minutes,
    }

def run_ml_stage1(min_price: float = None,
                  min_avg_vol: int = None,
                  verbose: bool = True,
                  stage1_source: str | None = None,
                  use_cache: bool = False) -> list[str]:
    """
    Stage 1 — Coarse filter. Returns a list of candidate ticker strings.

    Martin Luk's universe:
      • Price ≥ $5 (avoid penny stocks)
      • Avg volume ≥ 300K shares (liquidity)
      • No fundamental filter — pure technical system

    Parameters
    ----------
    use_cache : bool
        If True, load the previously saved ticker list from
        data/ml_universe_cache.json instead of hitting finviz/NASDAQ FTP.
        All three ML scan modes (standard, triple, combined) share the
        same cache file so a single fresh download covers every mode.
    """
    from modules.data_pipeline import get_universe

    min_price = min_price or getattr(C, "ML_MIN_PRICE", 5.0)
    min_avg_vol = min_avg_vol or getattr(C, "ML_MIN_AVG_VOLUME", 300_000)
    stage1_source = (stage1_source or getattr(C, "STAGE1_SOURCE", "finviz")).lower()

    # ── Fast-path: return cached universe ─────────────────────────────────
    if use_cache:
        cached = _load_universe_cache()
        if cached:
            tickers = cached["tickers"]
            saved_at = cached.get("saved_at", "")
            _progress("Stage 1", 10,
                      f"✓ 使用快取股票清單 ({len(tickers):,} 個, 儲存於 {saved_at}) Use cached universe")
            logger.info("[ML Stage1] Using cached universe: %d tickers saved at %s", len(tickers), saved_at)
            return tickers
        else:
            _progress("Stage 1", 2, "⚠ 快取不存在，將重新下載 Cache not found, downloading fresh…")
            logger.warning("[ML Stage1] use_cache=True but no cache found; downloading fresh data")

    if stage1_source == "nasdaq_ftp":
        _progress("Stage 1", 2, "連接 NASDAQ FTP 中… Connecting to NASDAQ FTP…")
        logger.info("[ML Stage1] Source: NASDAQ FTP (price>%.0f, vol>%d)", min_price, min_avg_vol)
        try:
            from modules.nasdaq_universe import get_universe_nasdaq
            t0 = time.time()
            _progress("Stage 1", 3, "下載 NASDAQ 股票清單… Downloading NASDAQ ticker list…")
            df_nasdaq = get_universe_nasdaq(price_min=min_price, vol_min=float(min_avg_vol))
            elapsed = time.time() - t0
            if df_nasdaq is not None and not df_nasdaq.empty and "Ticker" in df_nasdaq.columns:
                tickers = [str(t).strip().upper() for t in df_nasdaq["Ticker"].dropna()
                           if str(t).strip() and 1 <= len(str(t).strip()) <= 5
                           and str(t).strip().replace("-", "").isalpha()]
                _progress("Stage 1", 10, f"✓ Stage 1 完成 (NASDAQ FTP): {len(tickers):,} 個候選股票")
                logger.info("[ML Stage1] ✓ NASDAQ FTP: %d candidates in %.1fs", len(tickers), elapsed)
                _save_universe_cache(tickers, source="nasdaq_ftp")
                return tickers
            else:
                logger.warning("[ML Stage1] NASDAQ FTP returned empty, falling back to finvizfinance")
        except Exception as exc:
            logger.error("[ML Stage1] NASDAQ FTP failed (%s), falling back to finvizfinance", exc)

    # ── finvizfinance route ────────────────────────────────────────────────
    _progress("Stage 1", 2, "連接 finvizfinance 中… Connecting to finvizfinance…")
    logger.info("[ML Stage1] Starting coarse filter")

    filters = {
        "Price": f"Over ${int(min_price)}",
        "Average Volume": "Over 300K",
        "Country": "USA",
    }

    tickers = []
    final_df = pd.DataFrame()

    # Try multiple views with fallback
    for attempt, (view, desc) in enumerate([
        ("Performance", "Performance 數據層"),
        ("Overview", "Overview 視圖"),
    ], start=1):
        if not final_df.empty:
            break
        try:
            pct_start = 2 + attempt * 2
            _progress("Stage 1", pct_start, f"嘗試 {desc}… Attempting {view} view…")
            t0 = time.time()
            df = get_universe(filters, view=view, verbose=False)
            elapsed = time.time() - t0
            if df is not None and not df.empty:
                final_df = df
                _progress("Stage 1", 8, f"✓ {desc}: {len(df):,} 股票 ({elapsed:.1f}s)")
                logger.info("[ML Stage1] %s view: %d rows in %.1fs", view, len(df), elapsed)
        except Exception as exc:
            logger.warning("[ML Stage1] %s view failed: %s", view, str(exc)[:200])

    # Extract tickers
    if not final_df.empty:
        for col in ("Ticker", "ticker", "Symbol"):
            if col in final_df.columns:
                tickers = [str(t).strip().upper() for t in final_df[col].dropna().tolist()
                           if str(t).strip() and str(t).strip() != "nan"]
                break
        else:
            if final_df.index.name in ("Ticker", "Symbol"):
                tickers = [str(t).strip().upper() for t in final_df.index.tolist()]

    # OTC filter
    if tickers:
        try:
            from modules.nasdaq_universe import filter_otc
            before = len(tickers)
            tickers = filter_otc(tickers)
            removed = before - len(tickers)
            if removed:
                _progress("Stage 1", 9, f"移除 {removed} 個 OTC 股票")
        except Exception:
            pass

    if tickers:
        _progress("Stage 1", 10, f"✓ Stage 1 完成: {len(tickers):,} 個候選股票")
        logger.info("[ML Stage1] ✓ %d raw candidates", len(tickers))
        _save_universe_cache(tickers, source=stage1_source)
    else:
        _progress("Stage 1", 10, "⚠ Stage 1 未找到候選股票")
        logger.warning("[ML Stage1] No candidates found")

    return tickers


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — ADR + EMA structure + momentum gate
# ─────────────────────────────────────────────────────────────────────────────

def _check_ml_stage2(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    Per-ticker Stage 2 ML gate.
    Returns a dict of metrics if the stock passes; None if vetoed.

    Martin Luk's filters:
      1. ADR gate — hard veto if ADR < ML_MIN_ADR_PCT
      2. Dollar volume — skip if < ML_MIN_DOLLAR_VOLUME
      3. EMA structure — at least EMA21 and EMA50 must be present and rising
      4. Momentum — must satisfy at least one window (3M or 6M)
      5. Price must be above EMA50 (not in breakdown)
    """
    from modules.data_pipeline import (
        get_adr, get_dollar_volume, get_momentum_returns, get_ema_alignment
    )

    if df.empty or len(df) < 50:
        return None

    # ── 1. ADR veto ───────────────────────────────────────────────────────
    adr = get_adr(df)
    min_adr = getattr(C, "ML_MIN_ADR_PCT", 4.0)
    if adr < min_adr:
        logger.debug("[ML S2 VETO] %s ADR=%.1f%% < %.1f%%", ticker, adr, min_adr)
        return None

    # ── 2. Dollar volume gate ─────────────────────────────────────────────
    dv = get_dollar_volume(df)
    min_dv = getattr(C, "ML_MIN_DOLLAR_VOLUME", 5_000_000)
    if dv < min_dv:
        logger.debug("[ML S2 VETO] %s $Vol=%.0f < %.0f", ticker, dv, min_dv)
        return None

    # ── 3. EMA structure check ────────────────────────────────────────────
    ema_align = get_ema_alignment(df)
    # Must have at least EMA21 rising (Martin's primary EMA)
    ema21_rising = ema_align.get("ema_21_rising", False)
    ema50_rising = ema_align.get("ema_50_rising", False)
    price_above_all = ema_align.get("price_above_all", False)

    # Price must be above EMA50 (not in breakdown)
    ema50_val = ema_align.get("ema_50")
    close = float(df["Close"].iloc[-1])
    if ema50_val and close < ema50_val:
        logger.debug("[ML S2 VETO] %s below EMA50", ticker)
        return None

    # At least EMA21 OR EMA50 must be rising
    if not (ema21_rising or ema50_rising):
        logger.debug("[ML S2 VETO] %s no rising EMAs", ticker)
        return None

    # ── 3b. Weekly EMA veto (Chapter 12 — "Trust weekly over daily") ─────
    # Resample daily df to weekly and check W-EMA10 vs W-EMA40
    if getattr(C, "ML_WEEKLY_VETO_ENABLED", True):
        try:
            from modules.ml_setup_detector import compute_weekly_trend
            df_weekly = df.resample("W").agg({
                "Open": "first", "High": "max",
                "Low": "min", "Close": "last", "Volume": "sum",
            }).dropna()
            wt = compute_weekly_trend(df_weekly)
            if wt.get("is_veto"):
                logger.debug("[ML S2 WEEKLY VETO] %s weekly downtrend (W-EMA10<W-EMA40)", ticker)
                return None
        except Exception as exc:
            logger.debug("[ML S2] Weekly check failed for %s: %s", ticker, exc)

    # ── 4. Momentum filter ────────────────────────────────────────────────
    mom = get_momentum_returns(df)
    m3 = mom.get("3m")
    m6 = mom.get("6m")
    min3 = getattr(C, "ML_MOMENTUM_3M_MIN_PCT", 30.0)
    min6 = getattr(C, "ML_MOMENTUM_6M_MIN_PCT", 80.0)

    passes_3m = m3 is not None and m3 >= min3
    passes_6m = m6 is not None and m6 >= min6

    if not (passes_3m or passes_6m):
        logger.debug("[ML S2 VETO] %s momentum 3M=%.1f 6M=%.1f", ticker, m3 or 0, m6 or 0)
        return None

    return {
        "ticker":           ticker,
        "close":            round(close, 2),
        "adr":              round(adr, 2),
        "dollar_volume_m":  round(dv / 1_000_000, 2),
        "mom_3m":           round(m3, 1) if m3 is not None else None,
        "mom_6m":           round(m6, 1) if m6 is not None else None,
        "passes_3m":        passes_3m,
        "passes_6m":        passes_6m,
        "ema_stacked":      ema_align.get("all_stacked", False),
        "ema_all_rising":   ema_align.get("all_rising", False),
    }


def run_ml_stage2(tickers: list[str], verbose: bool = True,
                  enriched_map: dict = None) -> list[dict]:
    """
    Stage 2 — Download OHLCV and apply ML gate to each candidate.
    Returns list of passing ticker dicts for Stage 3.
    """
    from modules.data_pipeline import batch_download_and_enrich

    total = len(tickers)
    _progress("Stage 2", 12, f"下載 {total} 個股票歷史數據… Downloading history…")
    logger.info("[ML Stage2] Processing %d candidates", total)

    batch_size = getattr(C, "ML_STAGE2_BATCH_SIZE", 60)
    max_workers = getattr(C, "ML_STAGE2_MAX_WORKERS", 12)

    # Monotonic progress: prevent regression when batch_download_and_enrich
    # switches from cache phase (scale 0–1) to download phase (scale 1–N).
    _s2_max_pct = [12]
    def _progress_cb(batch_num: int, total_batches: int, msg: str = ""):
        pct = max(_s2_max_pct[0], min(47, 12 + int((batch_num / max(total_batches, 1)) * 35)))
        _s2_max_pct[0] = pct
        _progress("Stage 2", pct, msg)

    if enriched_map is None:
        enriched = batch_download_and_enrich(
            tickers,
            period="1y",  # 1 year for EMA150 + momentum calculation
            progress_cb=_progress_cb,
        )
    else:
        enriched = enriched_map
        logger.info("[ML Stage2] Using pre-downloaded enriched_map (%d records)", len(enriched))

    _progress("Stage 2", 48, "應用 ML EMA/ADR/動量過濾… Applying ML filters…")
    passed = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_check_ml_stage2, tkr, df_data): tkr
            for tkr, df_data in enriched.items()
        }
        done = 0
        for fut in as_completed(futures):
            if _is_cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            tkr = futures[fut]
            done += 1
            try:
                result = fut.result()
                if result is not None:
                    passed.append(result)
                    logger.info("[ML S2 PASS] %s ADR=%.1f%% $Vol=$%.1fM",
                                tkr, result["adr"], result["dollar_volume_m"])
            except Exception as exc:
                logger.warning("[ML S2 ERROR] %s: %s", tkr, exc)

            pct = 48 + int(done / max(total, 1) * 12)
            _progress("Stage 2", min(pct, 60), f"已檢查 {done}/{total}…", tkr)

    _progress("Stage 2", 62, f"{len(passed)} 通過 ADR/EMA/動量過濾")
    logger.info("[ML Stage2] %d / %d passed", len(passed), total)
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — EMA alignment + Pullback + AVWAP + Volume scoring
# ─────────────────────────────────────────────────────────────────────────────

def _score_ml_stage3(row: dict, df: pd.DataFrame) -> dict | None:
    """
    Apply full ML quality scoring for Stage 3.
    Augments the Stage 2 dict with pullback quality, AVWAP confluence,
    volume pattern, and a preliminary star rating for sorting.
    """
    from modules.data_pipeline import (
        get_ema_alignment, get_pullback_depth, get_avwap_from_swing_high,
        get_avwap_from_swing_low, get_ema_slope
    )
    from modules.ml_setup_detector import detect_setup_type

    ticker = row["ticker"]
    if df.empty or len(df) < 50:
        return None

    # ── Setup type detection ───────────────────────────────────────────────
    setup_info = detect_setup_type(df, ticker)
    setup_type = setup_info.get("primary_setup", "UNKNOWN")
    setup_conf = setup_info.get("confidence", 0.0)

    # ── EMA alignment (detailed) ──────────────────────────────────────────
    ema = get_ema_alignment(df)
    all_stacked = ema.get("all_stacked", False)
    all_rising = ema.get("all_rising", False)
    pullback_to = ema.get("pullback_to_ema")

    # ── Pullback depth ────────────────────────────────────────────────────
    pb = get_pullback_depth(df)
    pb_quality = pb.get("pullback_quality", "unknown")
    nearest_ema = pb.get("nearest_ema")
    too_extended = pb.get("too_extended", False)

    # ── AVWAP confluence ──────────────────────────────────────────────────
    avwap_high = get_avwap_from_swing_high(df)
    avwap_low = get_avwap_from_swing_low(df)
    above_avwap_high = avwap_high.get("above_avwap", False)
    above_avwap_low = avwap_low.get("above_avwap", False)

    # ── EMA slope (21 EMA is Martin's primary) ───────────────────────────
    slope_21 = get_ema_slope(df, period=21)
    slope_direction = slope_21.get("direction", "unknown")

    # ── Volume analysis ───────────────────────────────────────────────────
    close_price = float(df["Close"].iloc[-1])
    avg_vol_20 = float(df["Volume"].tail(20).mean()) if len(df) >= 20 else 0
    latest_vol = float(df["Volume"].iloc[-1])
    vol_ratio = latest_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

    # Volume dry-up check on recent 10 bars
    dry_ratio = 1.0
    if len(df) >= 30:
        baseline = float(df["Volume"].tail(30).iloc[:-10].mean())
        recent_vol = float(df["Volume"].tail(10).mean())
        dry_ratio = recent_vol / baseline if baseline > 0 else 1.0

    # ── Consistent star rating via ml_analyzer.compute_star_rating() ────────
    # Builds a 7-dimension dim_scores dict and delegates to the same function
    # used by the single-stock analyzer — guarantees screener ↔ analyzer parity.
    star = getattr(C, "ML_STAR_BASE", 2.5)
    try:
        from modules.ml_analyzer import compute_star_rating as _csr
        from modules.ml_setup_detector import (
            detect_higher_lows, count_support_confluence,
            compute_weekly_trend,
        )

        # Dim A — EMA structure + optional weekly veto
        _dim_a_score = 0.0
        _dim_a_veto = False
        if all_stacked and all_rising:
            _dim_a_score = 2.0
        elif all_stacked or all_rising:
            _dim_a_score = 1.0
        elif slope_direction in ("rising", "rising_fast"):
            _dim_a_score = 0.5
        if getattr(C, "ML_WEEKLY_VETO_ENABLED", True):
            try:
                _df_w = df.resample("W").agg({
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna()
                _wt = compute_weekly_trend(_df_w)
                if _wt.get("is_veto"):
                    _dim_a_veto = True
            except Exception:
                pass

        # Dim B — Pullback quality + higher lows
        _dim_b_score = 0.0
        if pb_quality == "ideal" and not too_extended:
            _dim_b_score = 2.0
        elif pb_quality == "acceptable" and not too_extended:
            _dim_b_score = 1.0
        elif too_extended:
            _dim_b_score = -1.0
        elif pb_quality == "broken":
            _dim_b_score = -2.0
        _hl = detect_higher_lows(df, lookback=getattr(C, "ML_HIGHER_LOW_LOOKBACK", 20))
        _dim_b_score = max(-2.0, min(2.0, _dim_b_score + _hl.get("adjustment", 0.0)))

        # Dim C — Support Confluence (AVWAP + EMA/prior levels)
        _dim_c_score = 0.0
        if above_avwap_high and above_avwap_low:
            _dim_c_score = 1.5
        elif above_avwap_low:
            _dim_c_score = 0.5
        _cf = count_support_confluence(df, close_price,
                                       getattr(C, "ML_CONFLUENCE_RADIUS_PCT", 1.5))
        _dim_c_score = max(-2.0, min(2.5, _dim_c_score + _cf.get("adjustment", 0.0)))

        # Dim D — Volume pattern (dry-up + surge)
        _dim_d_score = 0.0
        if dry_ratio < getattr(C, "ML_VOLUME_DRY_UP_RATIO", 0.50):
            _dim_d_score += 1.0
        if vol_ratio >= getattr(C, "ML_VOLUME_SURGE_MULT", 1.5):
            _dim_d_score += 1.0

        # Dim E — Risk/Reward (ATR-based stop estimate)
        _dim_e_score = 0.0
        _dim_e_veto = False
        if "ATR_14" in df.columns:
            _atr = float(df["ATR_14"].iloc[-1])
            _max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
            if _atr > 0 and close_price > 0:
                _stop_pct = (_atr / close_price) * 100.0
                if _stop_pct <= _max_stop:
                    _dim_e_score = 1.0
                elif _stop_pct > _max_stop * 2:
                    _dim_e_score = -2.0
                    _dim_e_veto = True
                else:
                    _dim_e_score = -1.0

        # Dim F — Relative Strength (3M/6M momentum proxy)
        _dim_f_score = 0.0
        mom_3m = row.get("mom_3m", 0) or 0
        mom_6m = row.get("mom_6m", 0) or 0
        if mom_3m >= 50 and mom_6m >= 100:
            _dim_f_score = 2.0
        elif mom_3m >= 30:
            _dim_f_score = 1.0

        # Dim G — Market Environment gate
        _dim_g_score = 0.0
        _dim_g_veto = False
        _mkt_env = row.get("market_env", "")
        if _mkt_env == "CONFIRMED_UPTREND":
            _dim_g_score = 1.0
        elif _mkt_env == "UPTREND_UNDER_PRESSURE":
            _dim_g_score = 0.0
        elif _mkt_env == "CHOPPY":
            _dim_g_score = -0.5
        elif _mkt_env in ("MARKET_IN_CORRECTION", "DOWNTREND"):
            _dim_g_score = -2.0
            _dim_g_veto = True

        _dim_scores = {
            "A": {"score": _dim_a_score, "is_veto": _dim_a_veto},
            "B": {"score": _dim_b_score},
            "C": {"score": _dim_c_score},
            "D": {"score": _dim_d_score},
            "E": {"score": _dim_e_score, "is_veto": _dim_e_veto},
            "F": {"score": _dim_f_score},
            "G": {"score": _dim_g_score, "is_veto": _dim_g_veto},
        }
        _star_result = _csr(_dim_scores)
        if _star_result.get("veto"):
            # Hard veto — drop this stock (already passed weekly veto in S2;
            # this catches late risk/market vetoes discovered during S3 data load)
            logger.debug("[ML S3 VETO] %s vetoed by %s in Stage 3",
                         ticker, _star_result.get("veto"))
            return None
        star = _star_result.get("capped_stars", star)
    except Exception as exc:
        logger.debug("[ML S3 star] %s using fallback star calc: %s", ticker, exc)
        # Fallback: preserve previous inline logic
        star = getattr(C, "ML_STAR_BASE", 2.5)
        if all_stacked and all_rising:
            star += 1.0
        elif all_stacked or all_rising:
            star += 0.5
        if pb_quality == "ideal" and not too_extended:
            star += 1.0
        elif too_extended:
            star -= 0.5
        if above_avwap_low:
            star += 0.25
        star_max = getattr(C, "ML_STAR_MAX", 5.0)
        star = max(0.0, min(star, star_max))

    # ── Build output dict ─────────────────────────────────────────────────
    row_out = dict(row)  # copy Stage 2 fields
    row_out.update({
        "ml_star":          round(star, 2),
        "setup_type":       setup_type,
        "setup_conf":       round(setup_conf, 2),
        "ema_stacked":      all_stacked,
        "ema_all_rising":   all_rising,
        "pullback_to_ema":  pullback_to,
        "pullback_quality": pb_quality,
        "nearest_ema":      nearest_ema,
        "too_extended":     too_extended,
        "above_avwap_high": above_avwap_high,
        "above_avwap_low":  above_avwap_low,
        "ema21_slope_dir":  slope_direction,
        "vol_ratio":        round(vol_ratio, 2),
        "vol_dry_ratio":    round(dry_ratio, 3),
    })
    return row_out


def run_ml_stage3(s2_rows: list[dict]) -> pd.DataFrame:
    """
    Stage 3 — Apply ML pullback quality scoring to all Stage 2 candidates.
    Returns a DataFrame with all scored rows.
    """
    from modules.data_pipeline import get_enriched

    total = len(s2_rows)
    logger.debug("[ML Stage3 DEBUG] Starting Stage 3 with %d candidates", total)
    _progress("Stage 3", 65, f"Scoring {total} candidates with ML pullback analysis…")
    logger.info("[ML Stage3] Scoring %d candidates", total)
    logger.debug("[ML Stage3 DEBUG] _progress and logger calls completed")

    scored = []
    for i, row in enumerate(s2_rows):
        if _is_cancelled():
            break
        ticker = row["ticker"]
        pct = 65 + int((i / max(total, 1)) * 30)
        _progress("Stage 3", min(pct, 95), f"Scoring {ticker}…", ticker)

        try:
            df = get_enriched(ticker, period="1y", use_cache=True)
            if df is None or df.empty:
                continue
            result = _score_ml_stage3(row, df)
            if result is not None:
                scored.append(result)
        except Exception as exc:
            logger.warning("[ML S3 ERROR] %s: %s", ticker, exc)

    if not scored:
        return pd.DataFrame()

    df_out = pd.DataFrame(scored)
    if "ml_star" in df_out.columns:
        df_out = df_out.sort_values("ml_star", ascending=False)
    return df_out


# ─────────────────────────────────────────────────────────────────────────────
# CSV auto-save
# ─────────────────────────────────────────────────────────────────────────────

def _save_scan_results_csv(df_passed: pd.DataFrame, df_all: pd.DataFrame,
                           scan_start: datetime) -> None:
    """Save scan results to scan_results/ for debugging."""
    out_dir = ROOT / "scan_results"
    out_dir.mkdir(exist_ok=True)
    date_str = scan_start.strftime("%Y-%m-%d_%H%M")
    keep = getattr(C, "ML_SCAN_RESULTS_KEEP", 30)

    for label, df_data in [("ml_passed", df_passed), ("ml_all", df_all)]:
        fpath = out_dir / f"{label}_{date_str}.csv"
        if not df_data.empty:
            df_data.to_csv(fpath, index=False)
            logger.info("[ML Scan] Saved %s (%d rows)", fpath.name, len(df_data))
        else:
            fpath.write_text("# No data\n")

        # Rotate old files
        existing = sorted(out_dir.glob(f"{label}_*.csv"), reverse=True)
        for old in existing[keep:]:
            try:
                old.unlink()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_ml_scan(verbose: bool = True, min_star: Optional[float] = None,
                top_n: Optional[int] = None, stage1_source: Optional[str] = None,
                scanner_mode: str = "triple",  # kept for API compat; always runs triple
                use_universe_cache: bool = False,
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full 3-stage Martin Luk pullback scan.

    Parameters
    ----------
    verbose        : if True, print progress to console
    min_star       : minimum star rating to include (default from config)
    top_n          : limit results to top N (default from config)
    stage1_source  : "nasdaq_ftp" or "finviz"
    scanner_mode   : kept for API backward-compat. Triple scanner always runs.
    use_universe_cache : if True, Stage 1 loads tickers from the last saved
                     cache file (data/ml_universe_cache.json) instead of
                     hitting finviz / NASDAQ FTP.

    Returns:
        (df_passed, df_all) where:
            df_passed — stocks with ml_star ≥ min_star (sorted best→worst)
            df_all    — all Stage-3 evaluated rows
    """
    scan_start = datetime.now()
    _progress("Initialising", 0, "Starting Martin Luk Pullback Scan…")
    logger.info("═" * 65)
    logger.info("  MARTIN LUK PULLBACK SCAN [TRIPLE] — %s",
                scan_start.strftime("%Y-%m-%d %H:%M"))
    logger.info("═" * 65)

    # ── Market environment gate ───────────────────────────────────────────
    market_env_label = ""
    if getattr(C, "ML_BLOCK_IN_BEAR", True):
        try:
            from modules.market_env import assess as mkt_assess
            mkt = mkt_assess(verbose=False)
            regime = mkt.get("regime", "")
            market_env_label = regime
            if regime == "DOWNTREND":
                logger.warning("[ML Scan] Market regime: %s — ML entries blocked", regime)
                _progress("Blocked", 100, f"熊市阻擋 Bear market block ({regime})")
                return pd.DataFrame(), pd.DataFrame()
        except Exception as exc:
            logger.warning("[ML Scan] Could not check market environment: %s", exc)

    # ── Stage 1 ───────────────────────────────────────────────────────────
    candidates = run_ml_stage1(verbose=verbose, stage1_source=stage1_source,
                               use_cache=use_universe_cache)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not candidates:
        _progress("Error", 100, "Stage 1 未找到候選股票")
        return pd.DataFrame(), pd.DataFrame()
    # Normalise to list[dict] — run_ml_stage1 returns list[str]
    if candidates and isinstance(candidates[0], str):
        candidates = [{"ticker": t, "channel": ""} for t in candidates]
    logger.info("[ML Scan] Stage1: %d candidates", len(candidates))

    # ── Triple-scanner channel pre-scan ──────────────────────────────────
    # Always runs: identifies GAP/GAINER/LEADER channels and annotates candidates.
    if getattr(C, "ML_TRIPLE_SCANNER_ENABLED", True):
        try:
            from modules.ml_scanner_channels import run_triple_scan
            _progress("Triple Scan", 37, "Running Martin Luk 3-channel scanner…")
            s1_tickers = [r["ticker"] for r in candidates]
            triple = run_triple_scan(s1_tickers, include_gaps=True,
                                     include_gainers=True, include_leaders=True)
            # Display detailed summary from triple scan result
            summary = triple.get("summary", {})
            gap_cnt = summary.get("gap_count", 0)
            gainer_cnt = summary.get("gainer_count", 0)
            leader_cnt = summary.get("leader_count", 0)
            total_unique = summary.get("total_unique", 0)
            _progress("Triple Scan", 43,
                      f"✓ 完成: {gap_cnt} 缺口 + {gainer_cnt} 漲幅 + {leader_cnt} 領導 = {total_unique} 獨特 | "
                      f"Gap: {gap_cnt}, Gainers: {gainer_cnt}, Leaders: {leader_cnt}")
            logger.info("[ML TripleScan] Display: %d gap + %d gainers + %d leaders = %d unique",
                        gap_cnt, gainer_cnt, leader_cnt, total_unique)
            # Merge channel tickers not already in candidates
            existing = {r["ticker"] for r in candidates}
            added = 0
            for item in triple.get("merged", []):
                if item["ticker"] not in existing:
                    # Inject as minimal Stage 1 candidate —
                    # Stage 2 will validate full EMA/ADR criteria
                    candidates.append({
                        "ticker":     item["ticker"],
                        "channel":    item.get("channel", "TRIPLE"),
                        "price":      item.get("price", 0),
                        "source":     f"triple_{item.get('channel','?').lower()}",
                    })
                    existing.add(item["ticker"])
                    added += 1
                else:
                    # Annotate existing candidate with channel badge
                    for c in candidates:
                        if c["ticker"] == item["ticker"]:
                            c["channel"] = item.get("channel", c.get("channel", ""))
                            break
            logger.info("[ML TripleScan] Injected %d additional tickers (%d merged total)",
                        added, len(triple.get("merged", [])))
        except Exception as exc:
            logger.warning("[ML Scan] Triple scanner failed (continuing with standard): %s", exc)

    # ── Stage 2 ───────────────────────────────────────────────────────────
    s2_input = len(candidates)
    s2_rows = run_ml_stage2([r["ticker"] for r in candidates], verbose=verbose)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not s2_rows:
        _progress("Done", 100, "沒有候選股票通過 ADR/EMA/動量過濾")
        return pd.DataFrame(), pd.DataFrame()
    # Display Stage 2 filter results
    _progress("Stage 2", 50,
              f"✓ 通過篩選: {len(s2_rows)}/{s2_input} ({len(s2_rows)*100//s2_input}%) | "
              f"已排除: {s2_input - len(s2_rows)} 檔")
    logger.info("[ML Scan] Stage2 filter: %d/%d passed (%.0f%%) | excluded: %d",
                len(s2_rows), s2_input, len(s2_rows)*100/s2_input if s2_input > 0 else 0, s2_input - len(s2_rows))
    # Propagate channel badge into Stage 2 rows
    ticker_to_channel = {r["ticker"]: r.get("channel", "") for r in candidates}
    for row in s2_rows:
        if not row.get("channel"):
            row["channel"] = ticker_to_channel.get(row["ticker"], "")
    # Attach market env label to all S2 rows (used by Stage 3 Dim G)
    for row in s2_rows:
        row.setdefault("market_env", market_env_label)
    logger.info("[ML Scan] Stage2: %d candidates", len(s2_rows))

    # ── Stage 3 ───────────────────────────────────────────────────────────
    logger.debug("[ML Scan] About to call run_ml_stage3 with %d candidates", len(s2_rows))
    try:
        df_all = run_ml_stage3(s2_rows)
        logger.debug("[ML Scan] run_ml_stage3 returned %d results", len(df_all) if not df_all.empty else 0)
    except Exception as e:
        logger.error("[ML Scan] STAGE 3 CRASHED: %s", e, exc_info=True)
        raise

    if df_all.empty:
        _progress("Done", 100, "沒有候選股票通過質量評分")
        return pd.DataFrame(), df_all

    # Separate passed vs all
    min_star_val = min_star if min_star is not None else getattr(C, "ML_SCAN_MIN_STAR", 2.5)
    top_n_val = top_n if top_n is not None else getattr(C, "ML_SCAN_TOP_N", 40)

    df_passed = df_all[df_all["ml_star"] >= min_star_val].copy()
    df_passed = df_passed.sort_values("ml_star", ascending=False)

    if top_n_val and len(df_passed) > top_n_val:
        df_passed = df_passed.head(top_n_val)

    # Display final results
    _progress("Stage 3", 95,
              f"✓ Stage 3 完成: {len(df_passed)}/{len(df_all)} ({len(df_passed)*100//len(df_all)}%) ⭐ ≥{min_star_val} | "
              f"顯示: {len(df_passed)} 檔 (Top {top_n_val})")
    logger.info("[ML Scan] Stage3 results: %d/%d passed quality (%.0f%%) | displaying %d (top %d)",
                len(df_passed), len(df_all), len(df_passed)*100/len(df_all) if len(df_all) > 0 else 0,
                len(df_passed), top_n_val or len(df_passed))

    elapsed = (datetime.now() - scan_start).total_seconds()
    logger.info("[ML Scan] FINAL: %d passed | %d scored total | %.0fs elapsed",
                len(df_passed), len(df_all), elapsed)

    # ── Auto-save CSV ─────────────────────────────────────────────────────
    try:
        _save_scan_results_csv(df_passed, df_all, scan_start)
    except Exception as exc:
        logger.warning("[ML Scan] CSV auto-save failed: %s", exc)

    if verbose:
        print()
        print(f"{_BOLD}{'═'*65}{_RESET}")
        print(f"{_BOLD}  ML PULLBACK SCAN RESULTS  — {date.today().isoformat()}{_RESET}")
        print(f"{'═'*65}")
        print(f"  Stage 1: {len(candidates):>4} candidates")
        print(f"  Stage 2: {len(s2_rows):>4} passed ADR/EMA/momentum gate")
        print(f"  Stage 3: {len(df_passed):>4} with star rating ≥ {min_star_val}{_STAR}")
        print(f"  Elapsed: {elapsed:.0f}s")
        print()

        if not df_passed.empty:
            cols_show = ["ticker", "ml_star", "close", "adr", "dollar_volume_m",
                         "setup_type", "pullback_quality", "ema_stacked",
                         "ema_all_rising", "above_avwap_low"]
            cols_show = [c for c in cols_show if c in df_passed.columns]
            print(df_passed[cols_show].to_string(index=False))

    _progress("Done", 100,
              f"✅ 掃描完成: {len(df_passed)} 隻符合條件 (≥{min_star_val}⭐) | "
              f"Stage1→{len(candidates)} | Stage2→{len(s2_rows)} | Stage3→{len(df_passed)} | "
              f"耗時 {elapsed:.0f}s")
    return df_passed, df_all
