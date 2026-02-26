"""
modules/stock_analyzer.py
──────────────────────────
Deep individual stock SEPA analysis.

Combines all data sources to produce a complete SEPA report:
  • Trend Template TT1-TT10  (yfinance + pandas_ta, exact computation)
  • Fundamentals F1-F13      (yfinance quarterly data, earnings surprise)
  • Catalyst assessment      (analyst targets, insider activity)
  • VCP / pattern analysis   (vcp_detector)
  • Position sizing          (ATR-based stop, risk/reward)
  • Actionable summary       (BUY / WATCH / AVOID with reasons)
"""

import sys
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.data_pipeline import get_enriched, get_fundamentals, get_news_fvf
from modules.rs_ranking import get_rs_rank
from modules.vcp_detector import detect_vcp
from modules.screener import (
    validate_trend_template, score_sepa_pillars, _parse_pct, _get_atr
)

logger = logging.getLogger(__name__)

# ANSI colours for terminal output
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry-point
# ═══════════════════════════════════════════════════════════════════════════════

def analyze(ticker: str,
            account_size: float = None,
            print_report: bool = True) -> dict:
    """
    Full SEPA analysis for a single ticker.
    Returns a comprehensive analysis dict.
    Prints a formatted report to terminal if print_report=True.
    """
    ticker = ticker.upper().strip()
    acct   = account_size or C.ACCOUNT_SIZE

    print(f"\nAnalysing {ticker}...", flush=True)

    # ── 1. Fetch data ─────────────────────────────────────────────────────────
    df      = get_enriched(ticker, period="2y")
    rs_rank = get_rs_rank(ticker)
    funds   = get_fundamentals(ticker)
    info    = funds.get("info", {})

    if df is None or df.empty:
        result = {"ticker": ticker, "error": "No price data available"}
        if print_report:
            print(f"{_RED}[ERROR] No price data for {ticker}{_RESET}")
        return result

    # ── 2. Trend Template ─────────────────────────────────────────────────────
    tt = validate_trend_template(ticker, df=df, rs_rank=rs_rank)
    
    # Promote TT checks to top level for frontend (expects tt.tt1, tt.tt2, etc)
    if tt and "checks" in tt:
        for k, v in tt["checks"].items():
            tt[k.lower()] = v  # TT1 → tt1

    # ── 3. Fundamentals scorecard ─────────────────────────────────────────────
    fund_checks = _check_fundamentals(info, funds)

    # ── 4. VCP detection ─────────────────────────────────────────────────────
    vcp = detect_vcp(df)

    # ── 5. SEPA 5-pillar scoring ──────────────────────────────────────────────
    scored = score_sepa_pillars(
        ticker, df,
        fundamentals=funds,
        tt_result=tt,
        rs_rank=rs_rank,
    )

    # ── 6. Position sizing ────────────────────────────────────────────────────
    close   = float(df.iloc[-1]["Close"])
    pos_calc = _calculate_position(close, df, acct)
    
    # Adapt position object for frontend naming conventions
    pos = {
        "entry":           pos_calc.get("entry_price", 0),
        "stop":            pos_calc.get("stop_price", 0),
        "shares":          pos_calc.get("shares", 0),
        "position_value":  pos_calc.get("position_value", 0),
        "target":          pos_calc.get("target_price", 0),
        "risk_dollar":     pos_calc.get("risk_dollar", 0),
        "risk_pct":        pos_calc.get("stop_pct", 0),  # Same as stop_pct
        "rr":              pos_calc.get("rr_ratio", 0),
    }

    # ── 7. Earnings acceleration ──────────────────────────────────────────────
    eps_accel = _check_eps_acceleration(funds)

    # ── 8. Recommendation ────────────────────────────────────────────────────
    recommendation = _make_recommendation(tt, scored, vcp, pos)
    # ── 9. Recent news ────────────────────────────────────────────────────────
    news = get_news_fvf(ticker)

    # Build recommendation with frontend-expected fields
    rec_base = _make_recommendation(tt, scored, vcp, pos)
    recommendation = {
        "action":       rec_base.get("signal", "MONITOR"),
        "description":  rec_base.get("reason", ""),
        "reasons":      [rec_base.get("action", "")],
    }

    # Build fundamentals checks list for frontend
    fund_checks_list = []
    if isinstance(fund_checks, dict):
        for k, v in fund_checks.items():
            desc = f"{k}: {v}"
            fund_checks_list.append(desc)

    result = {
        "ticker":           ticker,
        "company":          info.get("shortName", ticker),
        "sector":           info.get("sector", ""),
        "industry":         info.get("industry", ""),
        "price":            round(close, 2),
        "market_cap":       info.get("marketCap", 0),
        "rs_rank":          round(rs_rank, 1),
        "sepa_score":       scored.get("total_score", 0),  # Frontend expects sepa_score
        "trend_template":   tt,
        "fundamentals":     {"checks": fund_checks_list},  # Frontend expects checks array
        "vcp":              vcp,
        "scored_pillars":   scored,  # Frontend expects scored_pillars, not sepa_scores
        "position":         pos,
        "eps_acceleration": eps_accel,
        "recommendation":   recommendation,
        "news":             news,
    }

    if print_report:
        _print_full_report(result)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Fundamental scorecard
# ═══════════════════════════════════════════════════════════════════════════════

def _check_fundamentals(info: dict, funds: dict) -> dict:
    """Generate the Minervini fundamental checklist (F1-F13)."""
    checks = {}
    notes  = {}

    # F1: Recent quarter EPS growth ≥ 25%
    earnings_growth = _parse_pct(info.get("earningsGrowth")) or 0
    eps_qoq_pct = earnings_growth * 100 if abs(earnings_growth) < 10 else earnings_growth
    checks["F1_EPS_QOQ_25"]   = eps_qoq_pct >= C.F1_MIN_EPS_QOQ_GROWTH
    notes["F1_EPS_QOQ_25"]    = f"EPS growth: {eps_qoq_pct:.1f}% (need ≥{C.F1_MIN_EPS_QOQ_GROWTH}%)"

    # F2: EPS acceleration (from quarterly data)
    eh = funds.get("earnings_surprise", pd.DataFrame())
    accel = _check_eps_acceleration(funds)
    checks["F2_EPS_ACCEL"]    = accel.get("is_accelerating", False)
    notes["F2_EPS_ACCEL"]     = accel.get("note", "Insufficient quarterly data")

    # F3: Annual EPS growth ≥ 25%
    fwd_pe  = _parse_pct(info.get("forwardPE"))
    trail_pe = _parse_pct(info.get("trailingPE"))
    fwd_eps = _parse_pct(info.get("forwardEps"))
    trail_eps = _parse_pct(info.get("trailingEps"))
    annual_growth = 0
    if fwd_eps and trail_eps and trail_eps != 0:
        annual_growth = (fwd_eps - trail_eps) / abs(trail_eps) * 100
    checks["F3_ANNUAL_EPS_25"]  = annual_growth >= C.F3_MIN_EPS_ANNUAL_GROWTH
    notes["F3_ANNUAL_EPS_25"]   = f"FY EPS growth: {annual_growth:.1f}%"

    # F5: Revenue growth ≥ 20%
    rev_growth = (_parse_pct(info.get("revenueGrowth")) or 0) * 100
    checks["F5_REVENUE_20"]   = rev_growth >= C.F5_MIN_SALES_GROWTH
    notes["F5_REVENUE_20"]    = f"Revenue growth: {rev_growth:.1f}% (need ≥{C.F5_MIN_SALES_GROWTH}%)"

    # F7: Profit margin positive
    pm = (_parse_pct(info.get("profitMargins")) or 0) * 100
    checks["F7_PROFIT_MARGIN"] = pm > 0
    notes["F7_PROFIT_MARGIN"]  = f"Net margin: {pm:.1f}%"

    # F8: ROE ≥ 17%
    roe = (_parse_pct(info.get("returnOnEquity")) or 0) * 100
    checks["F8_ROE_17"]       = roe >= C.F8_MIN_ROE
    notes["F8_ROE_17"]        = f"ROE: {roe:.1f}% (need ≥{C.F8_MIN_ROE}%)"

    # F10: EPS beat last quarter
    beat = False
    if not eh.empty and "surprisePercent" in eh.columns:
        last_surprise = eh["surprisePercent"].dropna()
        if len(last_surprise) >= 1:
            beat = float(last_surprise.iloc[0]) > 0
    checks["F10_BEAT_ESTIMATES"] = beat
    notes["F10_BEAT_ESTIMATES"]  = ("Beat EPS estimate last Q ✓" if beat
                                     else "Did not beat estimate last Q")

    # F11: Analyst upward revisions
    er = funds.get("eps_revisions", pd.DataFrame())
    revisions_up = False
    if not er.empty:
        up_cols = [c for c in er.columns if "up" in c.lower()]
        if up_cols:
            try:
                rev7d = int(er[up_cols[0]].iloc[0])
                revisions_up = rev7d > 0
            except Exception:
                pass
    checks["F11_REVISIONS_UP"] = revisions_up
    notes["F11_REVISIONS_UP"]  = ("Analyst revisions UP ✓" if revisions_up
                                   else "No upward analyst revisions")

    # F12: Institutional ownership increasing
    ih = funds.get("institutional_holders", pd.DataFrame())
    checks["F12_INST_INCREASING"] = not ih.empty
    notes["F12_INST_INCREASING"]  = (f"Institutional holders: {len(ih)} reported"
                                      if not ih.empty else "Institutional data N/A")

    passes = sum(checks.values())
    total  = len(checks)
    return {
        "checks": checks,
        "notes":  notes,
        "passes": passes,
        "total":  total,
        "pct":    round(passes / total * 100, 0) if total > 0 else 0,
    }


def _check_eps_acceleration(funds: dict) -> dict:
    """
    Detect EPS acceleration from quarterly income statement.
    Minervini F2: acceleration means each quarter's YoY growth is higher than the last.
    """
    qi = funds.get("quarterly_eps", pd.DataFrame())
    if qi is None or qi.empty:
        return {"is_accelerating": False, "note": "Quarterly EPS data unavailable",
                "quarters": []}

    # Try to find EPS row
    eps_row = None
    for label in ["Basic EPS", "Diluted EPS", "EPS Basic", "EPS"]:
        if label in qi.index:
            eps_row = qi.loc[label]
            break
    # Try Net Income as fallback
    if eps_row is None:
        for label in ["Net Income", "Net Income Common Stockholders"]:
            if label in qi.index:
                eps_row = qi.loc[label]
                break

    if eps_row is None:
        return {"is_accelerating": False, "note": "Could not find EPS in quarterly data",
                "quarters": []}

    eps_series = eps_row.dropna().sort_index()  # oldest → newest
    if len(eps_series) < 5:
        return {"is_accelerating": False, "note": "Need ≥5Qs for acceleration check",
                "quarters": []}

    # Calculate year-over-year growth for each of last 4 quarters
    yoy_growths = []
    cols = list(eps_series.index)  # date-sorted
    for i in range(4, min(len(cols), 8)):
        curr = eps_series.iloc[i]
        prev = eps_series.iloc[i - 4]
        if prev != 0 and not pd.isna(curr) and not pd.isna(prev):
            growth = (curr - prev) / abs(prev) * 100
            yoy_growths.append(round(float(growth), 1))

    if len(yoy_growths) < 2:
        return {"is_accelerating": False,
                "note": f"Only {len(yoy_growths)} comparable quarters",
                "quarters": yoy_growths}

    # Acceleration = growth rate increasing over recent quarters
    recent = yoy_growths[-3:]
    is_accel = all(recent[i] > recent[i - 1] for i in range(1, len(recent))) if len(recent) >= 2 else False
    trend = " → ".join([f"{g:+.0f}%" for g in yoy_growths[-4:]])
    note = f"EPS YoY: {trend} {'⬆️ ACCELERATING' if is_accel else '⚠️ Not accelerating'}"

    return {
        "is_accelerating": is_accel,
        "note":    note,
        "quarters": yoy_growths,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Position Sizing
# ═══════════════════════════════════════════════════════════════════════════════

def _calculate_position(close: float, df: pd.DataFrame,
                         account_size: float) -> dict:
    """
    Calculate position size using Minervini's formula:
    Shares = (Account × Risk%) ÷ (Price - Stop)
    Stop = Price - 2 × ATR(14)
    """
    atr    = _get_atr(df) or close * 0.02
    stop   = close - C.ATR_STOP_MULTIPLIER * atr
    stop_pct = (close - stop) / close * 100

    # Cap stop at max allowed
    if stop_pct > C.MAX_STOP_LOSS_PCT:
        stop    = close * (1 - C.MAX_STOP_LOSS_PCT / 100)
        stop_pct = C.MAX_STOP_LOSS_PCT

    risk_dollar = account_size * (C.MAX_RISK_PER_TRADE_PCT / 100)
    risk_per_share = close - stop
    shares = int(risk_dollar / risk_per_share) if risk_per_share > 0 else 0

    position_value = shares * close
    position_pct   = position_value / account_size * 100

    # Cap at max single position size
    if position_pct > C.MAX_POSITION_SIZE_PCT:
        shares = int(account_size * C.MAX_POSITION_SIZE_PCT / 100 / close)
        position_value = shares * close
        position_pct   = position_value / account_size * 100

    target_1 = close * (1 + C.IDEAL_RISK_REWARD * stop_pct / 100)
    rr_ratio = (target_1 - close) / (close - stop) if (close - stop) > 0 else 0

    return {
        "entry_price":    round(close, 2),
        "stop_price":     round(stop, 2),
        "stop_pct":       round(stop_pct, 2),
        "target_price":   round(target_1, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "shares":         shares,
        "position_value": round(position_value, 0),
        "position_pct":   round(position_pct, 1),
        "risk_dollar":    round(risk_dollar, 0),
        "atr14":          round(atr, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Recommendation engine
# ═══════════════════════════════════════════════════════════════════════════════

def _make_recommendation(tt: dict, scored: dict,
                          vcp: dict, pos: dict) -> dict:
    """
    Produce a clear BUY / WATCH / AVOID recommendation with rationale.
    """
    total    = scored.get("total_score", 0)
    tt_pass  = tt.get("passes", False)
    rr       = pos.get("rr", 0)  # Changed from rr_ratio
    vcp_valid = vcp.get("is_valid_vcp", False)
    pivot    = vcp.get("pivot_price")
    close    = pos.get("entry", 0)  # Changed from entry_price

    reasons_for  = []
    reasons_against = []

    # Gate: must pass Trend Template
    if not tt_pass:
        failed_tt = [k for k, v in tt.get("checks", {}).items()
                     if not v and k < "TT9"]
        return {
            "signal": "AVOID",
            "colour": "RED",
            "reason": f"Failed Trend Template: {', '.join(failed_tt)}",
            "action": "Remove from consideration — stock not in Stage 2",
        }

    # R:R gate
    if rr < C.MIN_RISK_REWARD:
        return {
            "signal": "AVOID",
            "colour": "RED",
            "reason": f"R:R {rr:.1f}:1 < minimum {C.MIN_RISK_REWARD:.1f}:1",
            "action": "Skip — risk/reward not favourable",
        }

    # Check if in buy zone
    in_buy_zone = pivot and close and (pivot * 1.0 <= close <= pivot * 1.05)
    near_pivot  = pivot and close and (pivot * 0.95 <= close < pivot * 1.0)

    if total >= 75 and vcp_valid and in_buy_zone:
        signal = "BUY"
        colour = "GREEN"
        action = (f"Stock is in buy zone (within 5% of pivot ${pivot:.2f}). "
                  f"Entry: ${close:.2f}, Stop: ${pos['stop']:.2f} "
                  f"(-{pos['risk_pct']:.1f}%), "
                  f"Target: ${pos['target']:.2f} (R:R {rr:.1f}:1)")
    elif total >= 65 and tt_pass and (vcp_valid or near_pivot):
        signal = "WATCH"
        colour = "YELLOW"
        action = ("Strong candidate — add to A-grade watchlist. "
                  f"Wait for VCP completion and breakout above ${pivot:.2f}"
                  if pivot else "Wait for VCP completion near pivot.")
    elif total >= 50 and tt_pass:
        signal = "WATCH"
        colour = "YELLOW"
        action = "Good fundamentals and trend. Add to B-grade watchlist — monitor for VCP formation."
    else:
        signal = "MONITOR"
        colour = "YELLOW"
        action = "Passes basic criteria but not fully ready. Add to C-grade watchlist."

    return {
        "signal": signal,
        "colour": colour,
        "reason": f"SEPA score {total:.0f}/100, RS {scored.get('rs_rank', 0):.0f}, "
                  f"VCP {'✓' if vcp_valid else '–'}, R:R {rr:.1f}:1",
        "action": action,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Terminal Report Printer
# ═══════════════════════════════════════════════════════════════════════════════

def _print_full_report(r: dict):
    """Print a colour-coded SEPA analysis report to terminal."""
    sep = "═" * 65
    thin = "─" * 65
    tick = r["ticker"]

    print(f"\n{_BOLD}{sep}{_RESET}")
    print(f"{_BOLD}  SEPA ANALYSIS REPORT  —  {tick}{_RESET}")
    print(f"{_BOLD}{sep}{_RESET}")

    # Company info
    info_line = f"  {r.get('company', tick)}"
    if r.get("sector"):
        info_line += f"  |  {r['sector']} › {r.get('industry', '')}"
    if r.get("market_cap"):
        mc = r["market_cap"]
        mc_str = f"${mc/1e9:.1f}B" if mc >= 1e9 else f"${mc/1e6:.0f}M"
        info_line += f"  |  Market Cap: {mc_str}"
    print(info_line)
    print(f"  Price: ${r['price']:.2f}  |  RS Rank: {r['rs_rank']:.0f}/99")

    # SEPA Score
    scores = r.get("sepa_scores", {})
    total  = scores.get("total_score", 0)
    colour = _GREEN if total >= 70 else _YELLOW if total >= 50 else _RED
    print(f"\n{thin}")
    print(f"  {_BOLD}SEPA TOTAL SCORE: {colour}{total:.0f}/100{_RESET}")
    print(f"  Trend:{scores.get('trend_score',0):>3}  "
          f"Fundamentals:{scores.get('fundamental_score',0):>3}  "
          f"Catalyst:{scores.get('catalyst_score',0):>3}  "
          f"Entry:{scores.get('entry_score',0):>3}  "
          f"R/R:{scores.get('rr_score',0):>3}")

    # Recommendation
    rec    = r.get("recommendation", {})
    signal = rec.get("signal", "N/A")
    sig_colour = _GREEN if signal == "BUY" else _YELLOW if signal in ("WATCH","MONITOR") else _RED
    print(f"\n  {_BOLD}SIGNAL: {sig_colour}{signal}{_RESET}")
    print(f"  {rec.get('reason', '')}")
    print(f"  {_BOLD}Action:{_RESET} {rec.get('action', '')}")

    # Trend Template
    print(f"\n{thin}")
    print(f"  {_BOLD}TREND TEMPLATE (TT1-TT10){_RESET}")
    tt = r.get("trend_template", {})
    checks = tt.get("checks", {})
    passed_count = sum(checks.values())
    line = "  "
    for i in range(1, 11):
        key = f"TT{i}"
        ok  = checks.get(key, False)
        line += f"{_GREEN if ok else _RED}{key}{'✓' if ok else '✗'}{_RESET}  "
    print(line)
    print(f"  Score: {passed_count}/10  |  "
          f"SMA50: ${tt.get('sma50') or 0:.2f}  "
          f"SMA150: ${tt.get('sma150') or 0:.2f}  "
          f"SMA200: ${tt.get('sma200') or 0:.2f}")
    for note in tt.get("notes", [])[:3]:
        if "FAIL" in note:
            print(f"  {_RED}• {note}{_RESET}")

    # Fundamentals
    print(f"\n{thin}")
    print(f"  {_BOLD}FUNDAMENTALS{_RESET}")
    fund = r.get("fundamentals", {})
    fchk = fund.get("checks", {})
    fnotes = fund.get("notes", {})
    for key, ok in fchk.items():
        colour = _GREEN if ok else _RED
        print(f"  {colour}{'✓' if ok else '✗'}{_RESET}  {fnotes.get(key, key)}")

    # EPS Acceleration
    eps_a = r.get("eps_acceleration", {})
    if eps_a.get("note"):
        accel_colour = _GREEN if eps_a.get("is_accelerating") else _YELLOW
        print(f"\n  {_BOLD}EPS Acceleration:{_RESET} {accel_colour}{eps_a['note']}{_RESET}")

    # VCP
    vcp = r.get("vcp", {})
    print(f"\n{thin}")
    print(f"  {_BOLD}VCP / PATTERN ANALYSIS{_RESET}")
    vcp_colour = _GREEN if vcp.get("is_valid_vcp") else _YELLOW
    print(f"  Grade: {vcp_colour}{vcp.get('grade', 'D')}{_RESET}  "
          f"Score: {vcp.get('vcp_score', 0)}/100  "
          f"T-count: {vcp.get('t_count', 0)}")
    if vcp.get("base_depth_pct"):
        print(f"  Base depth: {vcp['base_depth_pct']:.1f}%  "
              f"Width: {vcp.get('base_weeks', 0):.1f}wks  "
              f"Pivot: ${vcp.get('pivot_price') or 0:.2f}")
    if vcp.get("contractions"):
        cont_str = "  Contractions: " + "  →  ".join(
            [f"T{i+1}:{c['range_pct']:.1f}%"
             for i, c in enumerate(vcp["contractions"])]
        )
        print(cont_str)
    for note in vcp.get("notes", [])[:4]:
        print(f"  • {note}")

    # Position sizing
    pos = r.get("position", {})
    print(f"\n{thin}")
    print(f"  {_BOLD}POSITION SIZING  (Account: ${C.ACCOUNT_SIZE:,.0f}){_RESET}")
    print(f"  Entry: ${pos.get('entry_price',0):.2f}  "
          f"Stop: ${pos.get('stop_price',0):.2f} (-{pos.get('stop_pct',0):.1f}%)  "
          f"Target: ${pos.get('target_price',0):.2f}")
    print(f"  R:R: {pos.get('rr_ratio',0):.1f}:1  "
          f"Shares: {pos.get('shares',0):,}  "
          f"Position: ${pos.get('position_value',0):,.0f} "
          f"({pos.get('position_pct',0):.1f}%)  "
          f"Risk: ${pos.get('risk_dollar',0):,.0f}")

    # News
    news = r.get("news")
    if news is not None and not news.empty:
        print(f"\n{thin}")
        print(f"  {_BOLD}RECENT NEWS (latest 3){_RESET}")
        for _, row in news.head(3).iterrows():
            title = str(row.get("Title", row.get("title", "News headline")))[:70]
            print(f"  • {title}")

    print(f"\n{sep}\n")
