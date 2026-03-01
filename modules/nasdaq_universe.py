"""modules/nasdaq_universe.py

Fast, free alternative to finvizfinance for Stage 1 stock universe selection.

Strategy:
  1. Download NASDAQ/NYSE/AMEX ticker lists from the free NASDAQ FTP server
     (no login, no API key required).  Files are cached locally for 24 hours.
  2. Filter out ETFs, warrants, preferred shares, test issues, etc.
  3. Batch-download 5-day OHLCV via yfinance to get current price & volume.
  4. Apply Stage 1 coarse filters: price > MIN_STOCK_PRICE, volume > MIN_AVG_VOLUME.
  5. Return a DataFrame with a "Ticker" column — same interface as get_universe().

Typical timing: ~2-4 minutes (vs finvizfinance ~15 minutes).

Usage:
    from modules.nasdaq_universe import get_universe_nasdaq
    df = get_universe_nasdaq()   # returns DataFrame with 'Ticker' column
"""

from __future__ import annotations

import json
import logging
import sys
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# NASDAQ FTP URLs — these are publicly accessible, no auth required
# ─────────────────────────────────────────────────────────────────────────────
_NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED_URL  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Local cache paths
_CACHE_DIR        = ROOT / "data"
_TICKER_LIST_FILE = _CACHE_DIR / "nasdaq_ticker_list.json"  # raw ticker list cache
_UNIVERSE_FILE    = _CACHE_DIR / "nasdaq_universe_cache.json"  # price/vol filtered cache

# ─────────────────────────────────────────────────────────────────────────────
# Build-time thresholds for the universe cache.
# Kept intentionally permissive so the same cache serves both SEPA ($10/200K)
# and QM ($5/300K) scans.  Callers apply their own stricter filter in-memory.
# ─────────────────────────────────────────────────────────────────────────────
_CACHE_BUILD_PRICE_MIN = 2.0       # Always cache stocks priced ≥ $2
_CACHE_BUILD_VOL_MIN   = 50_000    # Always cache stocks with avg vol ≥ 50K

# ─────────────────────────────────────────────────────────────────────────────
# Internal in-memory cache (avoids repeated file I/O within same process)
# Cache format: {"rows": [{"ticker", "close", "avg_vol"}, ...],
#                "build_price_min": float, "build_vol_min": float, "ts": float}
# ─────────────────────────────────────────────────────────────────────────────
_mem_universe_cache: dict | None = None


# ─────────────────────────────────────────────────────────────────────────────
# A. Ticker list download & parse
# ─────────────────────────────────────────────────────────────────────────────

def _download_raw_tickers() -> list[str]:
    """
    Download NASDAQ and Other (NYSE/AMEX) listed stocks from NASDAQ FTP.
    Returns a de-duplicated list of clean ticker symbols.
    Caches result to data/nasdaq_ticker_list.json for NASDAQ_TICKER_CACHE_DAYS.
    """
    cache_days = getattr(C, "NASDAQ_TICKER_CACHE_DAYS", 1)

    # Check file cache
    if _TICKER_LIST_FILE.exists():
        try:
            cached = json.loads(_TICKER_LIST_FILE.read_text())
            age_days = (time.time() - cached.get("ts", 0)) / 86400
            if age_days < cache_days and cached.get("tickers"):
                logger.info(
                    "[NASDAQ FTP] Ticker list cache HIT — age %.1f days (%d tickers)",
                    age_days, len(cached["tickers"])
                )
                return cached["tickers"]
        except Exception as e:
            logger.warning("[NASDAQ FTP] Ticker list cache read error: %s", e)

    logger.info("[NASDAQ FTP] Downloading ticker lists from NASDAQ…")
    tickers: set[str] = set()

    # ── 1. NASDAQ listed ────────────────────────────────────────────────────
    try:
        r = requests.get(_NASDAQ_LISTED_URL, timeout=30)
        r.raise_for_status()
        df_nasdaq = _parse_nasdaq_listed(r.text)
        tickers.update(df_nasdaq["symbol"].tolist())
        logger.info("[NASDAQ FTP] nasdaqlisted.txt → %d symbols", len(df_nasdaq))
    except Exception as e:
        logger.error("[NASDAQ FTP] Failed to download nasdaqlisted.txt: %s", e)

    # ── 2. Other listed (NYSE/AMEX) ─────────────────────────────────────────
    try:
        r = requests.get(_OTHER_LISTED_URL, timeout=30)
        r.raise_for_status()
        df_other = _parse_other_listed(r.text)
        tickers.update(df_other["symbol"].tolist())
        logger.info("[NASDAQ FTP] otherlisted.txt → %d symbols", len(df_other))
    except Exception as e:
        logger.error("[NASDAQ FTP] Failed to download otherlisted.txt: %s", e)

    ticker_list = sorted(tickers)
    logger.info("[NASDAQ FTP] Combined raw tickers: %d", len(ticker_list))

    # Save file cache
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _TICKER_LIST_FILE.write_text(
            json.dumps({"tickers": ticker_list, "ts": time.time()}, indent=2)
        )
    except Exception as e:
        logger.warning("[NASDAQ FTP] Could not save ticker list cache: %s", e)

    return ticker_list


def _parse_nasdaq_listed(text: str) -> pd.DataFrame:
    """
    Parse nasdaqlisted.txt (pipe-delimited).
    Columns: Symbol|Security Name|Market Category|Test Issue|Financial Status|
             Round Lot Size|ETF|NextShares
    Keep: common stocks on NASDAQ (not ETF, not test issue, not NextShares).
    """
    # Last line is metadata "File Creation Time: ..." — drop it
    lines = [ln for ln in text.splitlines() if not ln.startswith("File Creation Time")]
    df = pd.read_csv(StringIO("\n".join(lines)), sep="|")

    # Column name normalisation
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Filters
    mask = (
        (df["test_issue"].str.strip().str.upper() == "N") &
        (df.get("etf", pd.Series("N", index=df.index)).str.strip().str.upper() == "N") &
        (df["symbol"].str.strip().str.len() <= 5) &
        (~df["symbol"].str.contains(r"[^A-Z]", regex=True, na=True))  # letters only
    )
    df = df[mask].copy()
    df["symbol"] = df["symbol"].str.strip()
    return df[["symbol"]].drop_duplicates()


def _parse_other_listed(text: str) -> pd.DataFrame:
    """
    Parse otherlisted.txt (pipe-delimited).
    Columns: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|
             Test Issue|NASDAQ Symbol
    Keep: NYSE/AMEX stocks (Exchange in Y/A/N), not ETF, not test issue.
    """
    lines = [ln for ln in text.splitlines() if not ln.startswith("File Creation Time")]
    df = pd.read_csv(StringIO("\n".join(lines)), sep="|")
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Rename 'act_symbol' → 'symbol' for consistent access
    if "act_symbol" in df.columns:
        df = df.rename(columns={"act_symbol": "symbol"})

    # Keep NYSE (N), AMEX (A), NYSE MKT (A), NYSE Arca (P)
    valid_exchanges = {"N", "A", "P", "Q"}  # Q=NASDAQ (won't overlap much)
    mask = (
        (df["exchange"].str.strip().str.upper().isin(valid_exchanges)) &
        (df.get("etf", pd.Series("N", index=df.index)).str.strip().str.upper() == "N") &
        (df["test_issue"].str.strip().str.upper() == "N") &
        (df["symbol"].str.strip().str.len() <= 5) &
        (~df["symbol"].str.contains(r"[^A-Z]", regex=True, na=True))
    )
    df = df[mask].copy()
    df["symbol"] = df["symbol"].str.strip()
    return df[["symbol"]].drop_duplicates()


# ─────────────────────────────────────────────────────────────────────────────
# B. Price & volume filter via yfinance
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_price_volume_rows(
    raw_tickers: list[str],
    price_min: float,
    vol_min: float,
) -> list[dict]:
    """
    Download last 5 trading days of OHLCV for all tickers using yfinance
    batch downloads.

    Returns a list of dicts: [{"ticker": str, "close": float, "avg_vol": float}, ...]
    for every ticker that passes price_min AND vol_min.

    Storing price+vol in the cache (not just ticker names) allows callers with
    different thresholds (e.g. SEPA @$10 vs QM @$5) to re-filter in-memory
    without re-downloading from yfinance.

    Uses NASDAQ_BATCH_SIZE and NASDAQ_BATCH_SLEEP from trader_config.
    """
    batch_size = getattr(C, "NASDAQ_BATCH_SIZE", 100)
    batch_sleep = getattr(C, "NASDAQ_BATCH_SLEEP", 0.5)
    total       = len(raw_tickers)
    rows: list[dict] = []

    logger.info(
        "[NASDAQ Universe] Fetching price/vol for %d tickers (price≥%.2f, vol≥%d) … "
        "(batches of %d, ~%.0f min)",
        total, price_min, vol_min, batch_size,
        (total / batch_size) * (batch_sleep + 1.5) / 60,
    )
    t0 = time.time()

    # Suppress yfinance "possibly delisted" noise — those tickers simply won't
    # have price data and will be excluded by the filter automatically.
    import warnings
    import logging as _logging
    _yf_logger = _logging.getLogger("yfinance")
    _orig_yf_level = _yf_logger.level
    _yf_logger.setLevel(_logging.CRITICAL)

    for i in range(0, total, batch_size):
        batch = raw_tickers[i: i + batch_size]
        if i % (batch_size * 10) == 0:
            elapsed = time.time() - t0
            logger.info(
                "[NASDAQ Universe] Progress %d%% (%d/%d) — %.0fs elapsed",
                min(100, int(i / total * 100)), i, total, elapsed,
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(
                    batch,
                    period="5d",
                    interval="1d",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
            if data.empty:
                continue

            close  = data["Close"]
            volume = data["Volume"]

            # Normalise: yf returns multi-level columns when batch > 1 ticker
            if isinstance(close, pd.DataFrame):
                last_close = close.iloc[-1].dropna()
                avg_volume = volume.mean()
            else:
                last_close = pd.Series({batch[0]: float(close.iloc[-1])})
                avg_volume = pd.Series({batch[0]: float(volume.mean())})

            # Apply build-time minimum thresholds
            for ticker in last_close.index:
                c = float(last_close.get(ticker, 0) or 0)
                v = float(avg_volume.get(ticker, 0) or 0)
                if c >= price_min and v >= vol_min:
                    rows.append({"ticker": str(ticker), "close": round(c, 4), "avg_vol": round(v, 0)})

        except Exception as e:
            logger.warning("[NASDAQ Universe] Batch %d error: %s", i // batch_size, e)

        time.sleep(batch_sleep)

    # Restore yfinance logger level
    _yf_logger.setLevel(_orig_yf_level)

    elapsed_total = time.time() - t0
    logger.info(
        "[NASDAQ Universe] Fetch complete: %d/%d rows in %.0fs (%.1f min)",
        len(rows), total, elapsed_total, elapsed_total / 60,
    )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# C. Public API — mirrors get_universe() contract
# ─────────────────────────────────────────────────────────────────────────────

def _rows_to_df(rows: list[dict], price_min: float, vol_min: float) -> pd.DataFrame:
    """Filter cached rows by caller's thresholds and return a Ticker DataFrame."""
    tickers = [
        r["ticker"] for r in rows
        if r.get("close", 0) >= price_min
        and r.get("avg_vol", 0) >= vol_min
        and 1 <= len(r["ticker"]) <= 5
        and r["ticker"].replace("-", "").isalpha()
    ]
    return pd.DataFrame({"Ticker": sorted(set(tickers))})


def get_universe_nasdaq(
    price_min: float | None = None,
    vol_min:   float | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    NASDAQ FTP-based stock universe replacement for finvizfinance.

    Returns a DataFrame with at minimum a "Ticker" column.

    The cache stores raw {ticker, close, avg_vol} rows built with very permissive
    thresholds (_CACHE_BUILD_PRICE_MIN / _CACHE_BUILD_VOL_MIN).  Callers supply
    their own price_min / vol_min which is applied as an in-memory filter — so
    SEPA ($10/200K) and QM ($5/300K) both re-use the same file cache without a
    full yfinance re-download.

    Cache is rebuilt only when:
      • TTL expired (FINVIZ_CACHE_TTL_HOURS, default 4 h), OR
      • Caller requests thresholds MORE permissive than the build thresholds
        (e.g. first call with build_price_min>$2 would trigger rebuild at $2)

    Args:
        price_min:  Minimum closing price filter applied to cached rows
        vol_min:    Minimum 5-day avg volume filter applied to cached rows
        use_cache:  False forces a full rebuild regardless of TTL

    Returns:
        pd.DataFrame with column "Ticker"
    """
    global _mem_universe_cache

    price_min = float(price_min) if price_min is not None else float(getattr(C, "MIN_STOCK_PRICE", 10.0))
    vol_min   = float(vol_min)   if vol_min   is not None else float(getattr(C, "MIN_AVG_VOLUME", 200_000))

    cache_ttl_hours = getattr(C, "FINVIZ_CACHE_TTL_HOURS", 4)

    def _cache_is_permissive(cached: dict) -> bool:
        """True if cached build thresholds are at least as permissive as requested."""
        return (
            cached.get("build_price_min", 999) <= price_min
            and cached.get("build_vol_min", 999_999_999) <= vol_min
        )

    # ── In-memory cache ────────────────────────────────────────────────────
    if use_cache and _mem_universe_cache is not None:
        age_h = (time.time() - _mem_universe_cache["ts"]) / 3600
        if age_h < cache_ttl_hours and _cache_is_permissive(_mem_universe_cache):
            rows = _mem_universe_cache["rows"]
            df   = _rows_to_df(rows, price_min, vol_min)
            logger.info(
                "[NASDAQ Universe] Memory cache HIT — %.1fh old, %d rows → %d pass filter",
                age_h, len(rows), len(df),
            )
            return df

    # ── File cache ─────────────────────────────────────────────────────────
    if use_cache and _UNIVERSE_FILE.exists():
        try:
            cached = json.loads(_UNIVERSE_FILE.read_text())
            age_h  = (time.time() - cached.get("ts", 0)) / 3600
            if age_h < cache_ttl_hours and cached.get("rows") and _cache_is_permissive(cached):
                rows = cached["rows"]
                df   = _rows_to_df(rows, price_min, vol_min)
                logger.info(
                    "[NASDAQ Universe] File cache HIT — %.1fh old, %d rows → %d pass filter "
                    "(price≥%.2f, vol≥%d)",
                    age_h, len(rows), len(df), price_min, int(vol_min),
                )
                _mem_universe_cache = cached
                return df
            elif age_h < cache_ttl_hours and cached.get("rows"):
                logger.info(
                    "[NASDAQ Universe] Cache exists but built with stricter thresholds "
                    "(build_price=%.2f > req=%.2f or build_vol=%d > req=%d) — rebuilding",
                    cached.get("build_price_min", 0), price_min,
                    cached.get("build_vol_min", 0), int(vol_min),
                )
        except Exception as e:
            logger.warning("[NASDAQ Universe] File cache read error: %s", e)

    # ── Full build ─────────────────────────────────────────────────────────
    # Always build with the most permissive thresholds so the cache is reusable
    # across callers with different filters.
    build_price = min(price_min, _CACHE_BUILD_PRICE_MIN)
    build_vol   = min(vol_min,   _CACHE_BUILD_VOL_MIN)

    logger.info(
        "[NASDAQ Universe] Starting full universe build (build thresholds: price≥%.2f, vol≥%d)…",
        build_price, int(build_vol),
    )
    t_start = time.time()

    raw_tickers = _download_raw_tickers()
    if not raw_tickers:
        logger.error("[NASDAQ Universe] Failed to get any tickers from NASDAQ FTP")
        return pd.DataFrame()

    rows = _fetch_price_volume_rows(raw_tickers, build_price, build_vol)

    elapsed = time.time() - t_start
    logger.info(
        "[NASDAQ Universe] ✓ Built universe: %d rows in %.0fs (%.1f min)",
        len(rows), elapsed, elapsed / 60,
    )

    # Save cache with build metadata
    cache_payload = {
        "rows": rows,
        "build_price_min": build_price,
        "build_vol_min":   build_vol,
        "ts": time.time(),
    }
    try:
        _UNIVERSE_FILE.write_text(json.dumps(cache_payload))
    except Exception as e:
        logger.warning("[NASDAQ Universe] Could not save file cache: %s", e)

    _mem_universe_cache = cache_payload

    df = _rows_to_df(rows, price_min, vol_min)
    logger.info(
        "[NASDAQ Universe] Returning %d tickers (price≥%.2f, vol≥%d)",
        len(df), price_min, int(vol_min),
    )
    return df


def filter_otc(tickers: list[str]) -> list[str]:
    """
    Remove OTC/Pink-Sheet stocks from a ticker list by cross-referencing against
    the NASDAQ FTP raw ticker list (covers NASDAQ, NYSE, AMEX).

    Uses the already-cached ticker list — no network request needed.
    If the cache is unavailable the original list is returned unchanged.

    Args:
        tickers: list of uppercase ticker strings

    Returns:
        Filtered list with only officially-listed (non-OTC) stocks.
    """
    try:
        # Prefer in-memory raw list from a previous download if available
        if _TICKER_LIST_FILE.exists():
            cached = json.loads(_TICKER_LIST_FILE.read_text())
            listed: set[str] = set(cached.get("tickers", []))
        else:
            # Cache not built yet — trigger a download
            listed = set(_download_raw_tickers())

        if not listed:
            logger.warning("[filter_otc] Listed ticker set is empty — skipping OTC filter")
            return tickers

        filtered = [t for t in tickers if t.upper() in listed]
        removed  = len(tickers) - len(filtered)
        if removed:
            removed_tickers = sorted(set(t.upper() for t in tickers) - listed)
            logger.info(
                "[filter_otc] Removed %d OTC/unlisted tickers: %s",
                removed, ", ".join(removed_tickers[:20])
                + ("…" if len(removed_tickers) > 20 else ""),
            )
        else:
            logger.debug("[filter_otc] No OTC tickers found in input list")
        return filtered

    except Exception as exc:
        logger.warning("[filter_otc] Could not apply OTC filter (%s) — returning original list", exc)
        return tickers


def invalidate_cache() -> None:
    """Force next call to re-download from NASDAQ FTP and re-fetch price/vol data."""
    global _mem_universe_cache
    _mem_universe_cache = None
    for f in [_TICKER_LIST_FILE, _UNIVERSE_FILE]:
        try:
            if f.exists():
                f.unlink()
                logger.info("[NASDAQ Universe] Removed cache file: %s", f.name)
        except Exception as e:
            logger.warning("[NASDAQ Universe] Could not remove %s: %s", f.name, e)
