"""
tests/test_news_impact.py
─────────────────────────
Unit tests for pre-open free-source news impact digest.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C


def _mk_news(rows):
    return pd.DataFrame(rows)


def test_build_preopen_news_impact_basic(monkeypatch, tmp_path):
    from modules import news_impact as ni

    monkeypatch.setattr(C, "DATA_DIR", str(tmp_path.name))
    monkeypatch.setattr(ni, "_CACHE_FILE", tmp_path / "market_news_impact_last.json")
    monkeypatch.setattr(C, "NEWS_IMPACT_ENABLED", True)
    monkeypatch.setattr(C, "NEWS_LOOKBACK_HOURS", 24)
    monkeypatch.setattr(C, "NEWS_IMPACT_TICKER_LIMIT", 3)

    monkeypatch.setattr(ni, "assess", lambda verbose=False: {"regime": "CONFIRMED_UPTREND"})
    monkeypatch.setattr(ni, "_load_candidate_tickers", lambda limit: ["SPY", "QQQ"])

    now = pd.Timestamp.now("UTC")
    monkeypatch.setattr(
        ni,
        "get_news_fvf_normalized",
        lambda ticker, max_rows=None: _mk_news([
            {
                "source": "finviz",
                "published_at": now.isoformat(),
                "title": f"{ticker} beats earnings and strong guidance",
                "url": f"https://example.com/{ticker}",
                "ticker": ticker,
            }
        ]),
    )
    monkeypatch.setattr(
        ni,
        "get_yahoo_market_news_rss",
        lambda max_rows=None: _mk_news([
            {
                "source": "yahoo",
                "published_at": now.isoformat(),
                "title": "Fed rate cut hopes lift risk assets",
                "url": "https://example.com/yahoo",
                "ticker": "",
            }
        ]),
    )
    monkeypatch.setattr(
        ni,
        "get_sec_current_8k_feed",
        lambda max_rows=None: _mk_news([
            {
                "source": "sec",
                "published_at": now.isoformat(),
                "title": "8-K current report filing",
                "url": "https://example.com/sec",
                "ticker": "",
            }
        ]),
    )
    monkeypatch.setattr(
        ni,
        "get_wsj_market_news_rss",
        lambda max_rows=None: _mk_news([
            {
                "source": "wsj",
                "published_at": now.isoformat(),
                "title": "WSJ markets coverage headline",
                "url": "https://www.wsj.com/markets",
                "ticker": "",
            }
        ]),
    )

    out = ni.build_preopen_news_impact()

    assert out["ok"] is True
    assert out["regime"] == "CONFIRMED_UPTREND"
    assert out["headline_count"] >= 3
    assert out["direction"] in {"BULLISH", "NEUTRAL", "BEARISH"}
    assert isinstance(out["top_drivers"], list)
    assert ni._CACHE_FILE.exists()


def test_build_preopen_news_impact_lookback_filter(monkeypatch):
    from modules import news_impact as ni

    monkeypatch.setattr(C, "NEWS_IMPACT_ENABLED", True)
    monkeypatch.setattr(C, "NEWS_LOOKBACK_HOURS", 6)
    monkeypatch.setattr(C, "NEWS_IMPACT_TICKER_LIMIT", 2)
    monkeypatch.setattr(ni, "_save_last", lambda result: None)

    monkeypatch.setattr(ni, "assess", lambda verbose=False: {"regime": "UPTREND_UNDER_PRESSURE"})
    monkeypatch.setattr(ni, "_load_candidate_tickers", lambda limit: ["SPY"])

    now = pd.Timestamp.now("UTC")
    old = now - pd.Timedelta(hours=30)

    monkeypatch.setattr(
        ni,
        "get_news_fvf_normalized",
        lambda ticker, max_rows=None: _mk_news([
            {
                "source": "finviz",
                "published_at": now.isoformat(),
                "title": "SPY beats expectations",
                "url": "https://example.com/new",
                "ticker": "SPY",
            },
            {
                "source": "finviz",
                "published_at": old.isoformat(),
                "title": "Old stale headline with downgrade",
                "url": "https://example.com/old",
                "ticker": "SPY",
            },
        ]),
    )
    monkeypatch.setattr(ni, "get_yahoo_market_news_rss", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_sec_current_8k_feed", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_wsj_market_news_rss", lambda max_rows=None: pd.DataFrame())

    out = ni.build_preopen_news_impact()

    titles = [h.get("title", "") for h in out.get("headlines", [])]
    assert "SPY beats expectations" in titles
    assert "Old stale headline with downgrade" not in titles


def test_build_preopen_news_impact_disabled(monkeypatch):
    from modules import news_impact as ni

    monkeypatch.setattr(C, "NEWS_IMPACT_ENABLED", False)
    out = ni.build_preopen_news_impact()

    assert out["ok"] is False
    assert out["direction"] == "NEUTRAL"
    assert out["market_impact_score"] == 0.0


def test_source_nan_normalized_to_unknown(monkeypatch):
    from modules import news_impact as ni

    monkeypatch.setattr(C, "NEWS_IMPACT_ENABLED", True)
    monkeypatch.setattr(C, "NEWS_LOOKBACK_HOURS", 24)
    monkeypatch.setattr(C, "NEWS_IMPACT_TICKER_LIMIT", 1)
    monkeypatch.setattr(ni, "_save_last", lambda result: None)
    monkeypatch.setattr(ni, "assess", lambda verbose=False: {"regime": "CONFIRMED_UPTREND"})
    monkeypatch.setattr(ni, "_load_candidate_tickers", lambda limit: ["SPY"])

    now = pd.Timestamp.now("UTC")
    monkeypatch.setattr(
        ni,
        "get_news_fvf_normalized",
        lambda ticker, max_rows=None: _mk_news([
            {
                "source": float("nan"),
                "published_at": now.isoformat(),
                "title": "Stocks retreat on weak jobs report",
                "url": "https://example.com/news",
                "ticker": ticker,
            }
        ]),
    )
    monkeypatch.setattr(ni, "get_yahoo_market_news_rss", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_sec_current_8k_feed", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_wsj_market_news_rss", lambda max_rows=None: pd.DataFrame())

    out = ni.build_preopen_news_impact()
    assert out["source_counts"].get("unknown") == 1
    assert out["headlines"][0]["source"] == "unknown"


def test_wsj_stale_fallback_included(monkeypatch):
    from modules import news_impact as ni

    monkeypatch.setattr(C, "NEWS_IMPACT_ENABLED", True)
    monkeypatch.setattr(C, "NEWS_LOOKBACK_HOURS", 6)
    monkeypatch.setattr(C, "NEWS_IMPACT_TICKER_LIMIT", 1)
    monkeypatch.setattr(C, "NEWS_INCLUDE_STALE_WSJ_FALLBACK", True)
    monkeypatch.setattr(C, "NEWS_STALE_WSJ_MAX_DAYS", 800)
    monkeypatch.setattr(C, "NEWS_STALE_WSJ_MAX_ITEMS", 2)
    monkeypatch.setattr(ni, "_save_last", lambda result: None)
    monkeypatch.setattr(ni, "assess", lambda verbose=False: {"regime": "TRANSITION"})
    monkeypatch.setattr(ni, "_load_candidate_tickers", lambda limit: ["SPY"])

    now = pd.Timestamp.now("UTC")
    old = now - pd.Timedelta(days=300)

    monkeypatch.setattr(ni, "get_news_fvf_normalized", lambda ticker, max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_yahoo_market_news_rss", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(ni, "get_sec_current_8k_feed", lambda max_rows=None: pd.DataFrame())
    monkeypatch.setattr(
        ni,
        "get_wsj_market_news_rss",
        lambda max_rows=None: _mk_news([
            {
                "source": "wsj",
                "published_at": old.isoformat(),
                "title": "WSJ stale fallback headline",
                "url": "https://www.wsj.com/test",
                "ticker": "",
            }
        ]),
    )

    out = ni.build_preopen_news_impact()
    assert out["source_counts"].get("wsj") == 1
    assert out["headlines"][0].get("source") == "wsj"
