"""
ml_theme_tracker.py — Martin Luk Theme Lifecycle Tracking
==========================================================
Implements Martin Luk's theme-based stock selection framework (Chapter 7).

Martin's thesis: "The biggest winners are always theme-driven. The market
rotates money into narratives: AI chips, GLP-1 drugs, nuclear energy.
Find the theme EARLY, ride the leaders, exit when the theme exhausts."

Theme Lifecycle Stages:
  EARLY     — 1-3 stocks making new highs in the theme; low institutional coverage
  GROWTH    — 4-10 stocks trending; sector ETF making new highs; institutional buying
  MATURE    — >10 stocks trending; media coverage peaks; late entrants appear
  EXHAUSTED — Leaders rolling over; volume drying on up-days; theme losing momentum
  DEAD      — Most theme stocks broken; move on

Stock Selection within Theme:
  1. LEADER  — Highest RS, cleanest EMA structure, first to make highs
  2. FOLLOWER— Mirrors leader with 1-3 week lag; can still be tradeable
  3. LAGGARD — Still making highs when leader is tired; AVOID

Martin's Rule: "Always trade the LEADER. If you can't get the leader,
wait for its pullback. Never buy the 4th or 5th stock in a theme."

Public API
----------
  identify_themes(tickers, scan_results)  → dict[theme, list[dict]]
  classify_theme_lifecycle(theme_stocks)  → dict
  rank_within_theme(theme_stocks, df_map) → list[dict]
  get_theme_report(tickers, scan_results) → dict
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Theme taxonomy — comprehensive keyword map
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: theme_name → list of sector/industry keywords to match
_THEME_KEYWORDS: dict[str, list[str]] = {
    "AI/半導體 AI Chips":        ["SEMICONDUCTOR", "ARTIFICIAL INTEL", "GPU", "CHIPMAKER",
                                    "MICROCHIP", "INTEGRATED CIRCUIT", "FABLESS"],
    "量子計算 Quantum":           ["QUANTUM"],
    "核能/鈾 Nuclear":            ["NUCLEAR", "URANIUM", "RADIOACTIVE"],
    "太陽能/風能 Renewables":     ["SOLAR", "WIND ENERGY", "CLEAN ENERGY", "RENEWABLE"],
    "GLP-1/肥胖 Obesity":        ["GLP", "OBESITY", "DIABETES", "METABOLIC"],
    "生技/BIOTECH":               ["BIOTECHNOLOGY", "BIOPHARMACEUTICAL", "GENOMICS",
                                    "MOLECULAR DIAGNOSTIC"],
    "國防/航太 Defense":          ["AEROSPACE", "DEFENSE", "WEAPONS", "MILITARY"],
    "電動車/電池 EV":             ["ELECTRIC VEHICLE", "EV ", " EV", "BATTERY TECH"],
    "網絡安全 Cybersecurity":     ["CYBERSECURITY", "NETWORK SECURITY", "INFORMATION SECURITY"],
    "雲端/SaaS Cloud":            ["CLOUD COMPUTING", "SAAS", "ENTERPRISE SOFTWARE"],
    "消費/品牌 Consumer":         ["CONSUMER DISCRETIONARY", "APPAREL", "RESTAURANT", "SPECIALTY RETAIL"],
    "金融科技 FinTech":           ["FINANCIAL TECH", "PAYMENT", "CRYPTO", "DIGITAL BANKING"],
    "農業/食品 Agriculture":      ["AGRICULTURE", "FERTILIZER", "FOOD PROCESSING"],
    "醫療器械 MedTech":           ["MEDICAL DEVICE", "MEDICAL INSTRUMENT", "HEALTH TECH"],
}


def _match_theme(industry: str, sector: str) -> str | None:
    """Return the first matching theme label for a given industry/sector string."""
    text = (industry + " " + sector).upper()
    for theme_label, keywords in _THEME_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return theme_label
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Theme Identification from Scan Results
# ─────────────────────────────────────────────────────────────────────────────

def identify_themes(
    scan_results: list[dict] | pd.DataFrame,
    min_stocks: int | None = None,
) -> dict[str, list[dict]]:
    """
    Group scan results by theme and filter for themes with critical mass.

    Martin's rule: "A theme only matters when ≥2 stocks from the same
    sector are simultaneously making moves." (Chapter 7)

    Args:
        scan_results:  Row dicts from ml_screener Stage 3, each with 'ticker',
                       'industry', 'sector', 'ml_star', 'mom_3m'
        min_stocks:    Minimum stocks per theme to report (default ML_THEME_MIN_STOCKS)

    Returns:
        dict keyed by theme_label, value = sorted list of stock dicts
    """
    _min = min_stocks if min_stocks is not None else getattr(C, "ML_THEME_MIN_STOCKS", 2)

    if isinstance(scan_results, pd.DataFrame):
        rows = scan_results.to_dict("records")
    else:
        rows = list(scan_results)

    themes: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        industry = row.get("industry", "") or ""
        sector   = row.get("sector",   "") or ""
        theme    = _match_theme(industry, sector)
        if theme:
            themes[theme].append(row)

    # Filter by min_stocks and sort stocks within each theme by ml_star desc
    result: dict[str, list[dict]] = {}
    for theme_label, stocks in themes.items():
        if len(stocks) >= _min:
            result[theme_label] = sorted(stocks,
                                          key=lambda x: x.get("ml_star", 0),
                                          reverse=True)

    logger.info("[ML Theme] Identified %d themes with ≥%d stocks", len(result), _min)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Theme Lifecycle Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_theme_lifecycle(theme_stocks: list[dict]) -> dict:
    """
    Classify the lifecycle stage of a theme based on stock characteristics.

    Stage model (Martin Chapter 7):
      EARLY     — 1-3 stocks, avg ml_star > 3.5, high momentum, only 1-2 leaders
      GROWTH    — 3-8 stocks, avg star > 3.0, consistent momentum, sector breakouts
      MATURE    — >8 stocks, broader participation, some divergence in momentum
      EXHAUSTED — avg star declining, leaders with poor pullback quality, vol dry-up
      DEAD      — most stocks below key EMAs, ml_star < 2.5 broadly

    Args:
        theme_stocks: list of stock dicts with ml_star, pullback_quality, mom_3m

    Returns:
        dict with stage, stage_zh, confidence, recommendation_zh
    """
    if not theme_stocks:
        return {"stage": "UNKNOWN", "stage_zh": "未知", "confidence": 0}

    n = len(theme_stocks)
    avg_star  = sum(s.get("ml_star", 0) for s in theme_stocks) / n
    avg_mom3m = sum(s.get("mom_3m", 0) or 0 for s in theme_stocks) / n

    # Count quality indicators
    good_pullbacks = sum(1 for s in theme_stocks
                         if s.get("pullback_quality") in ("ideal", "acceptable"))
    broken_count   = sum(1 for s in theme_stocks
                         if s.get("pullback_quality") == "broken")
    high_stars     = sum(1 for s in theme_stocks if s.get("ml_star", 0) >= 3.5)

    # Determine lifecycle stage
    if broken_count > n * 0.5 or avg_star < 2.0:
        stage      = "DEAD"
        stage_zh   = "主題已死"
        rec_zh     = "放棄此主題所有候選"
        confidence = 85

    elif avg_star < 2.5 or broken_count > n * 0.3:
        stage      = "EXHAUSTED"
        stage_zh   = "主題耗盡"
        rec_zh     = "只考慮最強領袖股，避免追入"
        confidence = 75

    elif n > 8 and avg_star >= 2.5:
        stage      = "MATURE"
        stage_zh   = "主題成熟"
        rec_zh     = "已廣泛參與，可交易但注意風險，嚴選領袖"
        confidence = 70

    elif 3 <= n <= 8 and avg_star >= 3.0 and avg_mom3m >= 20:
        stage      = "GROWTH"
        stage_zh   = "主題成長"
        rec_zh     = "最佳交易窗口 — 選Top 2-3領袖股入場"
        confidence = 80

    elif n <= 3 and avg_star >= 3.5 and high_stars >= 1:
        stage      = "EARLY"
        stage_zh   = "主題早期"
        rec_zh     = "提前佈局 — 小倉試水，觀察擴散情況"
        confidence = 65

    else:
        stage      = "GROWTH"
        stage_zh   = "主題成長 (估計)"
        rec_zh     = "普通交易窗口 — 標準篩選"
        confidence = 55

    return {
        "stage":           stage,
        "stage_zh":        stage_zh,
        "n_stocks":        n,
        "avg_star":        round(avg_star, 2),
        "avg_mom_3m":      round(avg_mom3m, 2),
        "good_pullbacks":  good_pullbacks,
        "broken_count":    broken_count,
        "confidence":      confidence,
        "recommendation_zh": rec_zh,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stock Ranking within Theme (Leader / Follower / Laggard)
# ─────────────────────────────────────────────────────────────────────────────

def rank_within_theme(
    theme_stocks: list[dict],
    df_map: dict[str, pd.DataFrame] | None = None,
) -> list[dict]:
    """
    Rank stocks within a theme as LEADER / FOLLOWER / LAGGARD.

    Martin's scoring (Chapter 7):
      1. RS momentum weight (ML_THEME_W_RS)       — who ran first?
      2. Recency of highs (ML_THEME_W_RECENCY)    — who's freshest?
      3. Dollar volume (ML_THEME_W_DOLV)           — who has liquidity?
      4. Weekly EMA trend (ML_THEME_W_WEEKLY)      — who has weekly confirmation?

    Args:
        theme_stocks: list of stock dicts from scan results
        df_map:       optional pre-loaded {ticker: DataFrame} to avoid re-fetching

    Returns:
        Ranked list[dict] with added fields: theme_rank, theme_role
    """
    from modules.data_pipeline import get_enriched

    w_rs      = getattr(C, "ML_THEME_W_RS",      0.4)
    w_recency = getattr(C, "ML_THEME_W_RECENCY",  0.3)
    w_dolv    = getattr(C, "ML_THEME_W_DOLV",     0.2)
    w_weekly  = getattr(C, "ML_THEME_W_WEEKLY",   0.1)

    scored: list[dict] = []
    for stock in theme_stocks:
        ticker  = stock.get("ticker", "")
        mom_3m  = stock.get("mom_3m", 0)  or 0
        mom_1m  = stock.get("mom_1m", 0)  or 0
        ml_star = stock.get("ml_star", 0) or 0

        # RS score (momentum proxy)
        rs_score = min(1.0, (mom_3m / 100.0 + mom_1m / 50.0) / 2.0)

        # Dollar volume score
        price   = stock.get("price", 0)  or 0
        avg_vol = stock.get("avg_vol", 0) or 0
        dolv    = price * avg_vol
        dolv_score = min(1.0, dolv / 50_000_000)  # normalise vs $50M

        # Recency: how many days since the last 52-week high?
        recency_score = 0.5  # default
        weekly_score  = 0.5  # default
        if df_map and ticker in df_map:
            df = df_map[ticker]
        else:
            try:
                df = get_enriched(ticker, period="1y", use_cache=True)
            except Exception:
                df = None

        if df is not None and len(df) >= 20:
            close = df["Close"].values
            high52 = max(df["High"].tail(252))
            last_high_idx = df["High"].tail(252).idxmax()
            days_since_high = len(df) - df.index.get_loc(last_high_idx) - 1
            # Fresher high = higher recency score (≤5 days = 1.0)
            recency_score = max(0.0, 1.0 - days_since_high / 30.0)

            # Weekly EMA trend check
            try:
                from modules.ml_setup_detector import compute_weekly_trend
                df_w = df.resample("W").agg({
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna()
                wt = compute_weekly_trend(df_w)
                wt_label = wt.get("weekly_trend", "")
                if "UPTREND" in wt_label:
                    weekly_score = 1.0
                elif "NEUTRAL" in wt_label:
                    weekly_score = 0.5
                else:
                    weekly_score = 0.0
            except Exception:
                weekly_score = 0.5

        composite = (w_rs * rs_score + w_recency * recency_score +
                     w_dolv * dolv_score + w_weekly * weekly_score)

        stock_copy = dict(stock)
        stock_copy["theme_score"]    = round(composite, 3)
        stock_copy["rs_score"]       = round(rs_score, 3)
        stock_copy["recency_score"]  = round(recency_score, 3)
        stock_copy["weekly_score"]   = round(weekly_score, 3)
        scored.append(stock_copy)

    # Sort by composite score
    scored.sort(key=lambda x: x["theme_score"], reverse=True)

    # Assign role labels
    for i, stock in enumerate(scored):
        if i == 0:
            stock["theme_role"] = "LEADER"
            stock["theme_role_zh"] = "領袖股"
        elif i <= 2:
            stock["theme_role"] = "FOLLOWER"
            stock["theme_role_zh"] = "追隨股"
        else:
            stock["theme_role"] = "LAGGARD"
            stock["theme_role_zh"] = "落後股"
        stock["theme_rank"] = i + 1

    return scored


# ─────────────────────────────────────────────────────────────────────────────
# Full Theme Report
# ─────────────────────────────────────────────────────────────────────────────

def get_theme_report(
    scan_results: list[dict] | pd.DataFrame,
    min_stocks: int | None = None,
) -> dict:
    """
    Produce a complete theme analysis report from scan results.

    Combines: theme identification → lifecycle classification → intra-theme ranking

    Args:
        scan_results: Stage 3 scan output dicts or DataFrame
        min_stocks:   Minimum stocks per theme

    Returns:
        dict with themes (list of theme reports), top_theme, scan_time
    """
    themes_by_label = identify_themes(scan_results, min_stocks=min_stocks)

    theme_reports: list[dict] = []
    for theme_label, stocks in themes_by_label.items():
        lifecycle = classify_theme_lifecycle(stocks)
        ranked    = rank_within_theme(stocks)

        theme_reports.append({
            "theme":         theme_label,
            "lifecycle":     lifecycle,
            "stocks":        ranked,
            "n_stocks":      len(ranked),
            "stage":         lifecycle.get("stage", "UNKNOWN"),
            "stage_zh":      lifecycle.get("stage_zh", ""),
            "avg_star":      lifecycle.get("avg_star", 0),
            "recommendation_zh": lifecycle.get("recommendation_zh", ""),
            "leader":        ranked[0] if ranked else None,
        })

    # Sort themes: GROWTH first, then EARLY, MATURE, EXHAUSTED, DEAD
    _stage_order = {"GROWTH": 0, "EARLY": 1, "MATURE": 2, "EXHAUSTED": 3, "DEAD": 4, "UNKNOWN": 5}
    theme_reports.sort(key=lambda x: (_stage_order.get(x["stage"], 99), -x.get("avg_star", 0)))

    top_theme = theme_reports[0]["theme"] if theme_reports else None

    logger.info("[ML Theme Report] %d themes | Top: %s", len(theme_reports), top_theme)

    return {
        "themes":     theme_reports,
        "top_theme":  top_theme,
        "n_themes":   len(theme_reports),
        "scan_time":  datetime.now().isoformat(timespec="seconds"),
    }
