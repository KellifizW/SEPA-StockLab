"""
modules/qm_screener.py  —  Qullamaggie Breakout Swing Trading Scanner
══════════════════════════════════════════════════════════════════════
Implements Kristjan Kullamägi's 3-stage breakout screening funnel:

  Stage 1 (Coarse)  — finvizfinance broad filter: price, volume, basic momentum
  Stage 2 (QM Gate) — ADR veto + dollar volume + 1M/3M/6M momentum confirmation
  Stage 3 (Quality) — Consolidation pattern, MA alignment, higher lows scoring

Author note: This system is PURE TECHNICAL — no fundamental filtering.
ADR has independent veto power; stocks with ADR < QM_MIN_ADR_PCT are always rejected.
Market environment gate: QM breakouts blocked in confirmed bear/downtrend.

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
# Progress tracking  (same pattern as screener.py, for web UI polling)
# ─────────────────────────────────────────────────────────────────────────────
_qm_scan_lock   = threading.Lock()
_qm_progress    = {"stage": "idle", "pct": 0, "msg": "", "ticker": ""}
_qm_cancel_flag: Optional[threading.Event] = None


def _progress(stage: str, pct: int, msg: str = "", ticker: str = "") -> None:
    """Update the shared QM scan progress dict (polled by app.py)."""
    with _qm_scan_lock:
        _qm_progress.update({"stage": stage, "pct": pct, "msg": msg, "ticker": ticker})
    logger.debug("[QM Progress] %s %d%% — %s %s", stage, pct, ticker, msg)


def get_qm_scan_progress() -> dict:
    """Return current QM scan progress snapshot (thread-safe)."""
    with _qm_scan_lock:
        return dict(_qm_progress)


def set_qm_scan_cancel(ev: threading.Event) -> None:
    """Register a cancel event (set by app.py cancel endpoint)."""
    global _qm_cancel_flag
    _qm_cancel_flag = ev


def _is_cancelled() -> bool:
    return _qm_cancel_flag is not None and _qm_cancel_flag.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Coarse filter via finvizfinance
# ─────────────────────────────────────────────────────────────────────────────

def run_qm_stage1(min_price: float = 5.0,
                  min_avg_vol: int = 300_000,
                  verbose: bool = True,
                  timeout_sec: float = 30.0,
                  stage1_source: str = None) -> list[str]:
    """
    Stage 1 — Coarse filter.  Returns a list of candidate ticker strings.

    Source is controlled by C.STAGE1_SOURCE:
      "nasdaq_ftp"  — Free NASDAQ FTP + yfinance price/vol filter (~2-4 min)  [recommended]
      "finviz"      — finvizfinance (slow, ~15 min full scan)

    Qullamaggie's universe:
      • Listed stocks only (no OTC)
      • Price ≥ $5 (liquidity floor)
      • Avg volume ≥ 300K shares (for position-building)
      • No explicit fundamental filter — this is a pure tech system
    """
    from modules.data_pipeline import get_universe
    import time as time_module

    stage1_source = (stage1_source or getattr(C, "STAGE1_SOURCE", "finviz")).lower()

    # ── NASDAQ FTP route ───────────────────────────────────────────────────
    if stage1_source == "nasdaq_ftp":
        _progress("Stage 1", 2, "連接 NASDAQ FTP 中… Connecting to NASDAQ FTP…")
        logger.info("[QM Stage1] Source: NASDAQ FTP + yfinance filter (price>%.0f, vol>%d)",
                    min_price, min_avg_vol)
        try:
            from modules.nasdaq_universe import get_universe_nasdaq
            t0 = time_module.time()
            _progress("Stage 1", 3, "下載 NASDAQ 股票清單… Downloading NASDAQ ticker list…")
            df_nasdaq = get_universe_nasdaq(price_min=min_price, vol_min=float(min_avg_vol))
            elapsed = time_module.time() - t0
            if df_nasdaq is not None and not df_nasdaq.empty and "Ticker" in df_nasdaq.columns:
                tickers = [str(t).strip().upper() for t in df_nasdaq["Ticker"].dropna()
                           if str(t).strip() and 1 <= len(str(t).strip()) <= 5
                           and str(t).strip().replace("-", "").isalpha()]
                _progress("Stage 1", 10, f"✓ Stage 1 完成 (NASDAQ FTP): {len(tickers):,} 個候選股票")
                logger.info("[QM Stage1] ✓ NASDAQ FTP: %d candidates in %.1fs", len(tickers), elapsed)
                return tickers
            else:
                logger.warning("[QM Stage1] NASDAQ FTP returned empty, falling back to finvizfinance")
        except Exception as exc:
            logger.error("[QM Stage1] NASDAQ FTP failed (%s), falling back to finvizfinance", exc)

    # ── finvizfinance route (default / fallback) ───────────────────────────
    _progress("Stage 1", 2, "連接 finvizfinance 中… Connecting to finvizfinance…")
    logger.info("[QM Stage1] Starting coarse filter with patient timeout")

    # ── Qullamaggie's universe (basic filters only via Finviz) ──
    filters = {
        "Price":   "Over $5",
        "Average Volume": "Over 300K",
        "Country": "USA",
    }

    # Try finvizfinance views with detailed progress reporting
    tickers = []
    final_df = pd.DataFrame()
    
    # ── Attempt 1: Performance view (richest data) ──
    try:
        _progress("Stage 1", 3, "嘗試 Performance 數據層 (包含漲跌幅)… Fetching Performance view (may take 2-10 minutes)…")
        logger.info("[QM Stage1] → Attempt 1: Performance view with full fundamentals")
        
        start_time = time_module.time()
        df = get_universe(filters, view="Performance", verbose=False)
        elapsed = time_module.time() - start_time
        
        logger.info("[QM Stage1] Performance view completed after %.1f seconds, got %d rows",
                   elapsed, len(df) if df is not None and not df.empty else 0)
        
        if df is not None and not df.empty:
            _progress("Stage 1", 5, f"✓ Performance view: {len(df):,} 股票於 {elapsed:.1f}s 內獲得")
            final_df = df
        else:
            logger.warning("[QM Stage1] Performance view returned 0 rows, trying Overview fallback…")
            _progress("Stage 1", 4, f"Performance 無結果 ({elapsed:.1f}s)，切換至 Overview…")

    except Exception as exc:
        elapsed = time_module.time() - start_time
        logger.warning("[QM Stage1] Performance view failed after %.1f seconds: %s",
                      elapsed, str(exc)[:200])
        _progress("Stage 1", 4, f"Performance 失敗 ({elapsed:.1f}s)，試 Overview…")

    # ── Attempt 2: Overview view (simpler, less data) ──
    if final_df.empty:
        try:
            _progress("Stage 1", 6, "嘗試 Overview 視圖 (基礎信息)… Attempting Overview view (0-45 seconds)…")
            logger.info("[QM Stage1] → Attempt 2: Overview view with price + volume only")
            
            start_time = time_module.time()
            df = get_universe({"Price": "Over $5", "Average Volume": "Over 300K"}, 
                            view="Overview", verbose=False)
            elapsed = time_module.time() - start_time
            
            logger.info("[QM Stage1] Overview view completed after %.1f seconds, got %d rows",
                       elapsed, len(df) if df is not None and not df.empty else 0)
            
            if df is not None and not df.empty:
                _progress("Stage 1", 7, f"✓ Overview: {len(df):,} 股票於 {elapsed:.1f}s 內獲得")
                final_df = df
            else:
                logger.warning("[QM Stage1] Overview view returned 0 rows, trying price-only…")
                _progress("Stage 1", 6, f"Overview 無結果 ({elapsed:.1f}s)，試最小過濾…")
                
        except Exception as exc:
            elapsed = time_module.time() - start_time
            logger.warning("[QM Stage1] Overview view failed after %.1f seconds: %s",
                          elapsed, str(exc)[:200])
            _progress("Stage 1", 6, f"Overview 失敗 ({elapsed:.1f}s)，試最小過濾…")

    # ── Attempt 3: Price-only filter (minimal, last resort) ──
    if final_df.empty:
        try:
            _progress("Stage 1", 7, "最小過濾（僅價格 > $5）… Minimal filter (price only, 0-45 seconds)…")
            logger.info("[QM Stage1] → Attempt 3: Price-only filter (absolute fallback)")
            
            start_time = time_module.time()
            df = get_universe({"Price": "Over $5"}, view="Overview", verbose=False)
            elapsed = time_module.time() - start_time
            
            logger.info("[QM Stage1] Price-only view completed after %.1f seconds, got %d rows",
                       elapsed, len(df) if df is not None and not df.empty else 0)
            
            if df is not None and not df.empty:
                _progress("Stage 1", 8, f"✓ 最小過濾: {len(df):,} 股票於 {elapsed:.1f}s 內獲得")
                final_df = df
            else:
                logger.error("[QM Stage1] All three finvizfinance attempts returned 0 rows")
                _progress("Stage 1", 8, f"⚠ 所有過濾層都無結果 ({elapsed:.1f}s)，Stage 1 返回空列表")
                
        except Exception as exc:
            elapsed = time_module.time() - start_time
            logger.error("[QM Stage1] Price-only filter failed after %.1f seconds: %s",
                        elapsed, str(exc)[:200])
            _progress("Stage 1", 8, f"最小過濾失敗 ({elapsed:.1f}s)，返回空")

    # Normalise ticker column from whichever view succeeded
    _progress("Stage 1", 8, f"正在處理... Processing {len(final_df)} rows…")
    
    if not final_df.empty:
        for col in ("Ticker", "ticker", "Symbol"):
            if col in final_df.columns:
                tickers = [str(t).strip().upper() for t in final_df[col].dropna().tolist()
                           if str(t).strip() and str(t).strip() != "nan"]
                logger.info("[QM Stage1] Extracted %d tickers from column '%s'", len(tickers), col)
                break
        else:
            if final_df.index.name in ("Ticker", "Symbol"):
                tickers = [str(t).strip().upper() for t in final_df.index.tolist()]
                logger.info("[QM Stage1] Extracted %d tickers from index", len(tickers))
            else:
                logger.warning("[QM Stage1] Could not find ticker column in any finvizfinance result")
                tickers = []
    else:
        logger.error("[QM Stage1] All fallback attempts exhausted, returning empty list")
        tickers = []

    # ── OTC filter — strip Pink-Sheet / unlisted stocks ──────────────────
    if tickers:
        try:
            from modules.nasdaq_universe import filter_otc
            before = len(tickers)
            tickers = filter_otc(tickers)
            removed = before - len(tickers)
            if removed:
                _progress("Stage 1", 9,
                          f"移除 {removed} 個 OTC/場外股票 Removed {removed} OTC/unlisted tickers")
        except Exception as exc:
            logger.warning("[QM Stage1] OTC filter skipped: %s", exc)

    # Final status message
    if tickers:
        _progress("Stage 1", 10, f"✓ Stage 1 完成: {len(tickers):,} 個候選股票準備進入 Stage 2")
        logger.info("[QM Stage1] ✓ ✓ ✓ SUCCESS: %d raw candidates ready for Stage 2", len(tickers))
    else:
        _progress("Stage 1", 10, "⚠ Stage 1 未找到任何符合條件的股票。請檢查市場或調整篩選條件。")
        logger.warning("[QM Stage1] ⚠ ⚠ ⚠ FAILURE: No candidates found from any finvizfinance view")

    return tickers


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — ADR veto + dollar volume + momentum confirmation
# ─────────────────────────────────────────────────────────────────────────────

def _check_qm_stage2(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    Per-ticker Stage 2 QM gate.
    Returns a dict of metrics if the stock passes; None if vetoed.

    Rules (in order):
      1. ADR gate — hard veto if ADR < QM_MIN_ADR_PCT (default 5%)
      2. Dollar volume — skip if avg $Vol < QM_SCAN_MIN_DOLLAR_VOL
      3. Momentum — must satisfy at least ONE of: 1M≥25% | 3M≥50% | 6M≥150%
      4. 6-day range proximity — price within ±15% of rolling 6-day high/low
    """
    from modules.data_pipeline import (
        get_adr, get_dollar_volume, get_momentum_returns, get_6day_range_proximity
    )

    if df.empty or len(df) < 30:
        return None

    # ── 1. ADR veto ───────────────────────────────────────────────────────
    adr = get_adr(df)
    min_adr = getattr(C, "QM_MIN_ADR_PCT", 5.0)
    if adr < min_adr:
        logger.debug("[QM S2 VETO] %s ADR=%.1f%% < %.1f%%", ticker, adr, min_adr)
        return None

    # ── 2. Dollar volume gate ─────────────────────────────────────────────
    dv = get_dollar_volume(df)
    min_dv = getattr(C, "QM_SCAN_MIN_DOLLAR_VOL", 5_000_000)
    if dv < min_dv:
        logger.debug("[QM S2 VETO] %s $Vol=%.0f < %.0f", ticker, dv, min_dv)
        return None

    # ── 3. Momentum filter (at least one window must pass) ─────────────
    mom = get_momentum_returns(df)
    m1, m3, m6 = mom.get("1m"), mom.get("3m"), mom.get("6m")
    min1 = getattr(C, "QM_MOMENTUM_1M_MIN_PCT", 25.0)
    min3 = getattr(C, "QM_MOMENTUM_3M_MIN_PCT", 50.0)
    min6 = getattr(C, "QM_MOMENTUM_6M_MIN_PCT", 150.0)

    passes_1m = m1 is not None and m1 >= min1
    passes_3m = m3 is not None and m3 >= min3
    passes_6m = m6 is not None and m6 >= min6

    if not (passes_1m or passes_3m or passes_6m):
        logger.debug("[QM S2 VETO] %s momentum 1M=%.1f 3M=%.1f 6M=%.1f",
                     ticker, m1 or 0, m3 or 0, m6 or 0)
        return None

    # ── 4. 6-day range proximity (consolidation proximity check) ──────────
    # Rule: Price MUST be within 15% of 6-day high AND 15% of 6-day low
    # (i.e., within the 6-day consolidation rectangle, not just touching one edge)
    # This filters out stocks that have had large intraday swings or recent drops.
    rng = get_6day_range_proximity(df)
    near_high = rng.get("near_high", False)
    near_low  = rng.get("near_low",  False)
    if not (near_high and near_low):
        logger.debug("[QM S2 VETO] %s not near 6-day consolidation (high=%s low=%s)",
                     ticker, near_high, near_low)
        return None

    close = float(df["Close"].iloc[-1])
    return {
        "ticker":           ticker,
        "close":            round(close, 2),
        "adr":              round(adr, 2),
        "dollar_volume_m":  round(dv / 1_000_000, 2),
        "mom_1m":           round(m1, 1) if m1 is not None else None,
        "mom_3m":           round(m3, 1) if m3 is not None else None,
        "mom_6m":           round(m6, 1) if m6 is not None else None,
        "passes_1m":        passes_1m,
        "passes_3m":        passes_3m,
        "passes_6m":        passes_6m,
        "near_high":        near_high,
        "near_low":         near_low,
        "pct_from_6d_high": rng.get("pct_from_high"),
        "pct_from_6d_low":  rng.get("pct_from_low"),
    }


def run_qm_stage2(tickers: list[str], verbose: bool = True,
                  enriched_map: dict = None, shared: bool = False) -> list[dict]:
    """
    Stage 2 — Download OHLCV and apply QM gate to each candidate.
    Returns list of passing ticker dicts for Stage 3.
    
    If enriched_map is provided, skip batch download (for combined scanning).
    """
    from modules.data_pipeline import batch_download_and_enrich

    total = len(tickers)
    _progress("Stage 2", 12, f"Downloading price history for {total} candidates…")
    logger.info("[QM Stage2] Processing %d candidates", total)

    batch_size  = getattr(C, "QM_STAGE2_BATCH_SIZE",  40)
    max_workers = getattr(C, "QM_STAGE2_MAX_WORKERS", 12)
    sleep_sec   = getattr(C, "QM_STAGE2_BATCH_SLEEP",  1.5)

    def _progress_cb(batch_num: int, total_batches: int, msg: str = ""):
        pct = 12 + int((batch_num / total_batches) * 35)
        _progress("Stage 2", pct, msg)

    if enriched_map is None:
        enriched = batch_download_and_enrich(
            tickers,
            period="6mo",  # 6 months sufficient: ADR(14d) + mom 6M(126d) + 6d consolidation + SMA50
            progress_cb=_progress_cb,
        )
    else:
        enriched = enriched_map
        if verbose:
            logger.info("[QM Stage2] Using pre-downloaded enriched_map (%d records)", len(enriched))

    _progress("Stage 2", 48, f"Applying QM momentum/ADR/dollar-volume filters…")
    passed = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_check_qm_stage2, tkr, df): tkr
            for tkr, df in enriched.items()
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
                    logger.info("[QM S2 PASS] %s ADR=%.1f%% $Vol=$%.1fM",
                                tkr, result["adr"], result["dollar_volume_m"])
            except Exception as exc:
                logger.warning("[QM S2 ERROR] %s: %s", tkr, exc)

            pct = 48 + int(done / max(total, 1) * 12)
            _progress("Stage 2", min(pct, 60), f"Checked {done}/{total}…", tkr)

    _progress("Stage 2", 62, f"{len(passed)} passed ADR/momentum/volume gate")
    logger.info("[QM Stage2] %d / %d passed", len(passed), total)
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Consolidation + MA alignment + higher lows quality scoring
# ─────────────────────────────────────────────────────────────────────────────

def _score_qm_stage3(row: dict, df: pd.DataFrame) -> dict | None:
    """
    Apply full QM quality scoring for Stage 3.
    Augments the Stage 2 dict with consolidation quality, MA alignment,
    higher lows, and a preliminary star rating for sorting.
    """
    from modules.data_pipeline import (
        get_ma_alignment, get_higher_lows, get_consolidation_tightness
    )
    from modules.qm_setup_detector import detect_setup_type

    ticker = row["ticker"]

    if df.empty or len(df) < 50:
        return None

    # ── Setup type detection ───────────────────────────────────────────────
    setup_info = detect_setup_type(df, ticker)
    setup_type = setup_info.get("primary_type", "")
    type_code  = setup_info.get("type_code", "")

    # ── MA alignment ───────────────────────────────────────────────────────
    ma = get_ma_alignment(df)
    surfing_ma   = ma.get("surfing_ma", 0)
    all_ma_up    = ma.get("all_ma_rising", False)
    sma_10       = ma.get("sma_10")
    sma_20       = ma.get("sma_20")
    sma_50       = ma.get("sma_50")

    # Hard disqualifier: all MAs pointing down
    s10_rising = ma.get("sma_10_rising", False)
    s20_rising = ma.get("sma_20_rising", False)
    s50_rising = ma.get("sma_50_rising", False)
    if not s20_rising and not s50_rising and not s10_rising:
        logger.debug("[QM S3 VETO] %s all MAs pointing down", ticker)
        return None

    # ── Higher Lows ────────────────────────────────────────────────────────
    hl = get_higher_lows(df)
    has_hl   = hl.get("has_higher_lows", False)
    num_lows = hl.get("num_lows", 0)

    # ── Consolidation tightness ────────────────────────────────────────────
    tightness = get_consolidation_tightness(df)
    is_tight      = tightness.get("is_tight", False)
    tight_ratio   = tightness.get("tightness_ratio", 1.0)
    range_trend   = tightness.get("range_trend", "unknown")

    # ── Volume analysis ────────────────────────────────────────────────────
    close_price = float(df["Close"].iloc[-1])
    recent20 = df.tail(20)
    avg_vol_20   = float(recent20["Volume"].mean()) if len(recent20) >= 20 else 0
    latest_vol   = float(df["Volume"].iloc[-1])
    vol_ratio    = (latest_vol / avg_vol_20) if avg_vol_20 > 0 else 0.0

    # ── Preliminary star rating (quick automated estimate for sorting) ─────
    # Full detailed star rating is computed in qm_analyzer.py
    star = getattr(C, "QM_STAR_BASE", 3.0)

    # Dimension A: Momentum quality
    if row.get("passes_1m") and row.get("passes_3m"):
        star += 0.5   # Multi-timeframe momentum leader
    elif row.get("passes_6m"):
        star += 0.5   # 6M monster — very strong

    # Dimension B: ADR adjustment
    adr = row.get("adr", 0)
    if adr >= getattr(C, "QM_ADR_BONUS_HIGH", 15.0):
        star += 1.0
    elif adr < getattr(C, "QM_ADR_PENALTY_MARGINAL", 8.0):
        star -= 0.5

    # Dimension C: Consolidation (very important for Qullamaggie)
    # Higher Lows are critical evidence of institutional accumulation
    if is_tight and has_hl:
        star += 1.0  # Tight + higher lows = perfect setup
    elif is_tight and num_lows >= 2:
        star += 0.75  # Tight + some higher lows
    elif has_hl and num_lows >= 3:
        star += 0.7   # Multiple higher lows without tightness
    elif has_hl or (is_tight and num_lows >= 2):
        star += 0.5  # Either tight or some higher lows
    else:
        star -= 0.75  # No consolidation quality evidence (like ASTI) — significant penalty
        # This reflects that without higher lows, setup is less reliable

    # Dimension D: MA alignment
    if surfing_ma == 20:
        star += 1.0   # Golden line — best setup
    elif surfing_ma == 10:
        star += 0.5   # Most aggressive surfe
    elif surfing_ma == 50:
        star += 0.0   # Fine but slower
    elif not s20_rising and not s10_rising:
        star -= 1.0   # MAs diverging down

    # ── Supplement 2: Earnings proximity blackout ─────────────────────────
    # "Five star setup but I'm not trading it because of earnings." — Qullamaggie
    earnings_warning   = False
    days_to_earnings   = None
    try:
        from modules.data_pipeline import get_next_earnings_date
        from datetime import date as _date
        next_earn = get_next_earnings_date(ticker)
        if next_earn:
            days_to_earnings = (next_earn - _date.today()).days
            blackout = getattr(C, "QM_EARNINGS_BLACKOUT_DAYS", 3)
            if 0 <= days_to_earnings <= blackout:
                star -= 1.0
                earnings_warning = True
                logger.info(
                    "[QM S3] %s earnings in %d days — star -1.0 (blackout)",
                    ticker, days_to_earnings
                )
    except Exception:
        pass

    # Cap and floor
    star_max = getattr(C, "QM_STAR_MAX", 6.0)
    star = round(max(0.0, min(star, star_max)), 1)

    # Min star gate
    min_star = getattr(C, "QM_SCAN_MIN_STAR", 3.0)
    if star < min_star:
        logger.debug("[QM S3 FILTER] %s star=%.1f below min=%.1f", ticker, star, min_star)
        return None

    result = {**row,
        "setup_type":      setup_type,
        "setup_code":      type_code,
        "sma_10":          sma_10,
        "sma_20":          sma_20,
        "sma_50":          sma_50,
        "sma_10_rising":   s10_rising,
        "sma_20_rising":   s20_rising,
        "sma_50_rising":   s50_rising,
        "all_ma_rising":   all_ma_up,
        "surfing_ma":      surfing_ma,
        "price_vs_sma_10": ma.get("price_vs_sma_10"),
        "price_vs_sma_20": ma.get("price_vs_sma_20"),
        "price_vs_sma_50": ma.get("price_vs_sma_50"),
        "has_higher_lows": has_hl,
        "num_higher_lows": num_lows,
        "is_tight":        is_tight,
        "tight_ratio":     tight_ratio,
        "range_trend":     range_trend,
        "vol_ratio":       round(vol_ratio, 2),
        "avg_vol_20d":     int(avg_vol_20),
        "qm_star":         star,
        "earnings_warning": earnings_warning,
        "days_to_earnings": days_to_earnings,
        "scan_date":       date.today().isoformat(),
    }
    return result


def run_qm_stage3(stage2_rows: list[dict],
                  enriched_cache: dict | None = None) -> pd.DataFrame:
    """
    Stage 3 — Apply consolidation/MA quality filters and compute preliminary
    star ratings.  Sorts output by qm_star descending.

    Args:
        stage2_rows:    output from run_qm_stage2()
        enriched_cache: (optional) dict[ticker→DataFrame] if already downloaded

    Returns:
        pd.DataFrame sorted by qm_star descending, capped at QM_SCAN_TOP_N rows.
    """
    from modules.data_pipeline import get_historical, get_technicals

    total = len(stage2_rows)
    _progress("Stage 3", 64, f"Quality scoring {total} Stage-2 candidates…")
    logger.info("[QM Stage3] Scoring %d candidates", total)

    results = []
    for i, row in enumerate(stage2_rows):
        if _is_cancelled():
            break

        ticker = row["ticker"]
        pct    = 64 + int((i / max(total, 1)) * 30)
        _progress("Stage 3", pct, f"Scoring {ticker}…", ticker)

        # Get enriched DataFrame (use cache if available)
        df = pd.DataFrame()
        if enriched_cache and ticker in enriched_cache:
            raw_df = enriched_cache[ticker]
            df = get_technicals(raw_df) if "SMA_20" not in raw_df.columns else raw_df
        else:
            df = get_historical(ticker, period="1y", use_cache=True)
            if not df.empty:
                from modules.data_pipeline import get_technicals as _ta
                df = _ta(df)

        try:
            scored = _score_qm_stage3(row, df)
            if scored is not None:
                results.append(scored)
        except Exception as exc:
            logger.warning("[QM S3 ERROR] %s: %s", ticker, exc)

    _progress("Stage 3", 95, f"Sorting {len(results)} results…")

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results)
    df_out = df_out.sort_values("qm_star", ascending=False).reset_index(drop=True)

    top_n = getattr(C, "QM_SCAN_TOP_N", 50)
    return df_out.head(top_n)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def _save_scan_results_csv(df_passed: pd.DataFrame, df_all: pd.DataFrame,
                            scan_start: "datetime") -> None:
    """
    Auto-save QM scan results to scan_results/ for debugging.

    Creates two files per run:
      scan_results/qm_passed_YYYYMMDD_HHMMSS.csv  — stocks that met min_star
      scan_results/qm_all_YYYYMMDD_HHMMSS.csv     — all Stage-3 evaluated rows

    Keeps the last QM_SCAN_RESULTS_KEEP (default 30) runs per file type to
    avoid unbounded disk growth.
    """
    out_dir = ROOT / "scan_results"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = scan_start.strftime("%Y%m%d_%H%M%S")
    keep = getattr(C, "QM_SCAN_RESULTS_KEEP", 30)

    for label, df in [("qm_passed", df_passed), ("qm_all", df_all)]:
        fpath = out_dir / f"{label}_{ts}.csv"
        if df is not None and not df.empty:
            df.to_csv(fpath, index=False)
            logger.info("[QM Scan] Saved %d rows → %s", len(df), fpath.name)
        else:
            # Write empty file so the timestamp slot is still visible
            fpath.write_text("(no results)\n")
            logger.info("[QM Scan] No data for %s — wrote empty placeholder", fpath.name)

        # Rotate: keep only most recent N files of this label type
        existing = sorted(out_dir.glob(f"{label}_*.csv"), reverse=True)
        for old in existing[keep:]:
            try:
                old.unlink()
            except Exception:
                pass


def run_qm_scan(verbose: bool = True, min_star: float = None, top_n: int = None,
                strict_rs: bool = False, stage1_source: str = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full 3-stage Qullamaggie breakout scan.

    Parameters
    ----------
    verbose   : if True, print progress and results to console
    min_star  : minimum star rating to include in df_passed (default: from config)
    top_n     : limit df_passed to top N rows (default: no limit)
    strict_rs : if True, enforce RS rank ≥ QM_RS_STRICT_MIN_RANK (default 90)
                — Supplement 35: 「I only trade the top RS stocks.」

    Returns:
        (df_passed, df_all) where:
            df_passed — stocks with qm_star ≥ min_star (sorted best→worst, limited to top_n)
            df_all    — all Stage-3 evaluated rows (may include below-min-star)

    Usage from CLI:
        from modules.qm_screener import run_qm_scan
        df, _ = run_qm_scan()

    Usage from app.py background thread:
        set_qm_scan_cancel(cancel_event)
        df, df_all = run_qm_scan(min_star=3.0, top_n=50)
    """
    scan_start = datetime.now()
    _progress("Initialising", 0, "Starting Qullamaggie Breakout Scan…")
    logger.info("═" * 65)
    logger.info("  QULLAMAGGIE BREAKOUT SCAN — %s", scan_start.strftime("%Y-%m-%d %H:%M"))
    logger.info("═" * 65)

    # ── Market environment gate ───────────────────────────────────────────
    if getattr(C, "QM_BLOCK_IN_BEAR", True):
        try:
            from modules.market_env import assess as mkt_assess
            mkt = mkt_assess(verbose=False)
            regime = mkt.get("regime", "")
            if regime in ("DOWNTREND", "MARKET_IN_CORRECTION"):
                logger.warning("[QM Scan] Market regime: %s — QM breakout entries blocked", regime)
                _progress("Blocked", 100, f"Bear market block ({regime})")
                return pd.DataFrame(), pd.DataFrame()
        except Exception as exc:
            logger.warning("[QM Scan] Could not check market environment: %s", exc)

    # ── Stage 1 ───────────────────────────────────────────────────────────
    candidates = run_qm_stage1(verbose=verbose, stage1_source=stage1_source)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not candidates:
        _progress("Error", 100, "Stage 1 returned no candidates")
        return pd.DataFrame(), pd.DataFrame()
    logger.info("[QM Scan] Stage1: %d candidates", len(candidates))

    # ── Stage 2 ───────────────────────────────────────────────────────────
    s2_rows = run_qm_stage2(candidates, verbose=verbose)
    if _is_cancelled():
        return pd.DataFrame(), pd.DataFrame()
    if not s2_rows:
        _progress("Done", 100, "No candidates passed ADR/momentum gate")
        return pd.DataFrame(), pd.DataFrame()
    logger.info("[QM Scan] Stage2: %d candidates", len(s2_rows))

    # ── Stage 3 ───────────────────────────────────────────────────────────
    df_all = run_qm_stage3(s2_rows)

    if df_all.empty:
        _progress("Done", 100, "No candidates passed quality scoring")
        return pd.DataFrame(), df_all

    # Separate passed vs all — use provided min_star or fall back to config
    min_star_val = min_star if min_star is not None else getattr(C, "QM_SCAN_MIN_STAR", 3.0)
    top_n_val    = top_n if top_n is not None else getattr(C, "QM_SCAN_TOP_N", 50)
    
    df_passed = df_all[df_all["qm_star"] >= min_star_val].copy()
    df_passed = df_passed.sort_values("qm_star", ascending=False)

    # ── Supplement 35: RS strict filter ───────────────────────────────────
    if strict_rs and "rs_rank" in df_passed.columns:
        rs_min = getattr(C, "QM_RS_STRICT_MIN_RANK", 90.0)
        before = len(df_passed)
        df_passed = df_passed[df_passed["rs_rank"] >= rs_min]
        logger.info(
            "[QM Scan] strict_rs filter: RS≥%.0f kept %d/%d",
            rs_min, len(df_passed), before
        )

    if top_n_val and len(df_passed) > top_n_val:
        df_passed = df_passed.head(top_n_val)

    # ── Final sorting and filtering ────────────────────────────────────────
    # Note: qm_star is a heuristic approximation. For precise rating including 
    # all 6 dimensions (especially consolidation quality), users should click 
    # into detailed analysis (analyze_qm) which computes capped_stars.
    if not df_passed.empty:
        df_passed = df_passed.sort_values("qm_star", ascending=False)


    elapsed = (datetime.now() - scan_start).total_seconds()
    logger.info("[QM Scan] FINAL: %d passed | %d scored total | %.0fs elapsed",
                len(df_passed), len(df_all), elapsed)

    # ── Auto-save CSV to scan_results/ for debugging ───────────────────────
    try:
        _save_scan_results_csv(df_passed, df_all, scan_start)
    except Exception as _csv_exc:
        logger.warning("[QM Scan] CSV auto-save failed: %s", _csv_exc)

    if verbose:
        print()
        print(f"{_BOLD}{'═'*65}{_RESET}")
        print(f"{_BOLD}  QM SCAN RESULTS  — {date.today().isoformat()}{_RESET}")
        print(f"{'═'*65}")
        print(f"  Stage 1: {len(candidates):>4} candidates")
        print(f"  Stage 2: {len(s2_rows):>4} passed ADR/momentum/dollar-vol gate")
        print(f"  Stage 3: {len(df_passed):>4} with star rating ≥ {min_star_val}{_STAR}")
        print(f"  Elapsed: {elapsed:.0f}s")
        print()

        if not df_passed.empty:
            cols_show = ["ticker", "qm_star", "close", "adr", "dollar_volume_m",
                         "mom_1m", "mom_3m", "surfing_ma", "has_higher_lows", "is_tight"]
            cols_show = [c for c in cols_show if c in df_passed.columns]
            print(df_passed[cols_show].to_string(index=False))

    _progress("Done", 100, f"{len(df_passed)} stocks found with ≥{min_star_val}{_STAR}")
    return df_passed, df_all
