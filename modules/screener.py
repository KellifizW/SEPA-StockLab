"""
modules/screener.py
────────────────────
Minervini SEPA Three-Stage Screening Funnel

Stage 1 (Coarse)  — finvizfinance: basic fundamental/technical filters
                    → reduces ~8,000 stocks to ~100-300 candidates
Stage 2 (Precise) — yfinance + pandas_ta: validate ALL TT1-TT10 conditions
                    → reduces to ~20-50 stocks
Stage 3 (Score)   — Full SEPA 5-pillar scoring + VCP detection
                    → final ranked watchlist candidates
"""

import sys
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import (
    get_universe, get_enriched, get_fundamentals,
    get_sector_rankings, FVF_AVAILABLE,
    batch_download_and_enrich,
)
from modules.rs_ranking import get_rs_rank, _ensure_rs_loaded
from modules.vcp_detector import detect_vcp

logger = logging.getLogger(__name__)

# ─── Scan progress / cancel (module-level, single-scan-at-a-time) ─────────────
_scan_lock      = threading.Lock()
_scan_cancel    = threading.Event()   # set this to request stop
_scan_progress  = {"stage": "idle", "pct": 0, "msg": "", "ticker": ""}

def set_scan_cancel(event: threading.Event):
    """Register external cancel event from app.py."""
    global _scan_cancel
    _scan_cancel = event

def get_scan_progress() -> dict:
    with _scan_lock:
        return dict(_scan_progress)

def _progress(stage: str, pct: int, msg: str = "", ticker: str = ""):
    with _scan_lock:
        _scan_progress.update({"stage": stage, "pct": pct, "msg": msg, "ticker": ticker})

def _cancelled() -> bool:
    return _scan_cancel.is_set()


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Coarse filter via finvizfinance
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1 finviz filters ─────────────────────────────────────────────────
# IMPORTANT: Per Minervini, Stage 1 is a COARSE filter to reduce ~8000 stocks
# to ~100-300 candidates.  Detailed fundamental validation happens in Stage 2-3.
#
# We INTENTIONALLY exclude fundamental criteria (EPS, ROE, Sales) from Stage 1
# because finviz data can lag or differ from yfinance — causing false negatives.
# Example: ADEA (EPS +104%, ROE 25%, Revenue +53% per yfinance) was excluded
# by finviz fundamental filters, despite being rank #2 in manual analysis.
#
# Minervini's design:
#   Stage 1 → "Is it in a Stage 2 uptrend?" (technical screen only)
#   Stage 2 → "Does it pass TT1-TT8?" (precise trend validation via yfinance)
#   Stage 3 → "How does it score on SEPA 5 pillars?" (fundamentals + VCP + RS)

COARSE_FILTERS = {
    # Price & liquidity
    "Price":          f"Over ${C.MIN_STOCK_PRICE:.0f}",
    "Average Volume": f"Over {C.MIN_AVG_VOLUME // 1000}K",
    "Country":        "USA",

    # Basic technical — above key moving averages (Stage 2 prerequisite)
    "20-Day Simple Moving Average":  "Price above SMA20",
    "50-Day Simple Moving Average":  "Price above SMA50",
    "200-Day Simple Moving Average": "Price above SMA200",
}

# Minimum candidates Stage 1 should yield; if fewer, fall back to broader filter
MIN_STAGE1_CANDIDATES = 50


def run_stage1(custom_filters: dict | None = None,
               verbose: bool = True,
               stage1_source: str | None = None) -> list:
    """
    Stage 1: Coarse screener.  Returns list of ticker strings.

    Source is controlled by C.STAGE1_SOURCE (or overridden by stage1_source param):
      "finviz"      — finvizfinance (default; slow, ~15 min full scan)
      "nasdaq_ftp"  — Free NASDAQ FTP + yfinance price/vol filter (~2-4 min)
    """
    stage1_source = (stage1_source or getattr(C, "STAGE1_SOURCE", "finviz")).lower()

    # ── NASDAQ FTP route ───────────────────────────────────────────────────
    if stage1_source == "nasdaq_ftp":
        if verbose:
            print("\n[Stage 1] Source: NASDAQ FTP + yfinance filter")
            print(f"          Filters: price>${C.MIN_STOCK_PRICE:.0f}, "
                  f"volume>{C.MIN_AVG_VOLUME:,}")
        try:
            from modules.nasdaq_universe import get_universe_nasdaq
            df_nasdaq = get_universe_nasdaq()
            if not df_nasdaq.empty and "Ticker" in df_nasdaq.columns:
                tickers = df_nasdaq["Ticker"].dropna().str.strip().tolist()
                tickers = [t for t in tickers
                           if t and 1 <= len(t) <= 5
                           and t.replace("-", "").isalpha()]
                if verbose:
                    logger.info("[Stage 1] NASDAQ FTP: %d candidates", len(tickers))
                return tickers
        except Exception as e:
            logger.error("[Stage 1] NASDAQ FTP failed (%s), falling back to finvizfinance", e)
        # Fall through to finvizfinance below

    # ── finvizfinance route (default) ──────────────────────────────────────
    filters = {**COARSE_FILTERS}
    if custom_filters:
        filters.update(custom_filters)

    if verbose:
        print("\n[Stage 1] Running coarse finvizfinance screener...")
        print(f"          Filters: {len(filters)} criteria")

    df = get_universe(filters, view="Overview", verbose=False)

    # ── Auto-fallback: if primary filter is too restrictive, broaden it ────
    if df.empty or ("Ticker" in df.columns and len(df) < MIN_STAGE1_CANDIDATES):
        if verbose and not df.empty:
            logger.info("[Stage 1] Only %d results from strict filter (need %d+), "
                        "broadening to technical-only filter...",
                        len(df), MIN_STAGE1_CANDIDATES)
        elif verbose:
            print("[Stage 1] No results -- broadening to technical-only filter...")

        # Broader fallback: technical filters only (no fundamentals)
        # This lets Stage 2 (TT validation) and Stage 3 (SEPA scoring)
        # handle the precise fundamental evaluation per Minervini's design.
        fallback_filters = {
            "Price":          f"Over ${C.MIN_STOCK_PRICE:.0f}",
            "Average Volume": f"Over {C.MIN_AVG_VOLUME // 1000}K",
            "Country":        "USA",
            "200-Day Simple Moving Average": "Price above SMA200",
            "50-Day Simple Moving Average":  "Price above SMA50",
            "20-Day Simple Moving Average":  "Price above SMA20",
        }
        df_broad = get_universe(fallback_filters, view="Overview", verbose=False)
        # Merge: keep original results + add broader results (de-duplicated)
        if not df_broad.empty and "Ticker" in df_broad.columns:
            if df.empty or "Ticker" not in df.columns:
                df = df_broad
            else:
                combined = pd.concat([df, df_broad], ignore_index=True)
                df = combined.drop_duplicates(subset="Ticker", keep="first")

    if df.empty or "Ticker" not in df.columns:
        if verbose:
            print("[Stage 1] Fallback also empty -- using default universe")
        return _default_universe()

    tickers = df["Ticker"].dropna().str.strip().tolist()
    tickers = [t for t in tickers
               if t and 1 <= len(t) <= 5
               and t.replace("-", "").isalpha()]

    # ── OTC filter — strip Pink-Sheet / unlisted stocks ──────────────────
    try:
        from modules.nasdaq_universe import filter_otc
        before = len(tickers)
        tickers = filter_otc(tickers)
        removed = before - len(tickers)
        if removed and verbose:
            logger.info("[Stage 1] Removed %d OTC/unlisted tickers via NASDAQ FTP whitelist", removed)
    except Exception as exc:
        logger.warning("[Stage 1] OTC filter skipped: %s", exc)

    if verbose:
        logger.info("[Stage 1] %d candidates pass coarse filter", len(tickers))
    return tickers


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Precise Trend Template validation (TT1-TT10)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_trend_template(ticker: str,
                             df: pd.DataFrame = None,
                             rs_rank: float = None) -> dict:
    """
    Check all 10 Minervini Trend Template conditions for a single stock.

    Returns dict:
      passes: bool (all mandatory TT1-TT8 satisfied)
      score:  int  (0-10, one point per condition)
      checks: dict {condition_name: bool}
      notes:  list of strings
    """
    checks = {f"TT{i}": False for i in range(1, 11)}
    notes  = []

    if df is None:
        df = get_enriched(ticker, period="2y")

    if df is None or len(df) < 210:
        return {"passes": False, "score": 0, "checks": checks,
                "notes": [f"Insufficient history ({len(df) if df is not None else 0} days)"]}

    last = df.iloc[-1]
    close = float(last["Close"])

    def sma(col):
        """Safe SMA value retrieval, with rolling fallback."""
        if col in df.columns and not pd.isna(last.get(col, np.nan)):
            return float(last[col])
        length = int(col.split("_")[1])
        val = df["Close"].rolling(length).mean().iloc[-1]
        return float(val) if not pd.isna(val) else None

    sma50  = sma("SMA_50")
    sma150 = sma("SMA_150")
    sma200 = sma("SMA_200")

    # ─ TT1: Price > SMA150 ───────────────────────────────────────────────────
    if sma150 and close > sma150:
        checks["TT1"] = True
    else:
        notes.append(f"TT1 FAIL: Price {close:.2f} ≤ SMA150 {f'{sma150:.2f}' if sma150 else 'N/A'}")

    # ─ TT2: Price > SMA200 ───────────────────────────────────────────────────
    if sma200 and close > sma200:
        checks["TT2"] = True
    else:
        notes.append(f"TT2 FAIL: Price {close:.2f} ≤ SMA200 {f'{sma200:.2f}' if sma200 else 'N/A'}")

    # ─ TT3: SMA150 > SMA200 ──────────────────────────────────────────────────
    if sma150 and sma200 and sma150 > sma200:
        checks["TT3"] = True
    else:
        notes.append("TT3 FAIL: SMA150 ≤ SMA200 (not properly aligned)")

    # ─ TT4: SMA200 rising for ≥1 month ───────────────────────────────────────
    sma200_series = df["Close"].rolling(200).mean() if "SMA_200" not in df.columns \
                    else df["SMA_200"]
    sma200_clean  = sma200_series.dropna()
    if len(sma200_clean) >= C.TT4_SMA200_RISING_DAYS + 1:
        past_sma200  = float(sma200_clean.iloc[-(C.TT4_SMA200_RISING_DAYS + 1)])
        now_sma200   = float(sma200_clean.iloc[-1])
        if now_sma200 > past_sma200:
            checks["TT4"] = True
        else:
            notes.append(f"TT4 FAIL: SMA200 not rising ({past_sma200:.2f}→{now_sma200:.2f})")
    else:
        notes.append("TT4: Insufficient data to confirm SMA200 trend")

    # ─ TT5: SMA50 > SMA150 AND SMA50 > SMA200 ───────────────────────────────
    if sma50 and sma150 and sma200 and sma50 > sma150 and sma50 > sma200:
        checks["TT5"] = True
    else:
        notes.append("TT5 FAIL: SMA50 not above both SMA150 and SMA200")

    # ─ TT6: Price > SMA50 ────────────────────────────────────────────────────
    if sma50 and close > sma50:
        checks["TT6"] = True
    else:
        notes.append(f"TT6 FAIL: Price {close:.2f} ≤ SMA50 {f'{sma50:.2f}' if sma50 else 'N/A'}")

    # ─ TT7: Price ≥ 52W Low × 1.25 ───────────────────────────────────────────
    if "LOW_52W" in df.columns:
        low52 = float(df["LOW_52W"].iloc[-1])
    else:
        low52 = float(df["Low"].rolling(min(252, len(df))).min().iloc[-1])
    pct_above_low = (close - low52) / low52 * 100 if low52 > 0 else 0
    if pct_above_low >= C.TT7_MIN_ABOVE_52W_LOW_PCT:
        checks["TT7"] = True
    else:
        notes.append(f"TT7 FAIL: Only {pct_above_low:.1f}% above 52W low "
                     f"(need ≥{C.TT7_MIN_ABOVE_52W_LOW_PCT}%)")

    # ─ TT8: Price within 25% of 52W High ─────────────────────────────────────
    if "HIGH_52W" in df.columns:
        hi52 = float(df["HIGH_52W"].iloc[-1])
    else:
        hi52 = float(df["High"].rolling(min(252, len(df))).max().iloc[-1])
    pct_below_hi = (hi52 - close) / hi52 * 100 if hi52 > 0 else 100
    if pct_below_hi <= C.TT8_MAX_BELOW_52W_HIGH_PCT:
        checks["TT8"] = True
    else:
        notes.append(f"TT8 FAIL: {pct_below_hi:.1f}% below 52W high "
                     f"(max {C.TT8_MAX_BELOW_52W_HIGH_PCT}%)")

    # ─ TT9: RS Rank ≥ 70 (EXTRA — "non-absolute but strongly recommended" per Minervini) ─
    from modules.rs_ranking import RS_NOT_RANKED
    if rs_rank is None:
        rs_rank = get_rs_rank(ticker)
    if rs_rank == RS_NOT_RANKED:
        # Ticker not in RS universe — cannot evaluate TT9.  Do NOT penalise;
        # mark as unknown so Stage 3 scoring can handle it via fundamental data.
        checks["TT9"] = False
        notes.append(f"TT9: RS rank not available (ticker not in RS universe)")
    elif rs_rank >= C.TT9_MIN_RS_RANK:
        checks["TT9"] = True
    else:
        notes.append(f"TT9: RS rank {rs_rank:.0f} (need ≥{C.TT9_MIN_RS_RANK})")

    # ─ TT10: Sector in top third performance ─────────────────────────────────
    # (Assessed at portfolio level — marked True by default in single-stock analysis)
    checks["TT10"] = True    # Updated by run_stage2 using sector rankings

    score = sum(checks.values())
    # TT1-TT8 are the mandatory Minervini conditions
    mandatory = [checks[f"TT{i}"] for i in range(1, 9)]
    passes = all(mandatory)

    if passes and not notes:
        notes.append("[OK] All TT1-TT8 mandatory conditions passed")

    return {
        "ticker":  ticker.upper(),
        "passes":  bool(passes),
        "score":   int(score),
        "checks":  checks,
        "close":   float(round(close, 2)),
        "sma50":   float(round(sma50, 2)) if sma50 else None,
        "sma150":  float(round(sma150, 2)) if sma150 else None,
        "sma200":  float(round(sma200, 2)) if sma200 else None,
        "rs_rank": float(round(rs_rank, 1)) if rs_rank != RS_NOT_RANKED else None,
        "pct_from_52w_high": float(round(-pct_below_hi, 1)),
        "pct_from_52w_low":  float(round(pct_above_low, 1)),
        "notes":   notes,
    }


def run_stage2(tickers: list,
               sector_leaders: set = None,
               verbose: bool = True,
               enriched_map: dict = None,
               shared: bool = False) -> list:
    """
    Stage 2: Run TT1-TT10 precise validation on each ticker.
    Uses batch download + parallel threads for speed.  Returns list of dicts (only passing).
    
    If enriched_map is provided, skip batch download (for combined scanning).
    """
    if verbose:
        print(f"\n[Stage 2] Validating Trend Template (TT1-TT10) "
              f"for {len(tickers)} tickers (batch download + parallel)...")

    # Ensure RS rankings are loaded (from cache or compute)
    _ensure_rs_loaded()

    # NOTE: No pre-filtering by RS.  Per Minervini, TT9 (RS≥70) is an
    # "extra, non-absolute" condition.  We let ALL Stage-1 candidates through
    # to Stage 2 so that TT1-TT8 (mandatory) decide inclusion.
    filtered_tickers = list(tickers)
    total = len(filtered_tickers)

    # ── Batch download all historical data first ─────────────────────────
    if enriched_map is None:
        _progress("Stage 2 -- Trend Template", 34, "Batch-downloading price data...")
        enriched_map = batch_download_and_enrich(
            filtered_tickers, period="2y",
            progress_cb=lambda bi, bt, msg: _progress(
                "Stage 2 -- Trend Template",
                33 + int(bi / bt * 15),
                msg,
            ),
        )
    else:
        if verbose:
            logger.info("[Stage 2] Using pre-downloaded enriched_map (%d records)", len(enriched_map))

    # ── Parallel TT validation using pre-downloaded data ─────────────────
    passing = []
    done    = [0]

    def _validate(ticker: str):
        if _cancelled():
            return None
        try:
            rs = get_rs_rank(ticker)
            df = enriched_map.get(ticker)
            if df is None or df.empty:
                # Fallback to individual download if batch missed this ticker
                df = get_enriched(ticker, period="2y")
            result = validate_trend_template(ticker, df=df, rs_rank=rs)
            if sector_leaders and ticker in sector_leaders:
                result["checks"]["TT10"] = True
            if result["passes"]:
                result["df"] = df
            return result
        except Exception as exc:
            logger.warning(f"Stage 2 error for {ticker}: {exc}")
            return None

    workers = min(getattr(C, "STAGE2_MAX_WORKERS", 16), total)
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = {pool.submit(_validate, t): t for t in filtered_tickers}
        for fut in as_completed(futures):
            if _cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            done[0] += 1
            ticker = futures[fut]
            pct = 48 + int(done[0] / total * 18)
            _progress("Stage 2 -- Trend Template", pct,
                      f"{done[0]}/{total} validated", ticker)
            try:
                res = fut.result()
                if res and res.get("passes"):
                    passing.append(res)
            except Exception as exc:
                logger.warning(f"Stage 2 future error for {ticker}: {exc}")

    if verbose:
        logger.info("[Stage 2] %d/%d pass Trend Template", len(passing), total)
    return passing


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3 — Full SEPA 5-pillar scoring
# ═══════════════════════════════════════════════════════════════════════════════

def score_sepa_pillars(ticker: str, df: pd.DataFrame,
                       fundamentals: dict = None,
                       tt_result: dict = None,
                       rs_rank: float = None) -> dict:
    """
    Compute SEPA 5-pillar score for a single stock.
    Returns dict with pillar scores and total weighted score.
    """
    if fundamentals is None:
        fundamentals = get_fundamentals(ticker)
    if rs_rank is None:
        rs_rank = get_rs_rank(ticker)

    from modules.rs_ranking import RS_NOT_RANKED as _RS_NR
    rs_display = round(rs_rank, 1) if rs_rank != _RS_NR else None

    info = fundamentals.get("info", {})

    # ── Pillar 1: Trend (0-100) ───────────────────────────────────────────────
    trend_score = _score_trend(tt_result, rs_rank)

    # ── Pillar 2: Fundamentals (0-100) ────────────────────────────────────────
    fund_score  = _score_fundamentals(info, fundamentals)

    # ── Pillar 3: Catalyst (0-100) ────────────────────────────────────────────
    cat_score   = _score_catalyst(info, fundamentals)

    # ── Pillar 4: Entry / Pattern (0-100) — VCP ───────────────────────────────
    vcp_result  = detect_vcp(df)
    entry_score = _score_entry(df, vcp_result, info)

    # ── Pillar 5: Risk/Reward (0-100) ─────────────────────────────────────────
    rr_score, rr_ratio, stop_pct, target_pct = _score_risk_reward(df, info, vcp_result)

    # ── Weighted total ────────────────────────────────────────────────────────
    w = C.SEPA_WEIGHTS
    total = (
        trend_score  * w["trend"] +
        fund_score   * w["fundamental"] +
        cat_score    * w["catalyst"] +
        entry_score  * w["entry"] +
        rr_score     * w["risk_reward"]
    )

    last    = df.iloc[-1]
    close   = float(last["Close"])
    atr_val = _get_atr(df)

    return {
        "ticker":           ticker.upper(),
        "total_score":      float(round(total, 1)),
        "trend_score":      int(trend_score),
        "fundamental_score":int(fund_score),
        "catalyst_score":   int(cat_score),
        "entry_score":      int(entry_score),
        "rr_score":         int(rr_score),
        "rs_rank":          rs_display,
        "rr_ratio":         float(round(rr_ratio, 2)),
        "stop_pct":         float(round(stop_pct, 2)),
        "target_pct":       float(round(target_pct, 2)),
        "vcp":              vcp_result,
        "close":            float(round(close, 2)),
        "atr14":            float(round(atr_val, 2)) if atr_val else None,
        "pivot":            vcp_result.get("pivot_price"),
        "tt_checks":        tt_result.get("checks", {}) if tt_result else {},
    }


def _score_trend(tt_result: dict, rs_rank: float) -> int:
    if not tt_result:
        return 0
    base = tt_result["score"] * 8   # 10 checks × 8 pts each = 80 max
    # RS bonus (only when RS is actually calculated, not default sentinel)
    if rs_rank >= 0 and rs_rank >= 90:
        base += 20
    elif rs_rank >= 0 and rs_rank >= 80:
        base += 15
    elif rs_rank >= 0 and rs_rank >= 70:
        base += 10
    elif rs_rank >= 0 and rs_rank >= 60:
        base += 5
    # If RS not available (RS_NOT_RANKED = -1.0): no bonus, no penalty
    return min(int(base), 100)


def _score_fundamentals(info: dict, fundamentals: dict) -> int:
    score = 0

    # F1: EPS growth QoQ ≥ 25%
    # yfinance earningsGrowth is a decimal ratio (0.35 = 35%) — must convert to pct
    _eg = _parse_pct(info.get("earningsGrowth"))
    eps_qoq = round(_eg * 100, 1) if _eg is not None else None
    if eps_qoq is not None and eps_qoq >= C.F1_MIN_EPS_QOQ_GROWTH:
        score += 20
    elif eps_qoq is not None and eps_qoq >= 15:
        score += 10

    # F3: Annual EPS growth (forward estimate; same decimal field, same conversion)
    eps_forward_growth = eps_qoq  # already in pct after conversion above
    if eps_forward_growth is not None and eps_forward_growth >= C.F3_MIN_EPS_ANNUAL_GROWTH:
        score += 15

    # F5: Revenue growth ≥ 20%
    rev_growth = _parse_pct(info.get("revenueGrowth"))
    if rev_growth and rev_growth * 100 >= C.F5_MIN_SALES_GROWTH:
        score += 20
    elif rev_growth and rev_growth * 100 >= 10:
        score += 10

    # F8: ROE ≥ 17%
    roe = _parse_pct(info.get("returnOnEquity"))
    if roe and roe * 100 >= C.F8_MIN_ROE:
        score += 20
    elif roe and roe * 100 >= 10:
        score += 10

    # F7: Profit margin positive and improving
    pm = _parse_pct(info.get("profitMargins"))
    if pm and pm > 0:
        score += 10
    if pm and pm > 0.15:
        score += 5

    # EPS acceleration from quarterly data
    eh = fundamentals.get("earnings_surprise", pd.DataFrame())
    if not eh.empty and "surprisePercent" in eh.columns:
        surprises = eh["surprisePercent"].dropna()
        if len(surprises) >= 2 and surprises.iloc[0] > 0:
            score += 10   # F10: beat estimates last quarter

    return min(score, 100)


def _score_catalyst(info: dict, fundamentals: dict) -> int:
    score = 0

    # Institutional ownership increasing (F12)
    inst_trans = fundamentals.get("institutional_holders", pd.DataFrame())
    if not inst_trans.empty:
        score += 20

    # Analyst consensus (F11 proxy)
    recom_mean = info.get("recommendationMean")   # 1=Strong Buy, 5=Sell
    if recom_mean:
        if recom_mean <= 1.5:
            score += 25
        elif recom_mean <= 2.0:
            score += 20
        elif recom_mean <= 2.5:
            score += 15
        elif recom_mean <= 3.0:
            score += 5

    # Price target upside vs current price
    target_mean = info.get("targetMeanPrice")
    current     = info.get("currentPrice") or info.get("regularMarketPrice")
    if target_mean and current and current > 0:
        upside = (target_mean - current) / current * 100
        if upside >= 30:
            score += 25
        elif upside >= 20:
            score += 20
        elif upside >= 10:
            score += 15
        elif upside >= 5:
            score += 10

    # Insider buying (positive signal)
    it = fundamentals.get("insider_transactions", pd.DataFrame())
    if not it.empty:
        score += 10  # Any insider activity

    return min(score, 100)


def _score_entry(df: pd.DataFrame, vcp_result: dict, info: dict) -> int:
    """Score the entry quality (VCP + proximity to pivot)."""
    score = 0

    # VCP quality
    vcp_score = vcp_result.get("vcp_score", 0)
    score += int(vcp_score * 0.60)   # VCP contributes up to 60 pts

    # Volume dry-up
    if vcp_result.get("vol_dry"):
        score += 15

    # Proximity to pivot (within 5% = buyable zone)
    pivot = vcp_result.get("pivot_price")
    if pivot and df is not None and len(df) > 0:
        close = float(df.iloc[-1]["Close"])
        pct_from_pivot = (close - pivot) / pivot * 100
        if -2 <= pct_from_pivot <= 5:
            score += 25   # In the buy zone
        elif -5 <= pct_from_pivot <= 10:
            score += 10
        elif pct_from_pivot < -2:
            score += 5    # Below pivot but forming

    return min(score, 100)


def _score_risk_reward(df: pd.DataFrame, info: dict,
                       vcp_result: dict) -> tuple:
    """
    Compute R:R ratio and score.
    Returns (score, rr_ratio, stop_pct, target_pct).
    """
    if df is None or df.empty:
        return 0, 0.0, 8.0, 16.0

    close   = float(df.iloc[-1]["Close"])
    atr     = _get_atr(df) or close * 0.02

    # Stop loss: ATR-based (2 × ATR below entry)
    stop_price = close - C.ATR_STOP_MULTIPLIER * atr
    stop_pct   = (close - stop_price) / close * 100

    # Clamp stop to max allowed
    if stop_pct > C.MAX_STOP_LOSS_PCT:
        stop_price = close * (1 - C.MAX_STOP_LOSS_PCT / 100)
        stop_pct   = C.MAX_STOP_LOSS_PCT

    # Target: analyst mean price target or 3× risk
    target_mean = info.get("targetMeanPrice")
    if target_mean and target_mean > close:
        target_price = float(target_mean)
    else:
        target_price = close + (close - stop_price) * 3   # default 3:1

    target_pct = (target_price - close) / close * 100
    risk_amt   = close - stop_price
    reward_amt = target_price - close
    rr_ratio   = reward_amt / risk_amt if risk_amt > 0 else 0

    if rr_ratio >= 3.0:
        score = 100
    elif rr_ratio >= 2.0:
        score = 70
    elif rr_ratio >= 1.5:
        score = 40
    else:
        score = 0

    return score, rr_ratio, stop_pct, target_pct


def _get_atr(df: pd.DataFrame) -> Optional[float]:
    for col in ["ATR_14", "ATRr_14"]:
        if col in df.columns:
            val = df[col].dropna()
            if not val.empty:
                return float(val.iloc[-1])
    return None


def _parse_pct(val) -> Optional[float]:
    """Parse a value that might be a decimal ratio or integer percentage."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan(custom_filters: dict = None,
             refresh_rs: bool = False,
             verbose: bool = True,
             stage1_source: str = None) -> pd.DataFrame:
    """
    Run the full 3-stage SEPA scan.
    Returns a ranked DataFrame of candidates with SEPA scores.
    Respects the module-level _scan_cancel event and emits _scan_progress updates.
    """
    import time as _time
    _t_scan_start = _time.perf_counter()

    def _elapsed(since=None):
        """Return elapsed seconds since `since` (default: scan start)."""
        return _time.perf_counter() - (since if since is not None else _t_scan_start)

    _progress("Starting scan...", 0, "Initialising")
    _scan_cancel.clear()   # reset cancel flag for fresh run

    print("\n" + "=" * 60)
    print("  MINERVINI SEPA SCAN  --  Three-Stage Screening Funnel")
    print("=" * 60)

    # -- Ensure RS rankings are ready -----------------------------------------
    _progress("Stage 1 -- RS Rankings", 5, "Building / loading RS cache...")
    _t0 = _time.perf_counter()
    _ensure_rs_loaded(force_refresh=refresh_rs)
    logger.info("[Timing] RS load: %.1fs", _elapsed(_t0))
    if _cancelled():
        return pd.DataFrame()

    # -- Load sector rankings --------------------------------------------------
    sector_df = get_sector_rankings("Sector")
    sector_leaders = _get_sector_leaders(sector_df)

    # -- Stage 1 ---------------------------------------------------------------
    _progress("Stage 1 -- Coarse Filter", 15, "Querying finvizfinance screener...")
    _t1 = _time.perf_counter()
    s1_tickers = run_stage1(custom_filters, verbose=verbose, stage1_source=stage1_source)
    logger.info("[Timing] Stage 1: %.1fs -> %d candidates", _elapsed(_t1), len(s1_tickers))
    if _cancelled():
        return pd.DataFrame()
    if not s1_tickers:
        print("[ERROR] Stage 1 returned no tickers")
        return pd.DataFrame()
    _progress("Stage 1 -- Coarse Filter", 33, f"{len(s1_tickers)} candidates")

    # -- Stage 2 ---------------------------------------------------------------
    _t2 = _time.perf_counter()
    s2_results = run_stage2(s1_tickers, sector_leaders, verbose=verbose)
    logger.info("[Timing] Stage 2: %.1fs -> %d passed TT", _elapsed(_t2), len(s2_results))
    if _cancelled():
        # Return partial results if anything passed Stage 2
        if not s2_results:
            return pd.DataFrame()
    elif not s2_results:
        print("[Stage 2] No tickers passed Trend Template")
        return pd.DataFrame()

    # -- Stage 3 ---------------------------------------------------------------
    total3 = len(s2_results)
    _progress("Stage 3 -- SEPA Scoring", 66, f"Scoring {total3} qualifying stocks...")
    if verbose:
        print(f"\n[Stage 3] SEPA 5-pillar scoring for {total3} qualifying stocks "
              f"(parallel, {getattr(C, 'STAGE3_MAX_WORKERS', 6)} workers)...")

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3 worker — extracted for reusability (combined scanning)
# ═══════════════════════════════════════════════════════════════════════════════

def run_stage3(s2_results: list,
               verbose: bool = True,
               shared: bool = False) -> pd.DataFrame:
    """
    Stage 3: Score all Stage 2 passing tickers using SEPA 5-pillar rubric.
    Returns ranked DataFrame, filtered by min_score and top_n.
    
    shared=True: suppress progress updates (used in combined scanning).
    """
    if not s2_results:
        return pd.DataFrame()
    
    total3 = len(s2_results)
    if not shared:
        _progress("Stage 3 -- SEPA Scoring", 66, f"Scoring {total3} qualifying stocks...")
    if verbose:
        print(f"\n[Stage 3] SEPA 5-pillar scoring for {total3} qualifying stocks "
              f"(parallel, {getattr(C, 'STAGE3_MAX_WORKERS', 6)} workers)...")

    rows = []
    done3 = [0]
    _rows_lock = threading.Lock()

    def _score_one(tt_result):
        """Score a single ticker (thread-safe)."""
        if _cancelled():
            return None
        ticker = tt_result["ticker"]
        df     = tt_result.get("df")
        try:
            fundamentals = get_fundamentals(ticker)
            scored = score_sepa_pillars(
                ticker, df,
                fundamentals=fundamentals,
                tt_result=tt_result,
                rs_rank=tt_result["rs_rank"],
            )
            # Enrich with company info
            info = fundamentals.get("info", {})
            scored["company"]  = info.get("shortName", ticker)
            scored["sector"]   = info.get("sector", "")
            scored["industry"] = info.get("industry", "")
            scored["market_cap"] = info.get("marketCap", 0)
            scored["tt_score"] = tt_result["score"]
            vcp = scored.get("vcp") or {}
            scored["vcp_grade"]     = vcp.get("grade", "D")
            scored["vcp_score_raw"] = vcp.get("vcp_score", 0)
            scored["t_count"]       = vcp.get("t_count", 0)
            pivot_price = vcp.get("pivot_price", None)
            if pivot_price is None and df is not None and not df.empty and "High" in df.columns:
                recent_high = pd.to_numeric(df["High"], errors="coerce").dropna().tail(20)
                if not recent_high.empty:
                    pivot_price = float(recent_high.max())
            scored["pivot"]         = float(round(pivot_price, 2)) if pivot_price is not None else None
            # Strip heavy nested dicts -- prevents _clean() recursion issues
            scored.pop("vcp", None)
            scored.pop("df", None)
            return scored
        except Exception as exc:
            logger.warning(f"Stage 3 error for {ticker}: {exc}")
            return None

    stage3_workers = min(getattr(C, "STAGE3_MAX_WORKERS", 6), total3)
    with ThreadPoolExecutor(max_workers=max(stage3_workers, 1)) as pool:
        futures = {pool.submit(_score_one, tt): tt["ticker"] for tt in s2_results}
        for fut in as_completed(futures):
            if _cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            done3[0] += 1
            ticker = futures[fut]
            if not shared:
                pct = 66 + int(done3[0] / total3 * 34)
                _progress("Stage 3 -- SEPA Scoring", pct,
                          f"[{done3[0]}/{total3}] Scoring {ticker}", ticker)
            if verbose:
                print(f"  [{done3[0]}/{total3}] Scoring {ticker}...", end="\r")
            try:
                scored = fut.result()
                if scored:
                    with _rows_lock:
                        rows.append(scored)
            except Exception as exc:
                logger.warning(f"Stage 3 future error for {ticker}: {exc}")

    if not rows:
        if verbose:
            print("[Stage 3] No stocks scored successfully")
        if not shared:
            _progress("Complete", 100, "No stocks passed all filters")
        return pd.DataFrame()

    df_all = pd.DataFrame(rows)
    df_all = df_all.sort_values("total_score", ascending=False).reset_index(drop=True)
    df_all["rank"] = df_all.index + 1

    # ── Quality gate: minimum score + top-N cap ──────────────────────────
    total_scored = len(df_all)
    min_score = getattr(C, "SCAN_MIN_SCORE", 50.0)
    top_n     = getattr(C, "SCAN_TOP_N", 100)
    df_out = df_all[df_all["total_score"] >= min_score].head(top_n).reset_index(drop=True)
    
    if verbose:
        logger.info("[Stage 3] %d/%d pass scoring gate (min_score=%.0f)", 
                    len(df_out), total_scored, min_score)
        print(f"\n[Final] {len(df_out)}/{total_scored} qualify (score ≥ {min_score:.0f})")
    
    if not shared:
        _progress("Complete", 100, f"Scan done: {len(df_out)} final candidates")
    
    return df_out


# ═════════════════════════════════════════════════════════════════════════════════
# ═════════════════════════════════════════════════════════════════════════════════

    # -- Stage 3 ---------------------------------------------------------------
    _t3 = _time.perf_counter()
    df_out = run_stage3(s2_results, verbose=verbose, shared=False)
    logger.info("[Timing] Stage 3: %.1fs -> %d stocks scored", _elapsed(_t3), len(df_out))
    
    logger.info("[Timing] Total scan: %.1fs", _elapsed())
    logger.info("[Stage 3] %d stocks in final results", len(df_out))
    print("=" * 60)
    _progress("Complete", 100, f"{len(df_out)} stocks ranked (from {len(s2_results)} passed TT)")
    
    # Return tuple: (passed quality gate, all Stage 2 passing stocks)
    return df_out, pd.DataFrame(s2_results) if s2_results else pd.DataFrame()






# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_sector_leaders(sector_df: pd.DataFrame, top_pct: float = 0.35) -> set:
    """Return set of leading sector names (top 35% by performance)."""
    if sector_df.empty:
        return set()
    perf_col = next((c for c in sector_df.columns
                     if "month" in c.lower() or "perf" in c.lower()), None)
    if not perf_col:
        return set(sector_df.iloc[:, 0].tolist())
    sector_df = sector_df.copy()
    sector_df[perf_col] = pd.to_numeric(
        sector_df[perf_col].astype(str).str.replace("%", ""), errors="coerce")
    sector_df = sector_df.dropna(subset=[perf_col]).sort_values(perf_col, ascending=False)
    top_n    = max(1, int(len(sector_df) * top_pct))
    return set(sector_df.iloc[:top_n, 0].tolist())


def _default_universe() -> list:
    """Last-resort universe of quality growth stocks."""
    return [
        "NVDA", "AVGO", "AMD", "AAPL", "MSFT", "GOOGL", "META", "AMZN",
        "TSLA", "LLY", "NVO", "ABBV", "UNH", "TMO", "ISRG", "REGN",
        "V", "MA", "AXP", "GS", "JPM",
        "ANET", "FTNT", "CRWD", "PANW", "ZS", "SNOW", "DDOG", "MDB",
        "COST", "WMT", "LRCX", "KLAC", "AMAT", "TER", "ONTO",
        "TTD", "CELH", "DUOL", "BILL",
    ]
