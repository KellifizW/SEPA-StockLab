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


# ═══════════════════════════════════════════════════════════════════════════════
# A. finvizfinance — Screener & Snapshots
# ═══════════════════════════════════════════════════════════════════════════════

def get_universe(filters_dict: dict, view: str = "Overview",
                 verbose: bool = True) -> pd.DataFrame:
    """
    Coarse screener via finvizfinance.
    Returns a DataFrame with Ticker + metadata columns.
    Falls back to empty DataFrame if network error.
    """
    if not FVF_AVAILABLE:
        logger.error("finvizfinance not available.")
        return pd.DataFrame()
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

    # Download from yfinance
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
        logger.warning(f"get_historical({ticker}) error: {exc}")
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


def get_fundamentals(ticker: str) -> dict:
    """
    Comprehensive fundamental data via yfinance.
    Returns a single dict with keys from multiple data sources:
      info, quarterly_eps, earnings_surprise, eps_revisions,
      institutional_holders, insider_transactions, analyst_targets
    """
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
    try:
        tkr = yf.Ticker(ticker)

        # .info — price, valuation, margins, ROE, SMA50/200, etc.
        try:
            info = tkr.info or {}
            result["info"] = info
        except Exception:
            pass

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

    except Exception as exc:
        logger.warning(f"get_fundamentals({ticker}) error: {exc}")

    return result


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
