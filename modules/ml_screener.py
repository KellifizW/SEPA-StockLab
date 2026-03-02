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

# ─────────────────────────────────────────────────────────────────────────────
# Progress tracking  (same pattern as qm_screener.py, for web UI polling)
# ─────────────────────────────────────────────────────────────────────────────
_ml_scan_lock   = threading.Lock()
_ml_progress    = {"stage": "idle", "pct": 0, "msg": "", "ticker": ""}
_ml_cancel_flag: Optional[threading.Event] = None


def _progress(stage: str, pct: int, msg: str = "", ticker: str = "") -> None:
    """Update the shared ML scan progress dict (polled by app.py)."""
    with _ml_scan_lock:
        _ml_progress.update({"stage": stage, "pct": pct, "msg": msg, "ticker": ticker})
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
# Stage 1 — Coarse filter (reuses same universe as QM)
# ─────────────────────────────────────────────────────────────────────────────

def run_ml_stage1(min_price: float = None,
                  min_avg_vol: int = None,
                  verbose: bool = True,
                  stage1_source: str | None = None) -> list[str]:
    """
    Stage 1 — Coarse filter. Returns a list of candidate ticker strings.

    Martin Luk's universe:
      • Price ≥ $5 (avoid penny stocks)
      • Avg volume ≥ 300K shares (liquidity)
      • No fundamental filter — pure technical system
    """
    from modules.data_pipeline import get_universe

    min_price = min_price or getattr(C, "ML_MIN_PRICE", 5.0)
    min_avg_vol = min_avg_vol or getattr(C, "ML_MIN_AVG_VOLUME", 300_000)
    stage1_source = (stage1_source or getattr(C, "STAGE1_SOURCE", "finviz")).lower()

    # ── NASDAQ FTP route ───────────────────────────────────────────────────
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

    def _progress_cb(batch_num: int, total_batches: int, msg: str = ""):
        pct = 12 + int((batch_num / total_batches) * 35)
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

    # ── Preliminary star rating ───────────────────────────────────────────
    star = getattr(C, "ML_STAR_BASE", 2.5)

    # Dim A: EMA Structure (0.20 weight)
    if all_stacked and all_rising:
        star += 1.0
    elif all_stacked or all_rising:
        star += 0.5
    elif slope_direction in ("rising", "rising_fast"):
        star += 0.25

    # Dim B: Pullback Quality (0.20 weight)
    if pb_quality == "ideal" and not too_extended:
        star += 1.0
    elif pb_quality == "acceptable" and not too_extended:
        star += 0.5
    elif too_extended:
        star -= 0.5
    elif pb_quality == "broken":
        star -= 1.0

    # Dim C: AVWAP Confluence (0.15 weight)
    if above_avwap_high and above_avwap_low:
        star += 0.5   # Above both supply and support AVWAPs
    elif above_avwap_low:
        star += 0.25  # Above support AVWAP at least

    # Dim D: Volume Pattern (0.15 weight)
    dry_up_thresh = getattr(C, "ML_VOLUME_DRY_UP_RATIO", 0.50)
    surge_mult = getattr(C, "ML_VOLUME_SURGE_MULT", 1.5)
    if dry_ratio < dry_up_thresh:
        star += 0.25  # Volume dry-up during pullback (bullish)
    if vol_ratio >= surge_mult:
        star += 0.25  # Today's volume surge (confirmation)

    # Dim E: Risk/Reward
    # Approximate stop distance (ATR-based)
    if "ATR_14" in df.columns:
        atr = float(df["ATR_14"].iloc[-1])
        if atr > 0 and close_price > 0:
            stop_pct = (atr / close_price) * 100.0
            max_stop = getattr(C, "ML_MAX_STOP_LOSS_PCT", 2.5)
            if stop_pct <= max_stop:
                star += 0.25
            elif stop_pct > max_stop * 1.5:
                star -= 0.25

    # Dim F: Relative Strength (placeholder — uses momentum as proxy)
    mom_3m = row.get("mom_3m", 0) or 0
    mom_6m = row.get("mom_6m", 0) or 0
    if mom_3m >= 50 and mom_6m >= 100:
        star += 0.25
    elif mom_3m >= 30:
        star += 0.15

    # Clamp to [0, ML_STAR_MAX]
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
    _progress("Stage 3", 65, f"Scoring {total} candidates with ML pullback analysis…")
    logger.info("[ML Stage3] Scoring %d candidates", total)

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
                top_n: Optional[int] = None, stage1_source: Optional[str] = None
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full 3-stage Martin Luk pullback scan.

    Parameters
    ----------
    verbose      : if True, print progress to console
    min_star     : minimum star rating to include (default from config)
    top_n        : limit results to top N (default from config)
    stage1_source: "nasdaq_ftp" or "finviz"

    Returns:
        (df_passed, df_all) where:
            df_passed — stocks with ml_star ≥ min_star (sorted best→worst)
            df_all    — all Stage-3 evaluated rows
    """
    scan_start = datetime.now()
    _progress("Initialising", 0, "Starting Martin Luk Pullback Scan…")
    logger.info("═" * 65)
    logger.info("  MARTIN LUK PULLBACK SCAN — %s", scan_start.strftime("%Y-%m-%d %H:%M"))
    logger.info("═" * 65)

    # ── Market environment gate ───────────────────────────────────────────
    if getattr(C, "ML_BLOCK_IN_BEAR", True):
        try:
            from modules.market_env import assess as mkt_assess
            mkt = mkt_assess(verbose=False)
            regime = mkt.get("regime", "")
            if regime == "DOWNTREND":
                logger.warning("[ML Scan] Market regime: %s — ML entries blocked", regime)
                _progress("Blocked", 100, f"熊市阻擋 Bear market block ({regime})")
                return pd.DataFrame(), pd.DataFrame()
        except Exception as exc:
            logger.warning("[ML Scan] Could not check market environment: %s", exc)

    # ── Stage 1 ───────────────────────────────────────────────────────────
    candidates = run_ml_stage1(verbose=verbose, stage1_source=stage1_source)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not candidates:
        _progress("Error", 100, "Stage 1 未找到候選股票")
        return pd.DataFrame(), pd.DataFrame()
    logger.info("[ML Scan] Stage1: %d candidates", len(candidates))

    # ── Stage 2 ───────────────────────────────────────────────────────────
    s2_rows = run_ml_stage2(candidates, verbose=verbose)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not s2_rows:
        _progress("Done", 100, "沒有候選股票通過 ADR/EMA/動量過濾")
        return pd.DataFrame(), pd.DataFrame()
    logger.info("[ML Scan] Stage2: %d candidates", len(s2_rows))

    # ── Stage 3 ───────────────────────────────────────────────────────────
    df_all = run_ml_stage3(s2_rows)

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

    _progress("Done", 100, f"找到 {len(df_passed)} 隻符合條件的股票 (≥{min_star_val}{_STAR})")
    return df_passed, df_all
