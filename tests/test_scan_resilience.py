"""
tests/test_scan_resilience.py
─────────────────────────────
Resilience tests for scan-time data degradation handling.

Covers:
  • get_fundamentals() stale-cache fallback when live yfinance info is empty
  • run_stage3() fairness shuffle to avoid persistent alphabetical bias
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C


def test_get_fundamentals_uses_stale_cache_when_live_empty(tmp_path, monkeypatch):
    from modules import data_pipeline as dp

    ticker = "ZZZZ"
    cache_file = tmp_path / f"{ticker}_fundamentals.json"
    meta_file = tmp_path / f"{ticker}_fundamentals.fmeta"

    stale_payload = {
        "ticker": ticker,
        "info": {
            "shortName": "Stale Demo",
            "marketCap": 123456,
            "returnOnEquity": 0.21,
        },
        "earnings_surprise": [{"surprisePercent": 5.0}],
        "institutional_holders": [],
        "insider_transactions": [],
    }
    cache_file.write_text(json.dumps(stale_payload), encoding="utf-8")
    meta_file.write_text((date.today() - timedelta(days=3)).isoformat(), encoding="utf-8")

    monkeypatch.setattr(dp, "PRICE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(C, "DB_ENABLED", False)
    monkeypatch.setattr(C, "FUNDAMENTALS_CACHE_DAYS", 1)
    monkeypatch.setattr(C, "FUNDAMENTALS_USE_STALE_FALLBACK", True)
    monkeypatch.setattr(C, "FUNDAMENTALS_STALE_FALLBACK_DAYS", 7)
    monkeypatch.setattr(C, "YFINANCE_MAX_RETRIES", 1)

    class _FakeTicker:
        def __init__(self, _ticker):
            self.info = {}

        def get_earnings_history(self):
            return []

        def get_institutional_holders(self):
            return []

        def get_insider_transactions(self):
            return []

    monkeypatch.setattr(dp.yf, "Ticker", _FakeTicker)

    out = dp.get_fundamentals(ticker, use_cache=True, scan_mode=True)

    assert out.get("info", {}).get("shortName") == "Stale Demo"
    assert out.get("fundamentals_quality") == "stale"
    assert out.get("fundamentals_source") == "cache_stale"
    assert out.get("stale_age_days") == 3


def test_stage3_shuffle_changes_processing_order(monkeypatch):
    from modules import screener

    processed = []

    monkeypatch.setattr(C, "SEPA_STAGE3_SHUFFLE", True)
    monkeypatch.setattr(C, "SEPA_STAGE3_SHUFFLE_SEED", 123)
    monkeypatch.setattr(C, "STAGE3_MAX_WORKERS", 1)  # deterministic processing order
    monkeypatch.setattr(C, "SCAN_MIN_SCORE", 0.0)
    monkeypatch.setattr(C, "SCAN_TOP_N", 1000)

    def _fake_get_fundamentals(ticker, scan_mode=True):
        processed.append(ticker)
        return {"info": {"shortName": ticker}}

    def _fake_score(ticker, df, fundamentals=None, tt_result=None, rs_rank=None):
        return {
            "ticker": ticker,
            "total_score": 60.0,
            "trend_score": 60,
            "fundamental_score": 60,
            "catalyst_score": 60,
            "entry_score": 60,
            "rr_score": 60,
            "rs_rank": rs_rank,
            "rr_ratio": 2.0,
            "stop_pct": 5.0,
            "target_pct": 10.0,
            "vcp": {"grade": "C", "vcp_score": 50, "t_count": 1, "pivot_price": 11.0},
            "close": 10.0,
            "atr14": 1.0,
            "pivot": 11.0,
            "tt_checks": {},
        }

    monkeypatch.setattr(screener, "get_fundamentals", _fake_get_fundamentals)
    monkeypatch.setattr(screener, "score_sepa_pillars", _fake_score)

    df = pd.DataFrame({"High": [10.0, 10.5], "Close": [9.8, 10.0]})
    s2_results = [
        {"ticker": "AAAA", "df": df, "rs_rank": 80.0, "score": 8},
        {"ticker": "BBBB", "df": df, "rs_rank": 81.0, "score": 8},
        {"ticker": "CCCC", "df": df, "rs_rank": 82.0, "score": 8},
        {"ticker": "DDDD", "df": df, "rs_rank": 83.0, "score": 8},
        {"ticker": "EEEE", "df": df, "rs_rank": 84.0, "score": 8},
    ]

    out = screener.run_stage3(s2_results, verbose=False, shared=True)

    expected = [r["ticker"] for r in s2_results]
    shuffled = expected.copy()
    random.Random(123).shuffle(shuffled)

    assert not out.empty
    assert processed == shuffled
    assert processed != expected


def test_get_fundamentals_uses_finviz_fallback_when_live_empty(monkeypatch):
    from modules import data_pipeline as dp

    ticker = "AAPL"

    # No filesystem stale cache for this test path.
    monkeypatch.setattr(C, "DB_ENABLED", False)
    monkeypatch.setattr(C, "FUNDAMENTALS_USE_STALE_FALLBACK", False)
    monkeypatch.setattr(C, "FUNDAMENTALS_ENABLE_FINVIZ_FALLBACK", True)
    monkeypatch.setattr(dp, "FVF_AVAILABLE", True)

    class _FakeTicker:
        def __init__(self, _ticker):
            self.info = {}

        def get_earnings_history(self):
            return []

        def get_institutional_holders(self):
            return []

        def get_insider_transactions(self):
            return []

    monkeypatch.setattr(dp.yf, "Ticker", _FakeTicker)

    monkeypatch.setattr(
        dp,
        "get_snapshot",
        lambda _t: {
            "EPS this Q": "35%",
            "Sales Q/Q": "24%",
            "ROE": "21%",
            "Profit Margin": "18%",
            "Recom": "1.8",
            "Target Price": "250",
            "Price": "200",
            "Market Cap": "2.8T",
            "Inst Own": "74%",
        },
    )

    out = dp.get_fundamentals(ticker, use_cache=False, scan_mode=True)

    assert out.get("fundamentals_source") == "finviz_live"
    assert out.get("fundamentals_quality") == "fallback"
    assert out.get("info", {}).get("earningsGrowth") == 0.35
    assert out.get("info", {}).get("revenueGrowth") == 0.24
    assert out.get("info", {}).get("returnOnEquity") == 0.21
    assert out.get("info", {}).get("targetMeanPrice") == 250.0
    assert out.get("info", {}).get("currentPrice") == 200.0
    assert not out.get("institutional_holders", pd.DataFrame()).empty


def test_get_fundamentals_uses_sec_finviz_composite_fallback(monkeypatch):
    from modules import data_pipeline as dp

    ticker = "MSFT"

    monkeypatch.setattr(C, "DB_ENABLED", False)
    monkeypatch.setattr(C, "FUNDAMENTALS_USE_STALE_FALLBACK", False)

    class _FakeTicker:
        def __init__(self, _ticker):
            self.info = {}

        def get_earnings_history(self):
            return []

        def get_institutional_holders(self):
            return []

        def get_insider_transactions(self):
            return []

    monkeypatch.setattr(dp.yf, "Ticker", _FakeTicker)

    sec_fb = {
        "ticker": ticker,
        "info": {"earningsGrowth": 0.28, "revenueGrowth": 0.18},
        "quarterly_eps": pd.DataFrame(),
        "earnings_surprise": pd.DataFrame(),
        "eps_revisions": pd.DataFrame(),
        "eps_trend": pd.DataFrame(),
        "institutional_holders": pd.DataFrame(),
        "insider_transactions": pd.DataFrame(),
        "analyst_targets": pd.DataFrame(),
        "quarterly_revenue": pd.DataFrame(),
        "fundamentals_source": "sec_live",
        "fundamentals_quality": "fallback",
        "stale_age_days": None,
    }
    finviz_fb = {
        "ticker": ticker,
        "info": {"returnOnEquity": 0.32, "targetMeanPrice": 520.0, "currentPrice": 500.0},
        "quarterly_eps": pd.DataFrame(),
        "earnings_surprise": pd.DataFrame(),
        "eps_revisions": pd.DataFrame(),
        "eps_trend": pd.DataFrame(),
        "institutional_holders": pd.DataFrame([{"holder": "finviz_snapshot", "instOwn": 0.74}]),
        "insider_transactions": pd.DataFrame(),
        "analyst_targets": pd.DataFrame(),
        "quarterly_revenue": pd.DataFrame(),
        "fundamentals_source": "finviz_live",
        "fundamentals_quality": "fallback",
        "stale_age_days": None,
    }

    monkeypatch.setattr(dp, "_get_sec_fundamentals_fallback", lambda _t: sec_fb)
    monkeypatch.setattr(dp, "_get_finviz_fundamentals_fallback", lambda _t: finviz_fb)

    out = dp.get_fundamentals(ticker, use_cache=False, scan_mode=True)

    assert out.get("fundamentals_source") == "sec_finviz_live"
    assert out.get("info", {}).get("earningsGrowth") == 0.28
    assert out.get("info", {}).get("revenueGrowth") == 0.18
    assert out.get("info", {}).get("returnOnEquity") == 0.32
    assert out.get("info", {}).get("targetMeanPrice") == 520.0
    assert not out.get("institutional_holders", pd.DataFrame()).empty
