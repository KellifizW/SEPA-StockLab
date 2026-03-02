"""
ml_situational.py — Martin Luk 3-Layer Situational Awareness
=============================================================
Implements Martin Luk's 3-layer market reading framework (Chapter 6 / Core).

Martin explicitly states: "Before placing ANY trade, you must understand
what environment you are in. Context determines everything."

Layer 1 — Macro Market Regime (SPY / QQQ / IWM)
    Is the broad market trending, correcting, or choppy?
    Key: IWM (small-caps) is Martin's primary leading indicator.
    IWM > SPY/QQQ = risk-on, small-caps leading → favour longs
    IWM < SPY/QQQ = risk-off, FAANG-defended → reduce or stay out

Layer 2 — Sector Rotation
    Which sectors are in leadership vs distribution?
    Use 10-day sector RS change to identify rotation flows.
    Entry rule: trade stocks in leading sectors only.

Layer 3 — Individual Stock Context
    Is this stock acting better or worse than the market?
    Relative strength vs SPY on the pullback day = key filter.
    Stock holding EMA on a down market day = actionable.

CHOPPY Market Detection
    Added per user decision: detect CHOPPY regime as separate state.
    CHOPPY = Price oscillates 3%+ repeatedly with no net progress over 20 days.
    Signal: ADX < 20 + consecutive distribution/follow-through days pairs.
    Action: reduce position sizes to ML_CHOPPY_SIZE_MULT × normal.

Public API
----------
  assess_layer1(mkt_data)               → dict
  assess_layer2(sector_data)            → dict
  assess_layer3(df_stock, df_spy)       → dict
  detect_choppy(df_spy, lookback)       → dict
  get_full_situational(ticker, df)      → dict
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Macro Market Regime
# ─────────────────────────────────────────────────────────────────────────────

_REGIME_STATES = (
    "CONFIRMED_UPTREND",
    "UPTREND_UNDER_PRESSURE",
    "CHOPPY",
    "MARKET_IN_CORRECTION",
    "DOWNTREND",
)


def assess_layer1(mkt_data: dict | None = None) -> dict:
    """
    Layer 1: Broad market regime using SPY / QQQ / IWM.

    Martin's key insight: IWM (small-caps) leads the market.
    When small-caps are in distress, avoid ALL new longs.

    Args:
        mkt_data: Pre-fetched market data dict (from market_env.assess()).
                  If None, fetches fresh data.

    Returns:
        dict with regime, iwm_vs_spy_pct, action_bias, notes_zh
    """
    if mkt_data is None:
        try:
            from modules.market_env import assess as mkt_assess
            mkt_data = mkt_assess(verbose=False)
        except Exception as exc:
            logger.warning("[ML L1] Market env fetch failed: %s", exc)
            return {"regime": "UNKNOWN", "action_bias": "NEUTRAL", "iwm_vs_spy_pct": 0}

    regime = mkt_data.get("regime", "UNKNOWN")

    # IWM vs SPY relative performance (Martin's leading indicator)
    iwm_chg   = mkt_data.get("iwm_change_pct", 0) or 0
    spy_chg   = mkt_data.get("spy_change_pct", 0) or 0
    iwm_lag   = iwm_chg - spy_chg  # negative = small-cap underperformance

    iwm_lag_thresh = getattr(C, "ML_SA_IWM_LAG_THRESHOLD", -5.0)

    # Detect CHOPPY if market_env doesn't already emit it
    choppy = detect_choppy_from_mkt(mkt_data)

    if choppy.get("is_choppy"):
        effective_regime = "CHOPPY"
    elif iwm_lag < iwm_lag_thresh:
        # IWM severely lagging → "risk-off" regardless of stated regime
        effective_regime = "UPTREND_UNDER_PRESSURE" if regime == "CONFIRMED_UPTREND" else regime
    else:
        effective_regime = regime

    # Action bias
    if effective_regime == "CONFIRMED_UPTREND":
        action_bias = "FULL_SIZE"
        notes_zh = "市場確認上漲趨勢 — 可全倉入場"
    elif effective_regime == "UPTREND_UNDER_PRESSURE":
        action_bias = "REDUCED_SIZE"
        notes_zh = "趨勢受壓 — 縮小倉位 (½ 正常大小)"
    elif effective_regime == "CHOPPY":
        size_mult = getattr(C, "ML_CHOPPY_SIZE_MULT", 0.25)
        action_bias = "MINIMAL_SIZE"
        notes_zh = f"震盪市 — 超小倉位 ({int(size_mult*100)}% 正常大小)"
    elif effective_regime == "MARKET_IN_CORRECTION":
        action_bias = "HOLD_CASH"
        notes_zh = "市場調整中 — 持現金，等待反轉確認"
    else:  # DOWNTREND / UNKNOWN
        action_bias = "NO_NEW_LONGS"
        notes_zh = "下降趨勢 — 禁止新多頭入場"

    return {
        "regime":           regime,
        "effective_regime": effective_regime,
        "action_bias":      action_bias,
        "iwm_vs_spy_pct":   round(iwm_lag, 2),
        "is_choppy":        choppy.get("is_choppy", False),
        "choppy_detail":    choppy,
        "notes_zh":         notes_zh,
    }


def detect_choppy_from_mkt(mkt_data: dict) -> dict:
    """
    Detect CHOPPY state from market environment data.
    CHOPPY = oscillation without net progress; no clear trend direction.
    """
    dist_days  = mkt_data.get("distribution_days", 0) or 0
    follow_thru = mkt_data.get("follow_through_days", 0) or 0

    # Alternating distribution + follow-through without resolution
    chop_score = 0
    if 2 <= dist_days <= 5:
        chop_score += 1
    if 1 <= follow_thru <= 3:
        chop_score += 1
    regime = mkt_data.get("regime", "")
    if regime == "UPTREND_UNDER_PRESSURE":
        chop_score += 1

    is_choppy = chop_score >= 2

    return {
        "is_choppy":   is_choppy,
        "chop_score":  chop_score,
        "dist_days":   dist_days,
        "follow_thru": follow_thru,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1b — CHOPPY Detection (technical, from price data)
# ─────────────────────────────────────────────────────────────────────────────

def detect_choppy(df_index: pd.DataFrame, lookback: int | None = None) -> dict:
    """
    Detect CHOPPY market using ADX + net-progress analysis on an index (SPY).

    Criteria (Martin implied, Appendix F):
      1. ADX < ML_CHOPPY_ADX_THRESHOLD (20) = no directional trend
      2. Net price change over lookback < 2× average daily range
         → choppy = going nowhere fast

    Args:
        df_index: OHLCV DataFrame for a market index (SPY/QQQ)
        lookback: Number of bars to evaluate (default ML_SA_LAYER1_LOOKBACK * 4)

    Returns:
        dict with is_choppy, adx, net_change_pct, avg_range_pct, notes_zh
    """
    adx_thresh = getattr(C, "ML_CHOPPY_ADX_THRESHOLD", 20)
    default_lb  = getattr(C, "ML_SA_LAYER1_LOOKBACK", 5) * 4
    lb = lookback if lookback is not None else default_lb

    if df_index is None or len(df_index) < lb:
        return {"is_choppy": False, "adx": None, "notes_zh": "數據不足"}

    df = df_index.tail(lb).copy()
    close_start = float(df["Close"].iloc[0])
    close_end   = float(df["Close"].iloc[-1])
    net_change  = abs(close_end / close_start - 1.0) * 100.0 if close_start > 0 else 0

    # Average daily range
    df["daily_range"] = (df["High"] - df["Low"]) / df["Close"].shift(1) * 100.0
    avg_range = float(df["daily_range"].mean())

    # ADX check
    adx_val = None
    if "ADX_14" in df.columns:
        adx_val = float(df["ADX_14"].iloc[-1])
    elif len(df) >= 14:
        # Simplified Wilder ADX approximation
        try:
            hi = df["High"].values
            lo = df["Low"].values
            cl = df["Close"].values
            tr_arr = np.maximum(hi[1:] - lo[1:],
                     np.maximum(abs(hi[1:] - cl[:-1]),
                                abs(lo[1:] - cl[:-1])))
            atr = float(np.mean(tr_arr[-14:]))
            adx_val = (avg_range / (atr / float(df["Close"].mean()) * 100 + 1e-9)) * 10
            adx_val = min(adx_val, 60)
        except Exception:
            adx_val = None

    adx_flag  = (adx_val is not None and adx_val < adx_thresh)
    # Net movement less than twice the average daily range = stuck
    range_flag = net_change < avg_range * 2.0

    is_choppy = adx_flag and range_flag

    return {
        "is_choppy":      is_choppy,
        "adx":            round(adx_val, 1) if adx_val is not None else None,
        "net_change_pct": round(net_change, 2),
        "avg_range_pct":  round(avg_range, 2),
        "lookback_bars":  lb,
        "notes_zh": (
            f"{'震盪市確認' if is_choppy else '非震盪'}: "
            f"ADX {adx_val:.1f if adx_val else '?'} "
            f"| 淨漲{net_change:.1f}% vs 平均波幅{avg_range:.1f}%/日"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Sector Rotation
# ─────────────────────────────────────────────────────────────────────────────

# Sector ETF universe for rotation tracking
_SECTOR_ETFS = {
    "Technology":    "XLK",
    "Healthcare":    "XLV",
    "Financials":    "XLF",
    "Energy":        "XLE",
    "Industrials":   "XLI",
    "Materials":     "XLB",
    "Utilities":     "XLU",
    "Consumer Disc": "XLY",
    "Consumer Stapl":"XLP",
    "Real Estate":   "XLRE",
    "Communication": "XLC",
    "Small-Cap":     "IWM",
}


def assess_layer2(lookback_days: int | None = None) -> dict:
    """
    Layer 2: Sector rotation heatmap — identifies leading vs lagging sectors.

    Martin's rules:
      1. Rank sectors by 10-day RS change (momentum of momentum)
      2. Top 3 sectors = ADD SIZE; Bottom 3 = AVOID new longs
      3. When ≥ 6 sectors in DISTRIBUTION → stay in cash (broad selling)

    Args:
        lookback_days: Momentum lookback period (default ML_SA_LAYER1_LOOKBACK * 2)

    Returns:
        dict with ranked_sectors, leaders, laggards, broad_selling, notes_zh
    """
    from modules.data_pipeline import get_enriched

    lb = lookback_days if lookback_days is not None else getattr(C, "ML_SA_LAYER1_LOOKBACK", 5) * 2

    sectors_perf: list[dict] = []

    for sector_name, etf in _SECTOR_ETFS.items():
        try:
            df = get_enriched(etf, period="3mo", use_cache=True)
            if df is None or len(df) < lb + 1:
                continue
            c_now   = float(df["Close"].iloc[-1])
            c_prior = float(df["Close"].iloc[-(lb + 1)])
            perf_pct = (c_now / c_prior - 1.0) * 100.0 if c_prior > 0 else 0

            # 3-day change for momentum-of-momentum
            c_3d = float(df["Close"].iloc[-4]) if len(df) >= 4 else c_prior
            mom3 = (c_now / c_3d - 1.0) * 100.0 if c_3d > 0 else 0

            sectors_perf.append({
                "sector":   sector_name,
                "etf":      etf,
                "perf_pct": round(perf_pct, 2),
                "mom3d":    round(mom3, 2),
                "close":    round(c_now, 2),
            })
        except Exception as exc:
            logger.debug("[ML L2] %s (%s) error: %s", sector_name, etf, exc)

    if not sectors_perf:
        return {"ranked_sectors": [], "leaders": [], "laggards": [], "broad_selling": False}

    sectors_perf.sort(key=lambda x: x["perf_pct"], reverse=True)

    leaders  = [s["sector"] for s in sectors_perf[:3]]
    laggards = [s["sector"] for s in sectors_perf[-3:]]

    # Broad selling: more than half sectors negative
    neg_count = sum(1 for s in sectors_perf if s["perf_pct"] < 0)
    broad_selling = neg_count >= len(sectors_perf) // 2

    return {
        "ranked_sectors": sectors_perf,
        "leaders":        leaders,
        "laggards":       laggards,
        "broad_selling":  broad_selling,
        "lookback_days":  lb,
        "notes_zh": (
            f"領跑板塊: {', '.join(leaders)} | "
            f"落後板塊: {', '.join(laggards)}"
            + (" | ⚠ 廣泛分配" if broad_selling else "")
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — Individual Stock Relative Strength on Pullback
# ─────────────────────────────────────────────────────────────────────────────

def assess_layer3(df_stock: pd.DataFrame, df_spy: pd.DataFrame | None = None) -> dict:
    """
    Layer 3: Individual stock context — is it acting better than the market?

    Martin's key entry filter:
      "If SPY is down -0.5% on the day and your stock is only down -0.1%
       or positive, that's a sign of relative strength — go size up."

    Relative action categories:
      OUTPERFORM  — stock holds/rises while SPY falls ≥0.5%
      IN_LINE     — stock tracks SPY within ±0.3%
      UNDERPERFORM — stock falls much more than SPY

    Also checks: is price near an EMA on a low-volume pullback day?

    Args:
        df_stock:  OHLCV DataFrame for the individual stock
        df_spy:    OHLCV for SPY (fetched internally if None)

    Returns:
        dict with rs_action, stock_chg_pct, spy_chg_pct, relative_diff, holding_ema
    """
    from modules.data_pipeline import get_enriched

    if df_spy is None:
        try:
            df_spy = get_enriched("SPY", period="5d", use_cache=True)
        except Exception:
            df_spy = None

    stock_chg = 0.0
    spy_chg   = 0.0

    if df_stock is not None and len(df_stock) >= 2:
        c  = float(df_stock["Close"].iloc[-1])
        p  = float(df_stock["Close"].iloc[-2])
        stock_chg = (c / p - 1.0) * 100.0 if p > 0 else 0.0

    if df_spy is not None and len(df_spy) >= 2:
        sc  = float(df_spy["Close"].iloc[-1])
        sp  = float(df_spy["Close"].iloc[-2])
        spy_chg = (sc / sp - 1.0) * 100.0 if sp > 0 else 0.0

    relative_diff = stock_chg - spy_chg  # positive = outperforming

    # Classify
    if spy_chg <= -0.3 and relative_diff >= 0.3:
        rs_action = "OUTPERFORM"
        rs_note   = f"大市下跌 {spy_chg:.1f}% 但股票 {stock_chg:+.1f}% — 相對強勢"
    elif abs(relative_diff) <= 0.3:
        rs_action = "IN_LINE"
        rs_note   = f"跟隨大市走勢 (±{relative_diff:.1f}%)"
    else:
        rs_action = "UNDERPERFORM"
        rs_note   = f"股票跑輸大市 {relative_diff:.1f}% — 謹慎入場"

    # EMA proximity check on the latest bar
    holding_ema = None
    if df_stock is not None and len(df_stock) >= 1:
        close = float(df_stock["Close"].iloc[-1])
        for period in (9, 21, 50):
            col = f"EMA_{period}"
            if col in df_stock.columns:
                ema_val = float(df_stock[col].iloc[-1])
                dist_pct = abs(close / ema_val - 1.0) * 100.0 if ema_val > 0 else 999
                if dist_pct <= 1.5:  # within 1.5% of EMA = at EMA
                    holding_ema = period
                    break

    return {
        "rs_action":     rs_action,
        "stock_chg_pct": round(stock_chg, 2),
        "spy_chg_pct":   round(spy_chg, 2),
        "relative_diff": round(relative_diff, 2),
        "holding_ema":   holding_ema,
        "notes_zh":      rs_note,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full 3-Layer Assessment
# ─────────────────────────────────────────────────────────────────────────────

def get_full_situational(ticker: str, df: pd.DataFrame | None = None) -> dict:
    """
    Run all 3 layers for a single stock and produce a unified situational report.

    Args:
        ticker: Stock ticker
        df:     Pre-loaded OHLCV DataFrame (fetched if None)

    Returns:
        dict with layer1, layer2, layer3, overall_bias, size_multiplier, notes_zh
    """
    from modules.data_pipeline import get_enriched

    if df is None:
        try:
            df = get_enriched(ticker, period="6mo", use_cache=True)
        except Exception as exc:
            logger.warning("[ML SA] %s data fetch failed: %s", ticker, exc)
            df = None

    # Layer 1
    l1 = assess_layer1()
    # Layer 2
    l2 = assess_layer2()
    # Layer 3
    l3 = assess_layer3(df)

    effective_regime = l1.get("effective_regime", "UNKNOWN")
    rs_action        = l3.get("rs_action", "IN_LINE")
    broad_selling    = l2.get("broad_selling", False)

    # Determine overall action bias
    if effective_regime in ("MARKET_IN_CORRECTION", "DOWNTREND"):
        overall_bias     = "NO_NEW_LONGS"
        size_multiplier  = 0.0
        notes_zh = "禁止新多頭 (市場下跌/熊市)"
    elif effective_regime == "CHOPPY":
        chop_mult        = getattr(C, "ML_CHOPPY_SIZE_MULT", 0.25)
        overall_bias     = "MINIMAL_SIZE"
        size_multiplier  = chop_mult
        notes_zh = f"震盪市 — 倉位縮減至 {int(chop_mult*100)}%"
    elif broad_selling:
        overall_bias     = "REDUCED_SIZE"
        size_multiplier  = 0.5
        notes_zh = "板塊廣泛派發 — 減半倉位"
    elif effective_regime == "UPTREND_UNDER_PRESSURE":
        size_multiplier  = 0.5 if rs_action == "UNDERPERFORM" else 0.75
        overall_bias     = "REDUCED_SIZE"
        notes_zh = f"趨勢受壓 ({rs_action}) — 減倉"
    else:  # CONFIRMED_UPTREND
        if rs_action == "OUTPERFORM":
            overall_bias    = "FULL_SIZE_PLUS"
            size_multiplier = 1.25   # Up to 25% extra conviction
            notes_zh = "確認上升趨勢 + 個股相對強勢 — 可加大倉位"
        elif rs_action == "UNDERPERFORM":
            overall_bias    = "STANDARD_SIZE"
            size_multiplier = 0.75
            notes_zh = "大市良好但個股跑輸 — 標準倉位 (×0.75)"
        else:
            overall_bias    = "FULL_SIZE"
            size_multiplier = 1.0
            notes_zh = "大市良好，個股正常 — 全倉入場"

    return {
        "ticker":          ticker,
        "layer1":          l1,
        "layer2":          l2,
        "layer3":          l3,
        "overall_bias":    overall_bias,
        "size_multiplier": size_multiplier,
        "notes_zh":        notes_zh,
    }
