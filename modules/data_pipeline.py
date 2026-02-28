"""
modules/data_pipeline.py
━━━━━━━━━━━━━━━━━━━━━━━━
Unified data access layer integrating all three libraries:
  • finvizfinance  → coarse screener, sector rankings, quick snapshots
  • yfinance       → historical OHLCV, fundamentals, earnings data
  • pandas_ta      → SMA50/150/200, RSI, ATR, BBands, Slope (all local)

All upper-layer modules import ONLY from here — single point of change.
"""

import os
import sys
import time
import json
import threading
import warnings
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import yfinance as yf

# Suppress noisy warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore")

# pandas_ta integration
try:
    import pandas_ta as ta
    PTA_AVAILABLE = True
except ImportError:
    PTA_AVAILABLE = False
    print("[WARN] pandas_ta not installed. Technical indicators unavailable.")

# finvizfinance integration
try:
    from finvizfinance.screener.overview import Overview as FVOverview
    from finvizfinance.screener.performance import Performance as FVPerformance
    from finvizfinance.screener.technical import Technical as FVTechnical
    from finvizfinance.screener.financial import Financial as FVFinancial
    from finvizfinance.screener.ownership import Ownership as FVOwnership
    from finvizfinance.group.performance import Performance as FVGroupPerf
    from finvizfinance.quote import finvizfinance as FVQuote
    FVF_AVAILABLE = True
except ImportError:
    FVF_AVAILABLE = False
    print("[WARN] finvizfinance not installed. Using yfinance only.")

# ─── path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

PRICE_CACHE_DIR = ROOT / C.PRICE_CACHE_DIR
PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / C.DATA_DIR).mkdir(parents=True, exist_ok=True)
(ROOT / C.REPORTS_DIR).mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# ─── Rate-limit helpers ───────────────────────────────────────────────────────
_last_fvf_call = 0.0

def _fvf_sleep(min_gap: float = 1.2):
    """Polite delay between finvizfinance requests."""
    global _last_fvf_call
    elapsed = time.time() - _last_fvf_call
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    _last_fvf_call = time.time()


# ─── yfinance authentication helpers ─────────────────────────────────────────

def _is_crumb_error(exc: Exception) -> bool:
    """Return True if the exception looks like a Yahoo Finance 401/crumb error."""
    desc = str(exc).lower()
    return ("401" in desc or "unauthorized" in desc
            or "invalid crumb" in desc or "crumb" in desc)


def _reset_yf_crumb() -> bool:
    """
    Force yfinance to re-authenticate on the next API request by clearing
    the cached cookie/crumb from the YfData singleton.
    Returns True on success.
    """
    try:
        from yfinance.data import YfData  # noqa: PLC0415
        yd = YfData()
        yd._crumb = None
        yd._cookie = None
        logger.info("[DataPipeline] yfinance crumb reset — will re-authenticate on next request")
        return True
    except Exception as e:
        logger.warning(f"[DataPipeline] crumb reset failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# A. finvizfinance — Screener & Snapshots  (with TTL cache)
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory cache: key = (sorted filter items, view) -> (timestamp, DataFrame)
_finviz_cache: dict = {}

def _finviz_cache_key(filters_dict: dict, view: str) -> tuple:
    """Create a hashable cache key from filters + view."""
    return (tuple(sorted(filters_dict.items())), view)


def get_universe(filters_dict: dict, view: str = "Overview",
                 verbose: bool = True, use_cache: bool = True) -> pd.DataFrame:
    """
    Coarse screener via finvizfinance.
    Returns a DataFrame with Ticker + metadata columns.
    Falls back to empty DataFrame if network error.
    Results are cached in-memory for FINVIZ_CACHE_TTL_HOURS to speed up repeated scans.
    """
    if not FVF_AVAILABLE:
        logger.error("finvizfinance not available.")
        return pd.DataFrame()

    # ── Check in-memory cache ────────────────────────────────────────────
    cache_ttl_hours = getattr(C, "FINVIZ_CACHE_TTL_HOURS", 4)
    cache_key = _finviz_cache_key(filters_dict, view)
    if use_cache and cache_key in _finviz_cache:
        cached_time, cached_df = _finviz_cache[cache_key]
        age_hours = (time.time() - cached_time) / 3600
        if age_hours < cache_ttl_hours and not cached_df.empty:
            logger.info("[Finviz Cache HIT] Age %.1fh (TTL %dh), %d rows",
                        age_hours, cache_ttl_hours, len(cached_df))
            return cached_df.copy()

    try:
        _fvf_sleep()
        view_map = {
            "Overview":    FVOverview,
            "Performance": FVPerformance,
            "Technical":   FVTechnical,
            "Financial":   FVFinancial,
            "Ownership":   FVOwnership,
        }
        cls = view_map.get(view, FVOverview)
        screener = cls()
        screener.set_filter(filters_dict=filters_dict)
        df = screener.screener_view(verbose=verbose)
        if df is None or df.empty:
            return pd.DataFrame()
        # Normalise Ticker column
        if "Ticker" not in df.columns and df.index.name == "Ticker":
            df = df.reset_index()
        df.columns = [str(c).strip() for c in df.columns]

        # Save to in-memory cache
        _finviz_cache[cache_key] = (time.time(), df.copy())
        logger.debug("[Finviz Cache SAVE] %d rows, view=%s", len(df), view)
        return df
    except Exception as exc:
        logger.warning(f"get_universe error: {exc}")
        return pd.DataFrame()


def get_sector_rankings(group: str = "Sector") -> pd.DataFrame:
    """
    Sector / industry performance ranking via finvizfinance Group module.
    group: 'Sector' | 'Industry' | 'Country' | 'Capitalization'
    Returns DataFrame sorted by Performance(Week) desc.
    """
    if not FVF_AVAILABLE:
        return pd.DataFrame()
    try:
        _fvf_sleep()
        gp = FVGroupPerf()
        df = gp.screener_view(group=group)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as exc:
        logger.warning(f"get_sector_rankings error: {exc}")
        return pd.DataFrame()


def get_snapshot(ticker: str) -> dict:
    """
    Quick fundamental snapshot via finvizfinance quote.
    Returns dict with ~70 key/value pairs (P/E, EPS, SMA20/50/200, RSI, etc.)
    Falls back to empty dict on error.
    """
    if not FVF_AVAILABLE:
        return {}
    try:
        _fvf_sleep(0.8)
        fv = FVQuote(ticker)
        data = fv.ticker_fundament()
        if not data:
            return {}
        return {k: v for k, v in data.items()}
    except Exception as exc:
        logger.warning(f"get_snapshot({ticker}) error: {exc}")
        return {}


def get_insider_fvf(ticker: str) -> pd.DataFrame:
    """Insider trading data from finvizfinance."""
    if not FVF_AVAILABLE:
        return pd.DataFrame()
    try:
        _fvf_sleep()
        fv = FVQuote(ticker)
        return fv.ticker_inside_trader()
    except Exception:
        return pd.DataFrame()


def get_news_fvf(ticker: str) -> pd.DataFrame:
    """Recent news headlines from finvizfinance."""
    if not FVF_AVAILABLE:
        return pd.DataFrame()
    try:
        _fvf_sleep()
        fv = FVQuote(ticker)
        return fv.ticker_news()
    except Exception:
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# B. yfinance — Historical & Fundamental Data
# ═══════════════════════════════════════════════════════════════════════════════

def get_historical(ticker: str, period: str = "2y",
                   use_cache: bool = True) -> pd.DataFrame:
    """
    Daily OHLCV history via yfinance.
    Caches to parquet in data/price_cache/ to minimise repeated downloads.
    Returns DataFrame with columns: Open, High, Low, Close, Volume.
    """
    cache_file = PRICE_CACHE_DIR / f"{ticker.upper()}_{period}.parquet"
    today = date.today().isoformat()

    # Try reading cache (valid for today)
    if use_cache and cache_file.exists():
        try:
            df_cached = pd.read_parquet(cache_file)
            if not df_cached.empty:
                meta_file = cache_file.with_suffix(".meta")
                if meta_file.exists():
                    cache_date = meta_file.read_text().strip()
                    if cache_date == today:
                        return df_cached
        except Exception:
            pass

    # Download from yfinance (retry once on 401/crumb error)
    for _attempt in range(2):
        try:
            tkr = yf.Ticker(ticker)
            df = tkr.history(period=period, interval="1d", auto_adjust=True)
            if df is None or df.empty:
                return pd.DataFrame()
            # Keep only OHLCV columns
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index)
            if df.index.tzinfo is not None:
                df.index = df.index.tz_localize(None)
            df = df.dropna()

            # Save cache
            try:
                df.to_parquet(cache_file)
                cache_file.with_suffix(".meta").write_text(today)
            except Exception:
                pass

            return df
        except Exception as exc:
            if _attempt == 0 and _is_crumb_error(exc):
                logger.warning(
                    f"get_historical({ticker}) crumb/401 error — resetting session and retrying"
                )
                _reset_yf_crumb()
                continue
            logger.warning(f"get_historical({ticker}) error: {exc}")
            return pd.DataFrame()
    return pd.DataFrame()


def get_bulk_historical(tickers: list, period: str = "1y",
                        batch_size: int = None,
                        sleep_sec: float = 2.0) -> dict:
    """
    Batch download OHLCV for multiple tickers.
    Returns dict[ticker] -> DataFrame.
    Downloads in batches to respect rate limits.
    """
    if batch_size is None:
        batch_size = C.RS_BATCH_SIZE

    result = {}
    batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]

    for i, batch in enumerate(batches):
        try:
            raw = yf.download(
                tickers=batch,
                period=period,
                interval="1d",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if raw is None or raw.empty:
                continue

            # Handle single vs multi-ticker column structure
            if len(batch) == 1:
                tkr = batch[0]
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                if raw.index.tzinfo is not None:
                    raw.index = raw.index.tz_localize(None)
                result[tkr] = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
            else:
                for tkr in batch:
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            df_t = raw.xs(tkr, axis=1, level=1)
                        else:
                            df_t = raw[[c for c in raw.columns
                                        if tkr in str(c)]].copy()
                            df_t.columns = [c.split("_")[0] for c in df_t.columns]
                        if df_t.index.tzinfo is not None:
                            df_t.index = df_t.index.tz_localize(None)
                        df_t = df_t.dropna(how="all")
                        if not df_t.empty:
                            result[tkr] = df_t[["Open", "High", "Low",
                                                 "Close", "Volume"]].dropna()
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning(f"Batch download error (batch {i}): {exc}")

        if i < len(batches) - 1:
            time.sleep(sleep_sec)

    return result


def get_fundamentals(ticker: str, use_cache: bool = True) -> dict:
    """
    Comprehensive fundamental data via yfinance.
    Returns a single dict with keys from multiple data sources:
      info, quarterly_eps, earnings_surprise, eps_revisions,
      institutional_holders, insider_transactions, analyst_targets
    Caches to JSON for up to FUNDAMENTALS_CACHE_DAYS to avoid repeated API calls.
    """
    cache_file = PRICE_CACHE_DIR / f"{ticker.upper()}_fundamentals.json"
    meta_file  = cache_file.with_suffix(".fmeta")
    today_str  = date.today().isoformat()

    # ── Try reading cache ────────────────────────────────────────────────
    if use_cache and cache_file.exists() and meta_file.exists():
        try:
            cache_date = meta_file.read_text().strip()
            cached_dt  = date.fromisoformat(cache_date)
            if (date.today() - cached_dt).days < getattr(C, "FUNDAMENTALS_CACHE_DAYS", 1):
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                # If cached info is empty, the cache was written during an auth failure.
                # Delete the stale files now and fall through to re-fetch.
                if not raw.get("info"):
                    logger.warning(
                        "[Cache STALE] get_fundamentals(%s) — cached info is empty "
                        "(likely prior 401). Deleting and re-fetching.", ticker
                    )
                    try:
                        cache_file.unlink(missing_ok=True)
                        meta_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    # Restore DataFrames from dict lists
                    for key in ("quarterly_eps", "earnings_surprise", "eps_revisions",
                                "eps_trend", "institutional_holders", "insider_transactions",
                                "analyst_targets", "quarterly_revenue"):
                        val = raw.get(key)
                        if isinstance(val, list):
                            raw[key] = pd.DataFrame(val) if val else pd.DataFrame()
                        elif not isinstance(val, pd.DataFrame):
                            raw[key] = pd.DataFrame()
                    logger.debug("[Cache HIT] get_fundamentals(%s) from cache", ticker)
                    return raw
        except Exception:
            pass

    # ── Fetch from yfinance ── (retry once on 401/crumb error) ─────────────
    for _attempt in range(2):
        result = {
            "ticker": ticker.upper(),
            "info": {},
            "quarterly_eps": pd.DataFrame(),
            "earnings_surprise": pd.DataFrame(),
            "eps_revisions": pd.DataFrame(),
            "eps_trend": pd.DataFrame(),
            "institutional_holders": pd.DataFrame(),
            "insider_transactions": pd.DataFrame(),
            "analyst_targets": pd.DataFrame(),
            "quarterly_revenue": pd.DataFrame(),
        }
        _retry = False
        try:
            tkr = yf.Ticker(ticker)

            # .info — price, valuation, margins, ROE, SMA50/200, etc.
            try:
                info = tkr.info or {}
                result["info"] = info
            except Exception as _inf_exc:
                if _attempt == 0 and _is_crumb_error(_inf_exc):
                    logger.warning(
                        f"get_fundamentals({ticker}) crumb/401 on .info — resetting session"
                    )
                    _reset_yf_crumb()
                    _retry = True
                pass

            # yfinance 1.2.0 silently returns {} on 401 without raising.
            # If info is empty on the first attempt, reset crumb and retry.
            if not _retry and not result["info"] and _attempt == 0:
                logger.warning(
                    f"get_fundamentals({ticker}) .info is empty on attempt 1 — "
                    "resetting yfinance session and retrying"
                )
                _reset_yf_crumb()
                _retry = True

            if _retry:
                continue

            # Quarterly income statement (for EPS acceleration detection)
            try:
                qi = tkr.quarterly_income_stmt
                if qi is not None and not qi.empty:
                    result["quarterly_eps"] = qi
            except Exception:
                pass

            # Earnings history (EPS surprise data)
            try:
                eh = tkr.get_earnings_history()
                if eh is not None and not eh.empty:
                    result["earnings_surprise"] = eh
            except Exception:
                pass

            # EPS revisions (analyst upgrades/downgrades of estimates)
            try:
                er = tkr.get_eps_revisions()
                if er is not None and not er.empty:
                    result["eps_revisions"] = er
            except Exception:
                pass

            # EPS trend over time
            try:
                et = tkr.get_eps_trend()
                if et is not None and not et.empty:
                    result["eps_trend"] = et
            except Exception:
                pass

            # Institutional holders
            try:
                ih = tkr.get_institutional_holders()
                if ih is not None and not ih.empty:
                    result["institutional_holders"] = ih
            except Exception:
                pass

            # Insider transactions
            try:
                it = tkr.get_insider_transactions()
                if it is not None and not it.empty:
                    result["insider_transactions"] = it
            except Exception:
                pass

            # Analyst price targets
            try:
                at = tkr.analyst_price_targets
                if at is not None and not at.empty:
                    result["analyst_targets"] = at
            except Exception:
                pass

            # Quarterly revenue from income statement
            try:
                if not result["quarterly_eps"].empty:
                    qi = result["quarterly_eps"]
                    if "Total Revenue" in qi.index:
                        result["quarterly_revenue"] = qi.loc[["Total Revenue"]]
            except Exception:
                pass

            break  # fetched successfully — exit retry loop

        except Exception as exc:
            if _attempt == 0 and _is_crumb_error(exc):
                logger.warning(
                    f"get_fundamentals({ticker}) crumb/401 error — resetting session and retrying"
                )
                _reset_yf_crumb()
                continue
            logger.warning(f"get_fundamentals({ticker}) error: {exc}")
            break

    # ── Save to cache ────────────────────────────────────────────────────
    # Do NOT cache if info is empty — likely an auth failure.
    # Caching bad data would cause repeated auth warnings on every subsequent request.
    if not result.get("info"):
        logger.warning(
            f"get_fundamentals({ticker}) — skipping cache write (empty info, possible auth failure)"
        )
    else:
        try:
            def _to_serialisable(v):
                if isinstance(v, pd.DataFrame):
                    if v.empty:
                        return []
                    return json.loads(v.to_json(orient="records", date_format="iso",
                                                default_handler=str))
                if isinstance(v, pd.Series):
                    return v.tolist()
                return v

            cache_data = {k: _to_serialisable(v) for k, v in result.items()}
            cache_file.write_text(
                json.dumps(cache_data, ensure_ascii=False, default=str),
                encoding="utf-8")
            meta_file.write_text(today_str)
            logger.debug("[Cache SAVE] get_fundamentals(%s)", ticker)
        except Exception as exc:
            logger.debug(f"Could not save fundamentals cache for {ticker}: {exc}")

    # ── Also persist to DuckDB fundamentals_cache (background, non-blocking) ───────────
    if getattr(C, "DB_ENABLED", False):
        _bg_args = (ticker, result.copy())
        threading.Thread(
            target=_bg_fund_cache_set,
            args=_bg_args,
            daemon=True,
            name=f"fund_cache_{ticker}",
        ).start()

    return result


def _bg_fund_cache_set(ticker: str, data: dict):
    """Background worker: store fundamentals result in DuckDB cache."""
    try:
        import trader_config as _C
        if not getattr(_C, "DB_ENABLED", False):
            return
        from modules import db
        # Serialize DataFrames to JSON-safe dicts before storing
        safe = {}
        for k, v in data.items():
            if isinstance(v, pd.DataFrame):
                safe[k] = [] if v.empty else json.loads(
                    v.to_json(orient="records", date_format="iso", default_handler=str))
            elif isinstance(v, pd.Series):
                safe[k] = v.tolist()
            else:
                safe[k] = v
        db.fund_cache_set(ticker, safe)
        logger.debug("[data_pipeline] DuckDB fund_cache_set(%s) done", ticker)
    except Exception as exc:
        logger.debug("[data_pipeline] _bg_fund_cache_set(%s) failed: %s", ticker, exc)


# ═══════════════════════════════════════════════════════════════════════════════
# C. pandas_ta — Technical Indicator Computation
# ═══════════════════════════════════════════════════════════════════════════════

# Pre-built Study for batch indicator calculation
# False = tried and failed; None = not tried yet
_MINERVINI_STUDY = None
_STUDY_TRIED = False

def _build_study():
    """Build the pandas_ta Strategy object (if supported by installed version)."""
    global _MINERVINI_STUDY, _STUDY_TRIED
    if not PTA_AVAILABLE or _STUDY_TRIED:
        return
    _STUDY_TRIED = True
    try:
        strategy_cls = getattr(ta, "Strategy", None)
        if strategy_cls is None:
            # pandas_ta version without Strategy — fall back to individual calls
            return
        _MINERVINI_STUDY = strategy_cls(
            name="Minervini",
            description="SEPA Technical Indicators",
            ta=[
                {"kind": "sma",    "length": 50},
                {"kind": "sma",    "length": 150},
                {"kind": "sma",    "length": 200},
                {"kind": "rsi",    "length": 14},
                {"kind": "atr",    "length": 14},
                {"kind": "bbands", "length": 20, "std": 2},
            ],
        )
    except Exception as exc:
        logger.debug(f"pandas_ta Strategy unavailable, using individual calls: {exc}")
        _MINERVINI_STUDY = None


def get_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all Minervini technical indicators on an OHLCV DataFrame.

    Adds columns (if data is sufficient):
      SMA_50, SMA_150, SMA_200
      RSI_14
      ATRr_14  (ATR as ratio / or ATR_14)
      BBL_20_2.0, BBM_20_2.0, BBU_20_2.0, BBB_20_2.0  (Bollinger)
      SMA200_SLOPE     (22-day slope of SMA_200, in % per day)
      SMA150_SLOPE
      ABOVE_SMA50, ABOVE_SMA150, ABOVE_SMA200  (bool)
      SMA50_GT_SMA150, SMA50_GT_SMA200, SMA150_GT_SMA200  (bool)
      PCT_FROM_52W_HIGH, PCT_FROM_52W_LOW  (float)

    Returns the enriched DataFrame.
    """
    if not PTA_AVAILABLE or df is None or df.empty:
        return df

    df = df.copy()
    _build_study()

    # Compute indicators
    try:
        if _MINERVINI_STUDY is not None:
            df.ta.strategy(_MINERVINI_STUDY)
        else:
            # Fallback: compute individually
            df.ta.sma(length=50, append=True)
            df.ta.sma(length=150, append=True)
            df.ta.sma(length=200, append=True)
            df.ta.rsi(length=14, append=True)
            df.ta.atr(length=14, append=True)
            df.ta.bbands(length=20, std=2, append=True)
    except Exception as exc:
        logger.warning(f"pandas_ta computation error: {exc}")
        # Try individual indicators
        for length in [50, 150, 200]:
            col = f"SMA_{length}"
            if col not in df.columns:
                try:
                    df[col] = df["Close"].rolling(length).mean()
                except Exception:
                    pass
        return df

    # Normalise column names (pandas_ta versions vary)
    _rename_ta_columns(df)

    # SMA200 slope (% per day over last 22 trading days)
    if "SMA_200" in df.columns:
        df["SMA200_SLOPE"] = _rolling_slope_pct(df["SMA_200"], 22)
    if "SMA_150" in df.columns:
        df["SMA150_SLOPE"] = _rolling_slope_pct(df["SMA_150"], 22)

    # Boolean trend conditions (for last row / current status)
    close = df["Close"]
    for sma_col, bool_col in [("SMA_50", "ABOVE_SMA50"),
                               ("SMA_150", "ABOVE_SMA150"),
                               ("SMA_200", "ABOVE_SMA200")]:
        if sma_col in df.columns:
            df[bool_col] = close > df[sma_col]

    for col_a, col_b, bool_col in [
        ("SMA_50", "SMA_150", "SMA50_GT_SMA150"),
        ("SMA_50", "SMA_200", "SMA50_GT_SMA200"),
        ("SMA_150", "SMA_200", "SMA150_GT_SMA200"),
    ]:
        if col_a in df.columns and col_b in df.columns:
            df[bool_col] = df[col_a] > df[col_b]

    # 52-week high/low distances
    if len(df) >= 252:
        window_252 = df["Close"].rolling(252)
    else:
        window_252 = df["Close"].expanding()
    hi_252 = window_252.max()
    lo_252 = window_252.min()
    df["HIGH_52W"] = hi_252
    df["LOW_52W"]  = lo_252
    df["PCT_FROM_52W_HIGH"] = (close - hi_252) / hi_252 * 100   # negative = below high
    df["PCT_FROM_52W_LOW"]  = (close - lo_252) / lo_252 * 100   # positive = above low

    return df


def _rename_ta_columns(df: pd.DataFrame):
    """Normalise pandas_ta column names across different versions."""
    rename = {}
    for col in df.columns:
        s = str(col)
        if s.startswith("SMA_"):   # already correct
            pass
        elif s == "ATR_14":
            rename[col] = "ATR_14"
        elif s.startswith("ATRr_"):
            rename[col] = "ATR_14"
        elif s.startswith("BBL_"):
            rename[col] = f"BBL_{s.split('_')[1]}_2.0"
        elif s.startswith("BBM_"):
            rename[col] = f"BBM_{s.split('_')[1]}_2.0"
        elif s.startswith("BBU_"):
            rename[col] = f"BBU_{s.split('_')[1]}_2.0"
        elif s.startswith("BBB_"):
            rename[col] = f"BBB_{s.split('_')[1]}_2.0"
    df.rename(columns=rename, inplace=True)


def _rolling_slope_pct(series: pd.Series, window: int) -> pd.Series:
    """
    Compute the slope of a series as percentage change per day
    over a rolling `window`, using simple linear regression.
    """
    def slope(arr):
        if len(arr) < 2 or np.isnan(arr).any():
            return np.nan
        x = np.arange(len(arr), dtype=float)
        y = np.array(arr, dtype=float)
        slope_val = np.polyfit(x, y, 1)[0]
        base = y[0] if y[0] != 0 else 1.0
        return slope_val / base * 100   # % per day

    return series.rolling(window).apply(slope, raw=True)


# ═══════════════════════════════════════════════════════════════════════════════
# D. Convenience: get historical + technicals in one call
# ═══════════════════════════════════════════════════════════════════════════════

def get_enriched(ticker: str, period: str = "2y",
                 use_cache: bool = True) -> pd.DataFrame:
    """
    Get OHLCV + all technical indicators in one call.
    Returns DataFrame ready for trend template validation and VCP detection.
    """
    df = get_historical(ticker, period=period, use_cache=use_cache)
    if df.empty:
        return df
    return get_technicals(df)


def batch_download_and_enrich(tickers: list, period: str = "2y",
                              progress_cb=None) -> dict:
    """
    Batch-download OHLCV for many tickers, enrich with technicals,
    and save per-ticker parquet cache. Much faster than individual calls.

    Args:
        tickers:     list of ticker strings
        period:      yfinance period string (e.g. "2y")
        progress_cb: optional callable(batch_num, total_batches, msg)

    Returns:
        dict[ticker] -> enriched DataFrame  (only non-empty results)
    """
    today = date.today().isoformat()
    batch_size = getattr(C, "STAGE2_BATCH_SIZE", 50)
    sleep_sec  = getattr(C, "STAGE2_BATCH_SLEEP", 1.5)

    # ── Separate cached vs. uncached tickers ─────────────────────────────
    result = {}
    need_download = []
    for tkr in tickers:
        cache_file = PRICE_CACHE_DIR / f"{tkr.upper()}_{period}.parquet"
        meta_file  = cache_file.with_suffix(".meta")
        if cache_file.exists() and meta_file.exists():
            try:
                cache_date = meta_file.read_text().strip()
                if cache_date == today:
                    df_cached = pd.read_parquet(cache_file)
                    if not df_cached.empty:
                        result[tkr] = get_technicals(df_cached)
                        continue
            except Exception:
                pass
        need_download.append(tkr)

    if not need_download:
        logger.info("[Batch] All %d tickers found in cache", len(tickers))
        return result

    logger.info("[Batch] %d/%d tickers need download (%d cached)",
                len(need_download), len(tickers), len(result))

    # ── Batch download uncached tickers ──────────────────────────────────
    batches = [need_download[i:i + batch_size]
               for i in range(0, len(need_download), batch_size)]
    total_batches = len(batches)

    for bi, batch in enumerate(batches):
        if progress_cb:
            progress_cb(bi + 1, total_batches,
                        f"Downloading batch {bi+1}/{total_batches} ({len(batch)} tickers)")
        try:
            raw = yf.download(
                tickers=batch,
                period=period,
                interval="1d",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if raw is None or raw.empty:
                continue

            # Parse per-ticker DataFrames from batch result
            if len(batch) == 1:
                tkr = batch[0]
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                if raw.index.tzinfo is not None:
                    raw.index = raw.index.tz_localize(None)
                df_t = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if not df_t.empty:
                    # Save to cache
                    try:
                        cache_path = PRICE_CACHE_DIR / f"{tkr.upper()}_{period}.parquet"
                        df_t.to_parquet(cache_path)
                        cache_path.with_suffix(".meta").write_text(today)
                    except Exception:
                        pass
                    result[tkr] = get_technicals(df_t)
            else:
                for tkr in batch:
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            df_t = raw.xs(tkr, axis=1, level=1)
                        else:
                            continue
                        if df_t.index.tzinfo is not None:
                            df_t.index = df_t.index.tz_localize(None)
                        df_t = df_t[["Open", "High", "Low", "Close", "Volume"]].dropna()
                        if df_t.empty or len(df_t) < 50:
                            continue
                        # Save to per-ticker cache
                        try:
                            cache_path = PRICE_CACHE_DIR / f"{tkr.upper()}_{period}.parquet"
                            df_t.to_parquet(cache_path)
                            cache_path.with_suffix(".meta").write_text(today)
                        except Exception:
                            pass
                        result[tkr] = get_technicals(df_t)
                    except Exception:
                        continue

        except Exception as exc:
            logger.warning(f"Batch download error (batch {bi+1}): {exc}")

        # Rate-limit between batches
        if bi < total_batches - 1:
            time.sleep(sleep_sec)

    logger.info("[Batch] Enriched %d/%d tickers total", len(result), len(tickers))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# E. Qullamaggie-specific computed metrics (pure-pandas, no new API calls)
#    All functions operate on the DataFrame returned by get_historical() or
#    get_enriched().  They never call yfinance / finvizfinance directly.
# ═══════════════════════════════════════════════════════════════════════════════

def get_adr(df: pd.DataFrame, period: int = None) -> float:
    """
    Average Daily Range (ADR%) as used by Qullamaggie.
    ADR = average of (High / Low - 1) * 100  over the last N days.

    Qullamaggie's ThinkorSwim formula uses a 14-day window.
    ADR is the primary volatility gate: stocks with ADR < 5% are skipped.

    Args:
        df:     OHLCV DataFrame (from get_historical / get_enriched)
        period: number of daily bars to average (default: QM_ADR_PERIOD = 14)

    Returns:
        ADR as a percentage float, or 0.0 on insufficient data.
    """
    if period is None:
        period = getattr(C, "QM_ADR_PERIOD", 14)
    if df.empty or len(df) < period:
        return 0.0
    recent = df.tail(period)
    daily_ranges = (recent["High"] / recent["Low"] - 1.0) * 100.0
    return float(daily_ranges.mean())


def get_dollar_volume(df: pd.DataFrame, period: int = 20) -> float:
    """
    Average daily dollar volume = average of (Close × Volume) over the last N days.
    Qullamaggie uses $5M+ as a liquidity gate; live trading prefers $10M+.

    Args:
        df:     OHLCV DataFrame
        period: rolling window for the average (default 20 days)

    Returns:
        Average daily dollar volume as a float, or 0.0 on insufficient data.
    """
    if df.empty or len(df) < period:
        return 0.0
    recent = df.tail(period)
    dollar_vols = recent["Close"] * recent["Volume"]
    return float(dollar_vols.mean())


def get_momentum_returns(df: pd.DataFrame) -> dict:
    """
    Compute trailing momentum returns for scanner filters.
    Returns the % price change over 1-month (~22 bars), 3-month (~67 bars),
    and 6-month (~126 bars) lookback windows using actual trading days.

    Qullamaggie's scanner criteria:
        1M ≥ 25%  |  3M ≥ 50%  |  6M ≥ 150%

    Args:
        df: OHLCV DataFrame (needs at least 126 rows for full 6M)

    Returns:
        dict with keys '1m', '3m', '6m' — each value is % return (float)
        Missing periods return None if insufficient data.
    """
    if df.empty:
        return {"1m": None, "3m": None, "6m": None}

    last_close = float(df["Close"].iloc[-1])
    n = len(df)

    def _ret(lookback: int):
        if n <= lookback:
            return None
        past_close = float(df["Close"].iloc[-(lookback + 1)])
        if past_close <= 0:
            return None
        return (last_close / past_close - 1.0) * 100.0

    return {
        "1m":  _ret(22),
        "3m":  _ret(67),
        "6m":  _ret(126),
    }


def get_6day_range_proximity(df: pd.DataFrame) -> dict:
    """
    Check whether the current price is within the 6-day high/low range.
    Qullamaggie's consolidation scanner: price within ±15% of 6-day high/low.

    Args:
        df: OHLCV DataFrame

    Returns:
        dict with keys:
            'high_6d'       : float — rolling 6-day high
            'low_6d'        : float — rolling 6-day low
            'pct_from_high' : float — % distance below 6-day high (positive = below)
            'pct_from_low'  : float — % distance above 6-day low  (positive = above)
            'near_high'     : bool  — within QM_NEAR_HIGH_PCT of 6-day high
            'near_low'      : bool  — within QM_NEAR_LOW_PCT  of 6-day low
    """
    window = getattr(C, "QM_CONSOL_WINDOW_DAYS", 6)
    near_high_pct = getattr(C, "QM_NEAR_HIGH_PCT", 15.0)
    near_low_pct  = getattr(C, "QM_NEAR_LOW_PCT",  15.0)

    if df.empty or len(df) < window:
        return {
            "high_6d": None, "low_6d": None,
            "pct_from_high": None, "pct_from_low": None,
            "near_high": False, "near_low": False,
        }

    recent      = df.tail(window)
    high_6d     = float(recent["High"].max())
    low_6d      = float(recent["Low"].min())
    last_close  = float(df["Close"].iloc[-1])

    pct_from_high = (high_6d - last_close) / high_6d * 100.0 if high_6d > 0 else None
    pct_from_low  = (last_close - low_6d)  / low_6d  * 100.0 if low_6d  > 0 else None

    return {
        "high_6d":       high_6d,
        "low_6d":        low_6d,
        "pct_from_high": pct_from_high,
        "pct_from_low":  pct_from_low,
        "near_high":     (pct_from_high is not None and pct_from_high <= near_high_pct),
        "near_low":      (pct_from_low  is not None and pct_from_low  <= near_low_pct),
    }


def get_ma_alignment(df: pd.DataFrame, ma_periods: list = None) -> dict:
    """
    Calculate SMA values and check MA alignment / surfing conditions.
    Used in Qullamaggie Dimension D (MA alignment star scoring).

    Args:
        df:         OHLCV DataFrame (should have pandas_ta ATR / SMA columns if
                    get_enriched was already called; otherwise computes raw SMAs)
        ma_periods: list of SMA periods to evaluate  (default: [10, 20, 50])

    Returns:
        dict with:
            'sma_{n}'           : current SMA value for each period n
            'sma_{n}_rising'    : bool — SMA has trended up over last 5 bars
            'price_vs_sma_{n}'  : % of current price relative to SMA (+ = above)
            'surfing_20'        : bool — price within QM_SURFING_TOLERANCE_PCT of 20SMA
            'surfing_50'        : bool — price within QM_SURFING_TOLERANCE_PCT of 50SMA
            'all_ma_rising'     : bool — all specified SMAs are rising
            'surfing_ma'        : int  — which MA is being surfed (10/20/50) or 0
    """
    if ma_periods is None:
        ma_periods = getattr(C, "QM_MA_PERIODS", [10, 20, 50])
    tolerance   = getattr(C, "QM_SURFING_TOLERANCE_PCT", 3.0)
    rising_days = getattr(C, "QM_MA_RISING_MIN_DAYS", 5)

    result: dict = {}
    if df.empty or len(df) < max(ma_periods) + rising_days:
        for n in ma_periods:
            result[f"sma_{n}"]          = None
            result[f"sma_{n}_rising"]   = False
            result[f"price_vs_sma_{n}"] = None
        result["surfing_20"]  = False
        result["surfing_50"]  = False
        result["all_ma_rising"] = False
        result["surfing_ma"]  = 0
        return result

    last_close = float(df["Close"].iloc[-1])
    all_rising = True

    for n in ma_periods:
        sma_col = f"SMA_{n}"
        if sma_col in df.columns:
            sma_series = df[sma_col]
        else:
            sma_series = df["Close"].rolling(n).mean()

        sma_val = float(sma_series.iloc[-1]) if not sma_series.empty else None
        if sma_val is None or pd.isna(sma_val):
            result[f"sma_{n}"]          = None
            result[f"sma_{n}_rising"]   = False
            result[f"price_vs_sma_{n}"] = None
            all_rising = False
            continue

        # Check if SMA is rising (current > N-days ago)
        if len(sma_series.dropna()) >= rising_days + 1:
            sma_past = float(sma_series.dropna().iloc[-(rising_days + 1)])
            rising   = sma_val > sma_past
        else:
            rising = False
        if not rising:
            all_rising = False

        pct_vs_sma = (last_close / sma_val - 1.0) * 100.0 if sma_val > 0 else None

        result[f"sma_{n}"]          = round(sma_val, 4)
        result[f"sma_{n}_rising"]   = rising
        result[f"price_vs_sma_{n}"] = round(pct_vs_sma, 2) if pct_vs_sma is not None else None

    # Surfing checks: price within tolerance% of the MA AND MA is rising
    def _surfing(n: int) -> bool:
        pct = result.get(f"price_vs_sma_{n}")
        if pct is None:
            return False
        return abs(pct) <= tolerance and result.get(f"sma_{n}_rising", False)

    surf_10  = _surfing(10)
    surf_20  = _surfing(20)
    surf_50  = _surfing(50)
    result["surfing_20"]    = surf_20
    result["surfing_50"]    = surf_50
    result["all_ma_rising"] = all_rising

    # Identify the primary surfing MA (10 > 20 > 50 in priority)
    if surf_10:
        result["surfing_ma"] = 10
    elif surf_20:
        result["surfing_ma"] = 20
    elif surf_50:
        result["surfing_ma"] = 50
    else:
        result["surfing_ma"] = 0

    return result


def get_higher_lows(df: pd.DataFrame, min_lows: int = None,
                    lookback: int = 40) -> dict:
    """
    Detect whether a stock is building Higher Lows (HL) — Qullamaggie's most
    important consolidation quality indicator (Section 5.4, Criterion ① of ③④).

    Method: identify local swing lows (Low < previous N bars AND next N bars),
    then check if the sequence is ascending.

    Args:
        df:        OHLCV DataFrame
        min_lows:  minimum number of higher lows required   (default: QM_HIGHER_LOWS_MIN)
        lookback:  how many recent bars to search for lows  (default: 40)

    Returns:
        dict with:
            'has_higher_lows' : bool
            'num_lows'        : int — number of swing lows found
            'lows'            : list of (date_str, price) tuples
            'is_ascending'    : bool — all consecutive pairs are ascending
    """
    if min_lows is None:
        min_lows = getattr(C, "QM_HIGHER_LOWS_MIN", 2)

    empty = {"has_higher_lows": False, "num_lows": 0, "lows": [], "is_ascending": False}
    if df.empty or len(df) < lookback:
        return empty

    recent = df.tail(lookback).copy()
    lows   = []
    swing_n = 3  # bars on each side to confirm local minimum

    for i in range(swing_n, len(recent) - swing_n):
        bar_low = float(recent["Low"].iloc[i])
        left    = recent["Low"].iloc[i - swing_n: i]
        right   = recent["Low"].iloc[i + 1: i + swing_n + 1]
        if bar_low <= float(left.min()) and bar_low <= float(right.min()):
            lows.append((str(recent.index[i])[:10], bar_low))

    if len(lows) < min_lows:
        return {"has_higher_lows": False, "num_lows": len(lows), "lows": lows, "is_ascending": False}

    # Check ascending sequence across consecutive pairs
    ascending = all(lows[i][1] < lows[i + 1][1] for i in range(len(lows) - 1))
    return {
        "has_higher_lows": ascending and len(lows) >= min_lows,
        "num_lows":        len(lows),
        "lows":            lows,
        "is_ascending":    ascending,
    }


def get_consolidation_tightness(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Measure how 'tight' the recent consolidation is.
    A tight consolidation (Pennant/Flag) is a key Qullamaggie quality signal
    — K-lines progressively shrinking = supply/demand approaching equilibrium.

    Tightness is measured as:
        avg_daily_range_recent / adr_baseline  (lower = tighter)

    Args:
        df:       OHLCV DataFrame
        lookback: bars to measure recent tightness over (default 20)

    Returns:
        dict with:
            'tightness_ratio'   : float — <1 means tighter than ADR baseline
            'is_tight'          : bool  — ratio < QM_TIGHTNESS_THRESHOLD → very tight
            'avg_body_pct'      : float — avg candle body as % of price
            'range_trend'       : str   — 'contracting' / 'expanding' / 'stable'
    """
    if df.empty or len(df) < lookback + 14:
        return {"tightness_ratio": None, "is_tight": False,
                "avg_body_pct": None, "range_trend": "unknown"}

    threshold = getattr(C, "QM_TIGHTNESS_THRESHOLD", 0.5)

    # ADR baseline from last 14 days (Qullamaggie's formula)
    adr_baseline = get_adr(df, period=14)
    if adr_baseline <= 0:
        adr_baseline = 1.0

    recent = df.tail(lookback)
    daily_ranges = (recent["High"] / recent["Low"] - 1.0) * 100.0
    avg_range    = float(daily_ranges.mean())
    tightness    = avg_range / adr_baseline

    # Body size (measure of indecision / balance between buyers/sellers)
    bodies        = abs(recent["Close"] - recent["Open"]) / recent["Open"] * 100.0
    avg_body      = float(bodies.mean())

    # Range trend: compare first half vs second half of lookback
    half = lookback // 2
    first_half  = float((df["High"].iloc[-lookback:-half] / df["Low"].iloc[-lookback:-half] - 1.0).mean()) * 100
    second_half = float((df["High"].iloc[-half:]          / df["Low"].iloc[-half:]          - 1.0).mean()) * 100
    if second_half < first_half * 0.85:
        range_trend = "contracting"
    elif second_half > first_half * 1.15:
        range_trend = "expanding"
    else:
        range_trend = "stable"

    return {
        "tightness_ratio": round(tightness, 3),
        "is_tight":        tightness < threshold,
        "avg_body_pct":    round(avg_body, 3),
        "range_trend":     range_trend,
    }


def get_atr(df: pd.DataFrame, period: int = None) -> float:
    """
    Average True Range (ATR) — intraday range ONLY, excludes overnight gaps.

    Qullamaggie supplement rule (S1 + S31):
      ATR = average of (High − Low) per bar over the last N days.
      This differs from ADR which uses (High/Low − 1) and INCLUDES gaps.

    Quote: "ATR is not including the overnight gaps.  ADR is including the
            overnight gaps. So the ATR is just the intraday range."

    Key rules using ATR:
      • Entry gate: don't buy if intraday gain > 1× ATR (too late)
      • Ideal entry: intraday gain ≤ 2/3 ATR
      • Stop distance (entry − LOD) should not exceed ATR
      • Ideal stop ≈ 0.5 × ATR

    Args:
        df:     OHLCV DataFrame
        period: rolling window (default: QM_ATR_PERIOD = 14)

    Returns:
        ATR as a dollar float (not %, unlike ADR), or 0.0 on insufficient data.
    """
    if period is None:
        period = getattr(C, "QM_ATR_PERIOD", 14)
    if df.empty or len(df) < period:
        return 0.0
    recent = df.tail(period)
    intraday_ranges = recent["High"] - recent["Low"]
    return float(intraday_ranges.mean())


def get_next_earnings_date(ticker: str) -> "date | None":
    """
    Fetch the next scheduled earnings date for a ticker via yfinance.

    Qullamaggie supplement rule (S2):
      "Never buy stocks within 3 days of earnings — even a 4.5-star setup
       shouldn't be bought the day before earnings."

    Returns:
        datetime.date of next earnings, or None if unavailable / API error.
        Caller should treat None as "unknown" — do not block, but flag warning.
    """
    from datetime import date as _date
    try:
        tkobj = yf.Ticker(ticker)
        cal   = tkobj.calendar
        if cal is None or cal.empty:
            return None
        # yfinance calendar may be a DataFrame or dict depending on version
        if isinstance(cal, pd.DataFrame):
            # Rows are fields, columns may be dates; look for EarningsDate or first column
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"].iloc[0]
            elif cal.shape[0] > 0:
                val = cal.iloc[0, 0]
            else:
                return None
        elif isinstance(cal, dict):
            val = cal.get("Earnings Date", [None])
            if isinstance(val, (list, tuple)) and val:
                val = val[0]
            else:
                return None
        else:
            return None

        if pd.isna(val):
            return None
        if isinstance(val, (pd.Timestamp, datetime)):
            return val.date() if hasattr(val, "date") else _date.today()
        return None
    except Exception as exc:
        logger.debug("[get_next_earnings_date] %s: %s", ticker, exc)
        return None


def get_ma_slope(df: pd.DataFrame, period: int = 20,
                 lookback: int = None) -> dict:
    """
    Calculate moving average slope and approximate angle.

    Qullamaggie supplement rule (S27):
      "The 10, 20, 50-day MAs need to be in a high angle — like they go
       straight up, like they go up in a 45-degree angle. It needs to be
       at least 45°. The faster the better."

    Slope is computed as the average % change per bar of the MA over the
    lookback window, then mapped to an approximate angle via arctan.

    Args:
        df:       OHLCV DataFrame (needs 'SMA_{period}' col or will compute)
        period:   MA period to evaluate (e.g. 10, 20, 50)
        lookback: number of bars for slope calculation (default: QM_MA_SLOPE_LOOKBACK)

    Returns:
        dict with:
            'slope_pct_per_bar' : float  — avg % change per bar in MA
            'angle_approx_deg'  : float  — arctan approximation of angle
            'direction'         : str    — 'rising_fast' / 'rising' / 'flat' / 'declining'
            'passes_45deg'      : bool   — slope meets minimum 45° equivalent
            'is_steep'          : bool   — slope exceeds ideal (faster than 45°)
            'ma_value'          : float  — current MA value (last bar)
    """
    if lookback is None:
        lookback = getattr(C, "QM_MA_SLOPE_LOOKBACK", 20)
    min_slope = getattr(C, "QM_MA_MIN_SLOPE_PCT", 0.25)
    ideal_slope = getattr(C, "QM_MA_SLOPE_IDEAL_PCT", 0.45)

    col = f"SMA_{period}"
    if col in df.columns:
        ma_series = df[col].dropna()
    else:
        if df.empty or len(df) < period:
            return _empty_slope(period)
        ma_series = df["Close"].rolling(period).mean().dropna()

    if len(ma_series) < lookback + 1:
        return _empty_slope(period)

    recent_ma = ma_series.iloc[-lookback:]
    start_val = float(recent_ma.iloc[0])
    if start_val <= 0:
        return _empty_slope(period)

    # Average % change per bar
    pct_changes = recent_ma.pct_change().dropna() * 100.0
    slope_pct   = float(pct_changes.mean())

    # Approximate angle: arctan(slope_pct / 100) converted to degrees
    import math
    angle_deg = math.degrees(math.atan(abs(slope_pct) / 100.0 * 50))
    # Scale factor 50 chosen so that 0.25%/bar ≈ 45° in display terms
    # (this is a visual approximation, not geometric)

    if slope_pct >= ideal_slope:
        direction = "rising_fast"
    elif slope_pct >= min_slope:
        direction = "rising"
    elif slope_pct >= 0.0:
        direction = "flat"
    else:
        direction = "declining"

    ma_val = float(ma_series.iloc[-1]) if not ma_series.empty else 0.0

    return {
        "slope_pct_per_bar": round(slope_pct, 4),
        "angle_approx_deg":  round(angle_deg, 1),
        "direction":         direction,
        "passes_45deg":      slope_pct >= min_slope,
        "is_steep":          slope_pct >= ideal_slope,
        "ma_value":          round(ma_val, 2),
        "ma_period":         period,
    }


def _empty_slope(period: int) -> dict:
    """Return an empty slope dict for insufficient data."""
    return {
        "slope_pct_per_bar": 0.0,
        "angle_approx_deg":  0.0,
        "direction":         "unknown",
        "passes_45deg":      False,
        "is_steep":          False,
        "ma_value":          0.0,
        "ma_period":         period,
    }
