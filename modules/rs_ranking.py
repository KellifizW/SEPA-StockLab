"""
modules/rs_ranking.py
─────────────────────
True Relative Strength percentile ranking engine.

Methodology (IBD-style, Minervini-aligned):
  RS_raw = 0.40 × 3M_return + 0.20 × 6M_return + 0.20 × 9M_return + 0.20 × 12M_return

Result: percentile rank 1-99 across the full US stock universe.
TT9 Minervini requirement: RS ≥ 70 (ideal ≥ 80).

Cache: data/rs_cache.csv — rebuilt once per calendar day.
"""

import os
import sys
import time
import logging
from pathlib import Path
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_universe, FVF_AVAILABLE
try:
    from modules.nasdaq_universe import get_universe_nasdaq as _get_nasdaq_universe
    _NASDAQ_AVAILABLE = True
except Exception:
    _NASDAQ_AVAILABLE = False

logger = logging.getLogger(__name__)

RS_CACHE_FILE = ROOT / C.DATA_DIR / "rs_cache.csv"

# ─────────────────────────────────────────────────────────────────────────────
# Universe construction
# ─────────────────────────────────────────────────────────────────────────────

def build_rs_universe() -> list:
    """
    Get the list of all US stocks to be ranked.
    Priority: NASDAQ FTP cache (fast, ~instant) → finvizfinance (slow, ~180s) → S&P500 Wikipedia.
    NASDAQ FTP provides a broader universe (~8000 tickers) and is already cached from Stage 1.
    """
    def _clean_tickers(raw: list) -> list:
        """Remove warrants, units, ETNs and other non-standard tickers."""
        return [
            t for t in raw
            if t and len(t) <= 5
            and t.isalpha()
            and "^" not in t
            and "/" not in t
        ]

    # ── Primary: NASDAQ FTP (near-instant from cache) ─────────────────────
    if _NASDAQ_AVAILABLE:
        try:
            logger.info("[RS] Building universe from NASDAQ FTP cache (fast path)...")
            df_nasdaq = _get_nasdaq_universe()
            if df_nasdaq is not None and not df_nasdaq.empty and "Ticker" in df_nasdaq.columns:
                tickers = _clean_tickers(df_nasdaq["Ticker"].dropna().str.strip().tolist())
                if len(tickers) > 500:  # sanity check — expect 2000+ from NASDAQ FTP
                    logger.info("[RS] Universe size: %d tickers (NASDAQ FTP)", len(tickers))
                    return tickers
        except Exception as e:
            logger.warning("[RS] NASDAQ FTP universe failed: %s — falling back to finvizfinance", e)

    # ── Secondary: finvizfinance screener (slow, ~90 pages) ───────────────
    if FVF_AVAILABLE:
        filters = {
            "Price": "Over $5",
            "Average Volume": "Over 100K",
            "Country": "USA",
            "EPS growththis year": "Positive (>0%)",   # excludes ETFs/funds
        }
        logger.info("[RS] Fetching stock universe via finvizfinance (this may take 30-60s)...")
        df = get_universe(filters, view="Overview", verbose=False)
        if not df.empty and "Ticker" in df.columns:
            tickers = _clean_tickers(df["Ticker"].dropna().str.strip().tolist())
            logger.info("[RS] Universe size: %d tickers (finvizfinance)", len(tickers))
            return tickers

    # ── Fallback: S&P500 via Wikipedia ────────────────────────────────────
    logger.warning("[RS] Falling back to major index constituents for RS universe...")
    sp500_url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        tables = pd.read_html(sp500_url)
        sp500 = tables[0]["Symbol"].tolist()
        return [t.replace(".", "-") for t in sp500]
    except Exception:
        # Last resort: hardcoded major stocks
        return [
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
            "BRK-B", "LLY", "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG",
            "HD", "MRK", "ABBV", "CVX", "COST", "KO", "PEP", "ADBE", "WMT",
            "ACN", "AMD", "MCD", "CRM", "BAC", "TMO", "ORCL", "CSCO", "NFLX",
            "DIS", "TXN", "ABT", "NEE", "LIN", "QCOM", "DHR", "PM", "INTU",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Return calculation
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_returns(close_prices: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate weighted RS score for each ticker column in close_prices.
    close_prices: DataFrame where each column is a ticker, index is dates.
    """
    w = C.RS_WEIGHTS
    today_price = close_prices.iloc[-1]

    def lookback_return(days: int) -> pd.Series:
        idx = max(0, len(close_prices) - days - 1)
        past_price = close_prices.iloc[idx]
        return (today_price - past_price) / past_price.replace(0, np.nan) * 100

    # Approximate trading days per period
    r3m  = lookback_return(63)   # ~3 months
    r6m  = lookback_return(126)  # ~6 months
    r9m  = lookback_return(189)  # ~9 months
    r12m = lookback_return(252)  # ~12 months

    rs_raw = (
        r3m  * w["3m"] +
        r6m  * w["6m"] +
        r9m  * w["9m"] +
        r12m * w["12m"]
    )
    return rs_raw


# ─────────────────────────────────────────────────────────────────────────────
# Main ranking computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_rs_rankings(universe: list = None,
                        force_refresh: bool = False) -> pd.DataFrame:
    """
    Compute RS percentile rankings for the full universe.
    Returns DataFrame with columns: Ticker, RS_Raw, RS_Rank (1-99).
    """
    # — Check cache —
    if not force_refresh and RS_CACHE_FILE.exists():
        try:
            df_cache = pd.read_csv(RS_CACHE_FILE)
            cached_date = df_cache.get("CacheDate", pd.Series()).iloc[0] if len(df_cache) > 0 else ""
            if str(cached_date) == date.today().isoformat() and len(df_cache) > 10:
                logger.info("[RS] Loaded %d RS ranks from cache (%s)", len(df_cache), cached_date)
                return df_cache
        except Exception:
            pass

    if universe is None:
        universe = build_rs_universe()

    if not universe:
        return pd.DataFrame(columns=["Ticker", "RS_Raw", "RS_Rank", "CacheDate"])

    # — Batch download 1-year close prices —
    logger.info("[RS] Downloading 1-year history for %d tickers in batches of %d...",
                len(universe), C.RS_BATCH_SIZE)

    all_close: dict = {}
    batches = [universe[i:i + C.RS_BATCH_SIZE]
               for i in range(0, len(universe), C.RS_BATCH_SIZE)]
    total = len(batches)
    batch_sleep = getattr(C, "RS_BATCH_SLEEP", 0.5)
    parallel_batches = getattr(C, "RS_PARALLEL_BATCHES", 3)

    def _download_batch_with_retry(batch: list, max_retries: int = 3) -> object:
        """yf.download wrapper with exponential back-off on rate-limit (429) errors."""
        for attempt in range(max_retries):
            try:
                return yf.download(
                    tickers=batch,
                    period="1y",
                    interval="1d",
                    auto_adjust=True,
                    threads=False,   # threads=True causes 'dict changed size during iteration' race condition
                    progress=False,
                )
            except Exception as exc:
                err = str(exc).lower()
                # Classify error type
                if any(k in err for k in ("429", "rate limit", "too many requests",
                                          "connection reset", "remote end closed")):
                    wait = 5.0 * (2 ** attempt)   # 5s, 10s, 20s
                    logger.warning("[RS] Rate-limit/connection error on attempt %d/%d, "
                                   "waiting %.0fs: %s", attempt + 1, max_retries, wait, exc)
                    time.sleep(wait)
                    continue  # Retry after backoff
                elif "nonetype" in err or "cannot subscript" in err:
                    # yfinance internal error (edge case, likely malformed ticker or delisted stock)
                    # These are not retryable; skip this batch and continue
                    logger.debug("[RS] yfinance internal type error on batch (likely malformed/delisted ticker): %s", 
                                 type(exc).__name__)
                    return None
                else:
                    # Other errors (API, timeout, etc.) — log at debug and skip
                    logger.debug("[RS] Batch download exception on attempt %d: %s", attempt + 1, type(exc).__name__)
                    if attempt < max_retries - 1:
                        # Try one more time with a short delay
                        time.sleep(2.0)
                        continue
                    else:
                        return None
        logger.debug("[RS] Batch failed after %d retries", max_retries)
        return None

    def _process_batch(batch_info):
        """Download and extract close prices for one batch."""
        idx, batch = batch_info
        local_close = {}
        logger.debug("[RS] Batch %d/%d (%d tickers)...", idx + 1, total, len(batch))
        # Small stagger to avoid simultaneous bursts
        if idx > 0:
            time.sleep(batch_sleep * (idx % parallel_batches))
        raw = _download_batch_with_retry(batch)
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                try:
                    closes = raw["Close"]
                    if isinstance(closes, pd.Series):
                        closes = closes.to_frame(name=batch[0])
                    for col in closes.columns:
                        if closes[col].dropna().shape[0] > 50:
                            local_close[col] = closes[col]
                except Exception:
                    for tkr in batch:
                        try:
                            s = raw.xs(tkr, axis=1, level=1)["Close"]
                            if s.dropna().shape[0] > 50:
                                local_close[tkr] = s
                        except Exception:
                            pass
            else:
                if "Close" in raw.columns:
                    local_close[batch[0]] = raw["Close"]
        return local_close

    # ── Download batches in parallel ─────────────────────────────────────
    logger.info("[RS] Downloading %d batches with %d parallel workers...",
                total, parallel_batches)
    with ThreadPoolExecutor(max_workers=parallel_batches) as pool:
        futures = {pool.submit(_process_batch, (i, b)): i
                   for i, b in enumerate(batches)}
        for fut in as_completed(futures):
            try:
                local_close = fut.result()
                all_close.update(local_close)
            except Exception as exc:
                logger.warning("[RS] Batch future error: %s", exc)

    logger.info("[RS] Downloaded price data for %d tickers", len(all_close))

    if not all_close:
        return pd.DataFrame(columns=["Ticker", "RS_Raw", "RS_Rank", "CacheDate"])

    # — Build aligned DataFrame —
    df_close = pd.DataFrame(all_close)
    if df_close.index.tzinfo is not None:
        df_close.index = df_close.index.tz_localize(None)
    df_close = df_close.sort_index()

    # Require at least 63 trading days (3 months) of data
    df_close = df_close.loc[:, df_close.count() >= 63]

    # — Calculate RS raw scores —
    rs_raw = _calculate_returns(df_close)
    rs_raw = rs_raw.dropna()

    if rs_raw.empty:
        return pd.DataFrame(columns=["Ticker", "RS_Raw", "RS_Rank", "CacheDate"])

    # — Percentile rank (1-99) —
    rs_rank = rs_raw.rank(pct=True) * 100
    rs_rank = rs_rank.clip(1, 99).round(1)

    result = pd.DataFrame({
        "Ticker":    rs_raw.index,
        "RS_Raw":    rs_raw.values.round(2),
        "RS_Rank":   rs_rank.values.round(1),
        "CacheDate": date.today().isoformat(),
    })
    result = result.sort_values("RS_Rank", ascending=False).reset_index(drop=True)

    # — Save cache —
    try:
        result.to_csv(RS_CACHE_FILE, index=False)
        logger.info("[RS] Saved RS rankings for %d tickers -> %s", len(result), RS_CACHE_FILE)
    except Exception as exc:
        logger.warning(f"Could not save RS cache: {exc}")

    # — DuckDB 歷史記錄 —
    if getattr(C, "DB_ENABLED", True):
        try:
            from modules.db import append_rs_history
            append_rs_history(result)
        except Exception as exc:
            logger.warning("DB rs_history write skipped: %s", exc)

    return result



# ─────────────────────────────────────────────────────────────────────────────
# Query interface
# ─────────────────────────────────────────────────────────────────────────────

_rs_df: pd.DataFrame = pd.DataFrame()


def _ensure_rs_loaded(force_refresh: bool = False):
    """Load RS rankings into memory (from cache or compute)."""
    global _rs_df
    if force_refresh or _rs_df.empty:
        _rs_df = compute_rs_rankings(force_refresh=force_refresh)


# Sentinel value indicating RS rank was NOT calculated (ticker missing from RS universe).
# Distinct from a genuinely low score so callers can distinguish "not ranked" from "weak".
RS_NOT_RANKED = -1.0


def get_rs_rank(ticker: str, force_refresh: bool = False) -> float:
    """
    Get the RS percentile rank of a single ticker.
    Returns float 1-99, or RS_NOT_RANKED (-1.0) if ticker not in universe.
    """
    _ensure_rs_loaded(force_refresh)
    if _rs_df.empty:
        return RS_NOT_RANKED
    row = _rs_df[_rs_df["Ticker"].str.upper() == ticker.upper()]
    if row.empty:
        return RS_NOT_RANKED
    return float(row["RS_Rank"].iloc[0])


def _compute_approx_rs_rank(ticker: str) -> float:
    """
    Compute a rough RS percentile rank for a single ticker using a ~100-stock reference set.
    This avoids downloading the full universe when no RS cache is available.
    Ticker is always included in the reference set so it ranks against real market peers.
    Returns float 1-99, or RS_NOT_RANKED on failure.
    """
    # Representative reference set covering all major sectors (~100 large/mid caps)
    REFERENCE_TICKERS = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
        "BRK-B", "LLY", "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG",
        "HD", "MRK", "ABBV", "CVX", "COST", "KO", "PEP", "ADBE", "WMT",
        "ACN", "AMD", "MCD", "CRM", "BAC", "TMO", "ORCL", "CSCO", "NFLX",
        "DIS", "TXN", "ABT", "NEE", "LIN", "QCOM", "DHR", "PM", "INTU",
        "NOW", "ISRG", "GE", "RTX", "CAT", "HON", "SPGI", "BLK", "AXP",
        "GS", "SCHW", "PLD", "AMT", "CI", "SYK", "ZTS", "MO", "USB",
        "DE", "ETN", "AON", "ICE", "CME", "MCO", "EQIX", "SHW", "MMC",
        "ITW", "APD", "NSC", "WM", "GD", "EW", "HCA", "REGN", "BIIB",
        "GILD", "MRNA", "VRTX", "DXCM", "IDXX", "ELV", "HUM", "MTD",
        "RMD", "BSX", "IQV", "BDX", "TSCO", "ODFL", "FAST", "ROST",
        "RCL", "MAR", "HLT", "ABNB", "UBER", "LYFT", "SNOW", "PLTR",
        "CRWD", "PANW", "ZS", "DDOG", "MDB", "NET", "ANET", "SMCI",
        ticker,  # Always include the target ticker
    ]
    reference = list(dict.fromkeys(REFERENCE_TICKERS))  # deduplicate, preserve order

    try:
        logger.info("[RS] Downloading 1-year history for %d-stock reference set (lightweight RS for %s)...",
                    len(reference), ticker)
        raw = yf.download(
            tickers=reference,
            period="1y",
            interval="1d",
            auto_adjust=True,
            threads=False,
            progress=False,
        )
        if raw is None or raw.empty:
            return RS_NOT_RANKED

        closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        closes = closes.dropna(axis=1, how="all")
        closes = closes.loc[:, closes.count() >= 63]

        if ticker not in closes.columns:
            return RS_NOT_RANKED

        rs_raw = _calculate_returns(closes)
        rs_raw = rs_raw.dropna()

        if ticker not in rs_raw.index:
            return RS_NOT_RANKED

        rs_rank = rs_raw.rank(pct=True) * 100
        rs_rank = rs_rank.clip(1, 99).round(1)
        rank = float(rs_rank[ticker])
        logger.info("[RS] Approximate RS rank for %s: %.1f (vs %d-stock reference set)", ticker, rank, len(closes.columns) - 1)
        return rank
    except Exception as exc:
        logger.warning("[RS] Approximate RS failed for %s: %s", ticker, exc)
        return RS_NOT_RANKED


def get_rs_rank_lightweight(ticker: str) -> float:
    """
    Get RS rank for a single ticker WITHOUT triggering a full universe rebuild.

    Used by single-stock analysis (stock_analyzer, qm_analyzer, ml_analyzer) to avoid
    downloading ~8000 tickers just to score one stock.

    Lookup priority:
    1. In-memory cache (_rs_df) — already loaded, instant
    2. Any existing rs_cache.csv (today's or stale — acceptable for individual analysis)
    3. Approximate RS computed from ticker vs ~100-stock reference set (fast, ~5s)
    """
    global _rs_df

    # 1. Already in memory
    if not _rs_df.empty:
        row = _rs_df[_rs_df["Ticker"].str.upper() == ticker.upper()]
        if not row.empty:
            return float(row["RS_Rank"].iloc[0])

    # 2. Any existing cache file (today's → fresh; older → stale but acceptable)
    if RS_CACHE_FILE.exists():
        try:
            df_cache = pd.read_csv(RS_CACHE_FILE)
            if len(df_cache) > 10 and "Ticker" in df_cache.columns:
                _rs_df = df_cache  # load into memory for subsequent calls in this session
                row = df_cache[df_cache["Ticker"].str.upper() == ticker.upper()]
                if not row.empty:
                    cache_date = df_cache["CacheDate"].iloc[0] if "CacheDate" in df_cache.columns else "unknown"
                    logger.info("[RS] Using cached RS rank for %s from %s (stale ok for single-stock analysis)", ticker, cache_date)
                    return float(row["RS_Rank"].iloc[0])
        except Exception as e:
            logger.debug("[RS] Cache read failed: %s", e)

    # 3. No cache or ticker missing — compute approximate RS from a small reference set
    logger.info("[RS] No RS cache available for %s — using lightweight reference set computation", ticker)
    return _compute_approx_rs_rank(ticker)


def get_rs_top(percentile: float = 80.0,
               force_refresh: bool = False) -> pd.DataFrame:
    """
    Get all stocks with RS rank above the given percentile.
    Returns DataFrame sorted by RS_Rank descending.
    """
    _ensure_rs_loaded(force_refresh)
    if _rs_df.empty:
        return pd.DataFrame()
    df = _rs_df[_rs_df["RS_Rank"] >= percentile].copy()
    return df.sort_values("RS_Rank", ascending=False)


def get_rs_dataframe(force_refresh: bool = False) -> pd.DataFrame:
    """Return the full RS ranking DataFrame."""
    _ensure_rs_loaded(force_refresh)
    return _rs_df.copy()
