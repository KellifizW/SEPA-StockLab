"""
ml_scanner_channels.py — Martin Luk Triple Scanner System
===========================================================
Implements Martin Luk's 3-scanner workflow (Chapter 5 / Core Framework).

Martin runs three independent scan channels EVERY morning to build his
watchlist for the day. Each channel targets a different stock behaviour:

  Channel 1 — Pre-market Gap Scanner
      "Who is gapping up this morning?"
      Identifies stocks up >3% pre-market with catalyst (earnings/news).
      Trade: Flush-and-V-recovery or Opening Range High (ORH) breakout.

  Channel 2 — Biggest Gainers Scanner
      "Who was the biggest winner yesterday?"
      Yesterday's top % gainers with sector/theme context.
      Trade: Pullback to EMA on Day 2+ for a trending continuation.

  Channel 3 — Leader Scanner (Monthly RS Leaders)
      "Who has been running for weeks/months?"
      Stocks with best 1M/3M momentum; already on Martin's watchlist.
      Trade: Dip to 9 EMA / 21 EMA with proper consolidation base.

Public API
----------
  run_gap_scanner(tickers, ...)    → list[dict]
  run_biggest_gainers(...)         → list[dict]
  run_leader_scanner(tickers, ...) → list[dict]
  run_triple_scan(...)             → dict[str, list]

All modules import from data_pipeline ONLY for market data.
"""

from __future__ import annotations

import sys
import logging
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level progress state (follows project pattern)
# ─────────────────────────────────────────────────────────────────────────────

_lock         = threading.Lock()
_cancel_event = threading.Event()
_progress_state: dict = {"stage": "idle", "pct": 0, "msg": ""}

# Parallelization settings for channel scanners
# 32 workers on a 16-core machine: PyArrow reads + NumPy/pandas_ta ops release GIL,
# so more threads than cores is beneficial for this mixed I/O + compute workload.
_SCANNER_THREAD_WORKERS = getattr(C, "ML_SCANNER_WORKERS", 32)  # Configurable via trader_config


def _prog(stage: str, pct: int, msg: str) -> None:
    with _lock:
        _progress_state.update({"stage": stage, "pct": pct, "msg": msg})


def _cancelled() -> bool:
    return _cancel_event.is_set()


def cancel_scan() -> None:
    """Signal all running channel scans to stop."""
    _cancel_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# Channel 1 — Pre-market Gap Scanner
# ─────────────────────────────────────────────────────────────────────────────

def run_gap_scanner(
    tickers: list[str],
    min_gap_pct: float | None = None,
    min_vol_mult: float | None = None,
    catalyst_types: list[str] | None = None,
) -> list[dict]:
    """
    Identify stocks with pre-market / intraday gap-up ≥ min_gap_pct.
    Uses ThreadPoolExecutor for parallel processing (8 workers by default).

    Martin's rules (Chapter 5 — Gap Scanner):
      1. Gap must be ≥ 3% above prior close (configurable)
      2. Volume must confirm (≥ 1.5× 20-day avg in first 30 min)
      3. Gap must be accompanied by a catalyst (earnings, news, upgrade)
      4. Ideal trade: let stock flush below ORH first → V-recovery entry
         OR break above Opening Range High with confirming volume

    Args:
        tickers:       Universe of tickers to scan
        min_gap_pct:   Minimum gap % (default: ML_GAP_SCANNER_MIN_GAP_PCT)
        min_vol_mult:  Minimum volume multiplier (default: ML_GAP_SCANNER_VOL_MULT)
        catalyst_types: List of catalyst strings to match (None = accept any)

    Returns:
        list[dict] — Each dict has: ticker, gap_pct, gap_type, vol_mult,
                     prior_close, open_price, catalyst, channel, notes
    """
    from modules.data_pipeline import get_enriched

    _min_gap   = min_gap_pct  if min_gap_pct  is not None else getattr(C, "ML_GAP_SCANNER_MIN_GAP_PCT", 3.0)
    _min_vol   = min_vol_mult if min_vol_mult is not None else getattr(C, "ML_GAP_SCANNER_VOL_MULT", 1.5)

    def _check_ticker_gap(ticker: str) -> dict | None:
        """Check a single ticker for gap-up. Returns result dict or None."""
        try:
            # Performance optimization: only fetch EMA_9, EMA_21 (skip SMA, RSI, ATR, BBands)
            # Gap check only needs: Open, Close, Volume, High, Low, and optional EMA context
            df = get_enriched(ticker, period="3mo", use_cache=True,
                            indicators=["EMA_9", "EMA_21"])
            if df is None or len(df) < 5:
                return None

            today_open  = float(df["Open"].iloc[-1])
            prior_close = float(df["Close"].iloc[-2])
            if prior_close <= 0:
                return None

            gap_pct = (today_open / prior_close - 1.0) * 100.0
            if gap_pct < _min_gap:
                return None

            # Volume confirmation
            avg_vol_20 = float(df["Volume"].tail(21).iloc[:-1].mean())
            today_vol  = float(df["Volume"].iloc[-1])
            vol_mult   = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
            if vol_mult < _min_vol:
                return None

            # Gap type classification (same logic)
            if gap_pct >= 10.0:
                gap_type = "EARNINGS_GAP"
            elif gap_pct >= 5.0:
                gap_type = "NEWS_GAP"
            else:
                gap_type = "MOMENTUM_GAP"

            # Opening Range (first bar of day)
            orh = float(df["High"].iloc[-1])
            orl = float(df["Low"].iloc[-1])
            close = float(df["Close"].iloc[-1])

            # Basic EMA context (only if present)
            ema9   = None
            ema21  = None
            if "EMA_9" in df.columns:
                ema9  = float(df["EMA_9"].iloc[-1])
            if "EMA_21" in df.columns:
                ema21 = float(df["EMA_21"].iloc[-1])

            # Flush-and-V check: close near ORH after opening dip
            v_recovery = (orl < today_open * 0.99 and close >= orh * 0.995)

            return {
                "ticker":      ticker,
                "channel":     "GAP",
                "gap_pct":     round(gap_pct, 2),
                "gap_type":    gap_type,
                "vol_mult":    round(vol_mult, 2),
                "prior_close": round(prior_close, 2),
                "open_price":  round(today_open, 2),
                "orh":         round(orh, 2),
                "orl":         round(orl, 2),
                "close":       round(close, 2),
                "ema9":        round(ema9, 2) if ema9 else None,
                "ema21":       round(ema21, 2) if ema21 else None,
                "v_recovery":  v_recovery,
                "notes":       (
                    f"Gap +{gap_pct:.1f}% | Vol {vol_mult:.1f}x | "
                    f"{'V-Recovery 形態' if v_recovery else 'ORH突破觀察'}"
                ),
            }

        except Exception as exc:
            logger.debug("[GapScanner] %s error: %s", ticker, exc)
            return None

    results: list[dict] = []
    total = len(tickers)
    _prog("Gap Scanner", 0, f"Scanning {total} tickers for gap-ups ≥{_min_gap:.1f}%… (parallel: {_SCANNER_THREAD_WORKERS} workers)")

    # Parallel processing with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=_SCANNER_THREAD_WORKERS) as executor:
        futures = {executor.submit(_check_ticker_gap, tkr): tkr for tkr in tickers}
        completed = 0
        for future in as_completed(futures):
            if _cancelled():
                break
            ticker = futures[future]
            completed += 1
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug("[GapScanner] Future error for %s: %s", ticker, exc)

            # Progress logging every 100 tickers (or every 50 in first 300) — same pattern as Leader Scanner
            if completed % 100 == 0 or (completed <= 300 and completed % 50 == 0):
                pct = int(completed / max(total, 1) * 100)
                passed_count = len(results)
                _prog("Gap Scanner", pct,
                      f"Progress: {completed}/{total} checked, {passed_count} gap candidates ({pct}%)")
                logger.debug("[ML Channel1 Gap] Progress: %d/%d checked, %d passed",
                             completed, total, passed_count)

    results.sort(key=lambda x: x["gap_pct"], reverse=True)
    _prog("Gap Scanner", 100, f"Done — {len(results)} gap candidates found")
    logger.info("[ML Channel1 Gap] Found %d gap candidates (≥%.1f%%) using %d workers", 
                len(results), _min_gap, _SCANNER_THREAD_WORKERS)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Channel 2 — Biggest Gainers Scanner
# ─────────────────────────────────────────────────────────────────────────────

def run_biggest_gainers(
    tickers: list[str] | None = None,
    top_n: int | None = None,
    min_price: float | None = None,
    min_vol: float | None = None,
) -> list[dict]:
    """
    Identify yesterday's biggest % gainers for Day 2+ pullback opportunities.

    Martin's rules (Chapter 5 — Biggest Gainers):
      1. Sort universe by prior-day % gain, take top N
      2. Must have vol ≥ 1.5× avg on breakout day (the big-gain day)
      3. Look for EMA pullback entry on Day 2+ (not chasing on day of move)
      4. Gains from 3%+ are considered (market-driven momentum signal)

    Implementation:
      - Download prior 5 days of OHLCV for Stage 1 universe (parallel via get_bulk_historical)
      - Calculate last trading day % gain = (Close[today] - Close[yesterday]) / Close[yesterday] * 100
      - Filter by min_price, min_vol, min_gain
      - Sort descending, return top N

    Args:
        tickers:    Ticker universe (default: all Stage 1 candidates)
        top_n:      Top N gainers to return (default: ML_GAINER_SCANNER_TOP_N)
        min_price:  Minimum price filter (default: ML_MIN_PRICE)
        min_vol:    Minimum avg daily dollar volume (default: ML_MIN_DOLLAR_VOL)

    Returns:
        list[dict] with: ticker, gain_pct_prior, last_close, vol_mult, channel
    """
    from modules.data_pipeline import get_historical
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _top_n       = top_n     if top_n     is not None else getattr(C, "ML_GAINER_SCANNER_TOP_N", 50)
    _min_price   = min_price if min_price is not None else getattr(C, "ML_MIN_PRICE", 5.0)
    _min_vol     = min_vol   if min_vol   is not None else getattr(C, "ML_MIN_DOLLAR_VOL", 1_000_000)
    _min_gain    = getattr(C, "ML_GAINER_MIN_GAIN_PCT", 3.0)
    _tickers     = tickers or []

    if not _tickers:
        logger.debug("[ML Channel2 Gainers] No tickers provided")
        return []

    # Gap Scanner already cached 3mo OHLCV for all tickers — read from cache (no download).
    # Use period="3mo" to match the Gap Scanner's cache files (ticker_3mo.parquet).
    _prog("Gainers Scanner", 0, f"從快取讀取 {len(_tickers)} 檔昨日收盤價…")
    logger.info("[ML Channel2 Gainers] Reading prior-day gain from cache for %d tickers", len(_tickers))

    bulk_data: dict = {}
    total = len(_tickers)
    completed = 0

    def _fetch_one(tkr: str):
        # use_cache=True → reads ticker_3mo.parquet written by Gap Scanner
        return tkr, get_historical(tkr, period="3mo", use_cache=True)

    with ThreadPoolExecutor(max_workers=_SCANNER_THREAD_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, tkr): tkr for tkr in _tickers}
        for future in as_completed(futures):
            completed += 1
            try:
                tkr, df = future.result()
                if df is not None and not df.empty:
                    bulk_data[tkr] = df
            except Exception:
                pass
            if completed % 500 == 0 or completed == total:
                pct = int(completed / total * 90)
                _prog("Gainers Scanner", pct,
                      f"讀取快取: {completed}/{total} 檔完成")
                logger.info("[ML Channel2 Gainers] Cache read: %d/%d tickers", completed, total)

    logger.info("[ML Channel2 Gainers] Loaded OHLCV for %d/%d tickers from cache",
                len(bulk_data), total)

    results: list[dict] = []

    for ticker, df in bulk_data.items():
        try:
            if df is None or df.empty or len(df) < 2:
                continue

            # Get last 2 rows (yesterday and today)
            df_sorted = df.sort_index()
            if len(df_sorted) < 2:
                continue

            close_yesterday = float(df_sorted.iloc[-2]["Close"])
            close_today     = float(df_sorted.iloc[-1]["Close"])
            vol_today       = float(df_sorted.iloc[-1]["Volume"])
            
            # Compute prior-day % gain
            if close_yesterday > 0:
                gain_pct = ((close_today - close_yesterday) / close_yesterday) * 100
            else:
                continue

            # Filter: min gain, min price, min volume
            if gain_pct < _min_gain:
                continue
            if close_today < _min_price:
                continue
            if vol_today < _min_vol / close_today:  # Dollar volume proxy
                continue

            # Compute avg volume for vol_mult
            avg_vol = float(df_sorted["Volume"].tail(5).mean())
            vol_mult = vol_today / avg_vol if avg_vol > 0 else 1.0

            results.append({
                "ticker":        ticker,
                "gain_pct_prior": round(gain_pct, 2),
                "last_close":     round(close_today, 2),
                "vol_mult":       round(vol_mult, 2),
                "vol_today":      int(vol_today),
                "channel":        "GAINER",
            })
        except Exception:
            continue

    # Sort by gain_pct descending
    results.sort(key=lambda x: x["gain_pct_prior"], reverse=True)
    
    top_results = results[:_top_n]
    _prog("Gainers Scanner", 100, 
          f"Found {len(top_results)} gainers (≥{_min_gain}%) from {len(_tickers)} tickers")
    logger.info("[ML Channel2 Gainers] Found %d gainers ≥%.1f%% from %d tickers",
                len(top_results), _min_gain, len(_tickers))

    return top_results


def _detect_theme_from_industry(industry: str, sector: str) -> str:
    """Simple keyword matcher to assign a theme badge to a stock."""
    text = (industry + " " + sector).upper()

    theme_map = {
        "AI/半導體":    ["SEMICONDUCTOR", "ARTIFICIAL INTEL", "CHIPMAKER", "GPU", "NVIDIA"],
        "量子計算":      ["QUANTUM"],
        "核能/能源":    ["NUCLEAR", "URANIUM", "SOLAR", "ENERGY"],
        "GLP-1/生技":  ["BIOTECHNOLOGY", "PHARMACEUT", "GLP", "OBESITY"],
        "國防/航太":    ["AEROSPACE", "DEFENSE"],
        "電動車":        ["ELECTRIC VEHICLE", "EV ", "BATTERY"],
        "網絡安全":      ["CYBERSECURITY", "NETWORK SECURITY"],
        "雲端/軟體":    ["SOFTWARE", "CLOUD", "SAAS"],
        "消費品":        ["CONSUMER"],
    }

    for badge, keywords in theme_map.items():
        for kw in keywords:
            if kw in text:
                return badge
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Channel 3 — Leader Scanner
# ─────────────────────────────────────────────────────────────────────────────

def run_leader_scanner(
    tickers: list[str],
    momentum_period: int | None = None,
    min_weeks_trending: int | None = None,
) -> list[dict]:
    """
    Identify established leaders for EMA pullback / base entry.
    Uses ThreadPoolExecutor for parallel processing (8 workers by default).

    Martin's rules (Chapter 5 — Leader Scanner):
      1. Must be at or near 52-week high (within 15%)
      2. 1-month return ≥ +15% (strong momentum)
      3. EMA stack: 9 > 21 > 50 (all rising, clean uptrend)
      4. Price has traded above 21 EMA for ≥ min_weeks_trending weeks
      5. Best entries: pull back to rising 9 EMA or 21 EMA with low volume
      6. Prefer stocks already on your watchlist (ML_LEADER_FROM_WATCHLIST)

    Args:
        tickers:            Candidate universe
        momentum_period:    Lookback days for momentum filter (default 21)
        min_weeks_trending: Minimum weeks above 21 EMA (default 4)

    Returns:
        list[dict] with: ticker, weeks_above_21, dist_52wk_high_pct,
                         mom_1m_pct, ema_stacked, pullback_to_ema, channel
    """
    from modules.data_pipeline import get_enriched, get_ema_alignment, get_pullback_depth

    _mom_period   = momentum_period    if momentum_period    is not None else 21
    _min_weeks    = min_weeks_trending if min_weeks_trending is not None else getattr(C, "ML_LEADER_MIN_WEEKS", 4)
    _dist_52h_max = getattr(C, "ML_LEADER_MAX_DIST_52H_PCT", 15.0)

    def _check_ticker_leader(ticker: str) -> dict | None:
        """Check a single ticker for leader pattern. Returns result dict or None."""
        try:
            # Performance optimization: only fetch EMA_9, EMA_21, EMA_50, EMA_150
            # (needed by get_ema_alignment and get_pullback_depth)
            df = get_enriched(ticker, period="1y", use_cache=True,
                            indicators=["EMA_9", "EMA_21", "EMA_50", "EMA_150"])
            if df is None or len(df) < 50:
                return None

            close = float(df["Close"].iloc[-1])

            # 52-week high proximity
            high_52w = float(df["High"].tail(252).max())
            dist_52h = (high_52w / close - 1.0) * 100.0 if close > 0 else 999
            if dist_52h > _dist_52h_max:
                return None

            # 1-month momentum
            if len(df) < _mom_period + 1:
                return None
            prior_price = float(df["Close"].iloc[-_mom_period - 1])
            mom_1m = (close / prior_price - 1.0) * 100.0 if prior_price > 0 else 0
            min_mom = getattr(C, "ML_LEADER_MIN_MOM_1M_PCT", 15.0)
            if mom_1m < min_mom:
                return None

            # EMA structure
            ema = get_ema_alignment(df)
            ema21_rising = ema.get("ema_21_rising", False)
            ema9         = ema.get("ema_9")
            ema21        = ema.get("ema_21")
            ema50        = ema.get("ema_50")
            all_stacked  = ema.get("all_stacked", False)

            if not ema21_rising:
                return None

            # Weeks trading above 21 EMA
            weeks_above = 0
            if ema21 is not None and "EMA_21" in df.columns:
                above_mask = df["Close"] > df["EMA_21"]
                streak = 0
                for v in reversed(above_mask.values):
                    if v:
                        streak += 1
                    else:
                        break
                weeks_above = streak // 5  # approx trading days → weeks
            if weeks_above < _min_weeks:
                return None

            # Pullback assessment
            pb = get_pullback_depth(df)
            pb_quality  = pb.get("pullback_quality", "unknown")
            pullback_to = pb.get("nearest_ema")

            return {
                "ticker":            ticker,
                "channel":           "LEADER",
                "mom_1m_pct":        round(mom_1m, 2),
                "dist_52h_pct":      round(dist_52h, 2),
                "weeks_above_21":    weeks_above,
                "ema_stacked":       all_stacked,
                "ema9":              round(ema9,  2) if ema9  else None,
                "ema21":             round(ema21, 2) if ema21 else None,
                "ema50":             round(ema50, 2) if ema50 else None,
                "pullback_quality":  pb_quality,
                "pullback_to_ema":   pullback_to,
                "notes": (
                    f"1M +{mom_1m:.1f}% | {weeks_above}週在21EMA上方 | "
                    f"距52W高 -{dist_52h:.1f}% | 回調至 {pullback_to or 'N/A'}"
                ),
            }

        except Exception as exc:
            logger.debug("[ML Channel3 Leader] %s error: %s", ticker, exc)
            return None

    results: list[dict] = []
    total = len(tickers)
    _prog("Leader Scanner", 0, f"Scanning {total} tickers for leaders… (parallel: {_SCANNER_THREAD_WORKERS} workers)")

    # Parallel processing with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=_SCANNER_THREAD_WORKERS) as executor:
        futures = {executor.submit(_check_ticker_leader, tkr): tkr for tkr in tickers}
        completed = 0
        last_log_count = 0
        for future in as_completed(futures):
            if _cancelled():
                break
            ticker = futures[future]
            completed += 1
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug("[ML Channel3 Leader] Future error for %s: %s", ticker, exc)

            # Update progress every 100 tickers or more frequently at the start
            if completed % 100 == 0 or (completed <= 300 and completed % 50 == 0):
                pct = int(completed / max(total, 1) * 100)
                passed_count = len(results)
                _prog("Leader Scanner", pct, 
                      f"Progress: {completed}/{total} checked, {passed_count} leaders found ({pct}%)")
                logger.debug("[ML Channel3 Leader] Progress: %d/%d checked, %d passed", 
                            completed, total, passed_count)

    # Sort: most momentum first, then fewest weeks (fresher leaders slightly
    # preferred for entry because they haven't extended as far yet)
    results.sort(key=lambda x: (-x["mom_1m_pct"], x["dist_52h_pct"]))
    _prog("Leader Scanner", 100, f"Done — {len(results)} leaders found")
    logger.info("[ML Channel3 Leader] Found %d leaders (%dw min above 21EMA) using %d workers", 
                len(results), _min_weeks, _SCANNER_THREAD_WORKERS)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Combined Triple-Scan Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_triple_scan(
    tickers: list[str],
    include_gaps: bool = True,
    include_gainers: bool = True,
    include_leaders: bool = True,
) -> dict:
    """
    Run all three ML scanner channels and return a merged watchlist.

    Martin Luk's morning routine (Chapter 5):
      07:00 HKT — Pre-market: run gap scanner
      08:00 HKT — After close: run gainers + leader scanners
      Merge all three lists → final watchlist for the day

    This function combines the three channels, removes duplicates (ticker
    appears in multiple channels → highest priority channel wins), and
    adds a composite priority score for sorting.

    Priority order: GAP > GAINER > LEADER
    (Gaps have the most time-sensitive opportunity)

    Args:
        tickers:          Universe for leader/gap scans (gainers from finviz)
        include_gaps:     Run Channel 1 (gap scanner)
        include_gainers:  Run Channel 2 (biggest gainers)
        include_leaders:  Run Channel 3 (leader scanner)

    Returns:
        dict with:
          "gap_results":    list[dict]
          "gainer_results": list[dict]
          "leader_results": list[dict]
          "merged":         list[dict] (deduplicated, sorted by channel priority)
          "summary":        dict (counts per channel)
          "scan_time":      str (ISO timestamp)
    """
    _cancel_event.clear()

    gap_results:    list[dict] = []
    gainer_results: list[dict] = []
    leader_results: list[dict] = []

    # Channel 1
    if include_gaps and tickers:
        _prog("Triple Scan", 5, "Channel 1: Gap Scanner…")
        try:
            gap_results = run_gap_scanner(tickers)
        except Exception as exc:
            logger.warning("[TripleScan] Gap scanner failed: %s", exc)

    # Channel 2
    if include_gainers and tickers:
        _prog("Triple Scan", 40, "Channel 2: Biggest Gainers…")
        try:
            gainer_results = run_biggest_gainers(tickers=tickers)
        except Exception as exc:
            logger.warning("[TripleScan] Gainers scanner failed: %s", exc)

    # Channel 3
    if include_leaders and tickers:
        _prog("Triple Scan", 65, "Channel 3: Leader Scanner…")
        try:
            leader_results = run_leader_scanner(tickers)
        except Exception as exc:
            logger.warning("[TripleScan] Leader scanner failed: %s", exc)

    # Merge & deduplicate — channel priority: GAP > GAINER > LEADER
    seen: set[str] = set()
    merged: list[dict] = []
    channel_priority = {"GAP": 1, "GAINER": 2, "LEADER": 3}

    for item in gap_results + gainer_results + leader_results:
        tkr = item["ticker"]
        if tkr in seen:
            continue
        seen.add(tkr)
        item["channel_priority"] = channel_priority.get(item.get("channel", ""), 9)
        merged.append(item)

    merged.sort(key=lambda x: x["channel_priority"])

    summary = {
        "gap_count":    len(gap_results),
        "gainer_count": len(gainer_results),
        "leader_count": len(leader_results),
        "total_unique": len(merged),
    }

    _prog("Triple Scan", 100,
          f"Done — {summary['total_unique']} unique candidates "
          f"({summary['gap_count']} gap / {summary['gainer_count']} gainers / {summary['leader_count']} leaders)")

    logger.info(
        "[ML TripleScan] Complete: %d gap + %d gainers + %d leaders = %d unique",
        summary["gap_count"], summary["gainer_count"],
        summary["leader_count"], summary["total_unique"],
    )

    return {
        "gap_results":    gap_results,
        "gainer_results": gainer_results,
        "leader_results": leader_results,
        "merged":         merged,
        "summary":        summary,
        "scan_time":      datetime.now().isoformat(timespec="seconds"),
    }
