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

import pandas as pd
import numpy as np
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_universe, FVF_AVAILABLE

logger = logging.getLogger(__name__)

RS_CACHE_FILE = ROOT / C.DATA_DIR / "rs_cache.csv"

# ─────────────────────────────────────────────────────────────────────────────
# Universe construction
# ─────────────────────────────────────────────────────────────────────────────

def build_rs_universe() -> list:
    """
    Get the list of all US stocks to be ranked.
    Uses finvizfinance with minimal filters: price >$5, avg vol >100K.
    Falls back to S&P500+NASDAQ100 constituents if finvizfinance fails.
    """
    if FVF_AVAILABLE:
        filters = {
            "Price": "Over $5",
            "Average Volume": "Over 100K",
            "Country": "USA",
            "EPS growththis year": "Positive (>0%)",   # excludes ETFs/funds
        }
        logger.info("[RS] Fetching stock universe (this may take 30-60s)...")
        df = get_universe(filters, view="Overview", verbose=False)
        if not df.empty and "Ticker" in df.columns:
            tickers = df["Ticker"].dropna().str.strip().tolist()
            # Filter out bad tickers (warrants, units, etc.)
            tickers = [t for t in tickers
                       if t and len(t) <= 5
                       and t.isalpha()
                       and "^" not in t
                       and "/" not in t]
            logger.info("[RS] Universe size: %d tickers", len(tickers))
            return tickers

    # Fallback: use a broad index ETF constituent proxy
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

    def _download_batch_with_retry(batch: list, max_retries: int = 3) -> object:
        """yf.download wrapper with exponential back-off on rate-limit (429) errors."""
        for attempt in range(max_retries):
            try:
                return yf.download(
                    tickers=batch,
                    period="1y",
                    interval="1d",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
            except Exception as exc:
                err = str(exc).lower()
                if any(k in err for k in ("429", "rate limit", "too many requests",
                                          "connection reset", "remote end closed")):
                    wait = 5.0 * (2 ** attempt)   # 5s, 10s, 20s
                    logger.warning("[RS] Rate-limit/connection error on attempt %d/%d, "
                                   "waiting %.0fs: %s", attempt + 1, max_retries, wait, exc)
                    time.sleep(wait)
                else:
                    logger.debug("[RS] Batch download exception (batch %d): %s", attempt, exc)
                    return None
        logger.warning("[RS] Batch failed after %d retries", max_retries)
        return None

    for i, batch in enumerate(batches):
        logger.debug("[RS] Batch %d/%d (%d tickers)...", i + 1, total, len(batch))
        raw = _download_batch_with_retry(batch)
        if raw is not None and not raw.empty:
            # Extract closing prices
            if isinstance(raw.columns, pd.MultiIndex):
                try:
                    closes = raw["Close"]
                    if isinstance(closes, pd.Series):
                        closes = closes.to_frame(name=batch[0])
                    for col in closes.columns:
                        if closes[col].dropna().shape[0] > 50:
                            all_close[col] = closes[col]
                except Exception:
                    for tkr in batch:
                        try:
                            s = raw.xs(tkr, axis=1, level=1)["Close"]
                            if s.dropna().shape[0] > 50:
                                all_close[tkr] = s
                        except Exception:
                            pass
            else:
                if "Close" in raw.columns:
                    all_close[batch[0]] = raw["Close"]

        # Throttle between batches to respect yfinance rate limits
        if i < total - 1:
            time.sleep(1.0)

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


def get_rs_rank(ticker: str, force_refresh: bool = False) -> float:
    """
    Get the RS percentile rank of a single ticker.
    Returns float 1-99, or 50.0 if ticker not in universe.
    """
    _ensure_rs_loaded(force_refresh)
    if _rs_df.empty:
        return 50.0
    row = _rs_df[_rs_df["Ticker"].str.upper() == ticker.upper()]
    if row.empty:
        return 50.0
    return float(row["RS_Rank"].iloc[0])


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
