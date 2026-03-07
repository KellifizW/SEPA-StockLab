"""
modules/news_impact.py
──────────────────────
Pre-open market news impact signal (free-source MVP).

Data sources:
  - Finviz ticker headlines (via data_pipeline)
  - Yahoo Finance market RSS (via data_pipeline)
  - SEC current 8-K Atom feed (via data_pipeline)

Output:
  - market_impact_score (-100..100)
  - direction (BULLISH / NEUTRAL / BEARISH)
  - confidence (LOW / MEDIUM / HIGH)
  - top_drivers (highest absolute weighted-impact headlines)
"""

import sys
import json
import math
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

from modules.market_env import assess
from modules.data_pipeline import (
    get_news_fvf_normalized,
    get_yahoo_market_news_rss,
    get_sec_current_8k_feed,
    get_wsj_market_news_rss,
)

logger = logging.getLogger(__name__)

_CACHE_FILE = ROOT / C.DATA_DIR / "market_news_impact_last.json"


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _to_utc(ts: Any) -> datetime | None:
    """Coerce arbitrary datetime-like value into aware UTC datetime."""
    try:
        d = pd.to_datetime(ts, errors="coerce", utc=True)
        if pd.isna(d):
            return None
        return d.to_pydatetime()
    except Exception:
        return None


def _load_candidate_tickers(limit: int) -> list[str]:
    """
    Load candidate tickers from latest combined scan cache.

    Fallback to configured market ETF list if cache is missing or malformed.
    """
    default = [str(x).upper().strip() for x in getattr(C, "NEWS_DEFAULT_MARKET_TICKERS", [])]
    cache_file = ROOT / C.DATA_DIR / "combined_last_scan.json"
    if not cache_file.exists():
        return default[:limit]

    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("_load_candidate_tickers: parse error: %s", exc)
        return default[:limit]

    tickers: list[str] = []

    def _collect_from_rows(rows: Any):
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            t = str(row.get("ticker") or "").upper().strip()
            if t:
                tickers.append(t)

    if isinstance(payload, dict):
        _collect_from_rows(payload.get("rows"))
        _collect_from_rows(payload.get("results"))
        _collect_from_rows(payload.get("sepa_rows"))
        _collect_from_rows(payload.get("qm_rows"))
        _collect_from_rows(payload.get("sepa"))
        _collect_from_rows(payload.get("qm"))

    uniq = []
    seen = set()
    for t in tickers + default:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
        if len(uniq) >= limit:
            break
    return uniq


def _headline_sentiment_score(title: str) -> float:
    """Rule-based sentiment score in range [-1.0, 1.0]."""
    txt = str(title or "").lower()
    if not txt:
        return 0.0

    pos = sum(1 for k in getattr(C, "NEWS_POSITIVE_KEYWORDS", []) if k in txt)
    neg = sum(1 for k in getattr(C, "NEWS_NEGATIVE_KEYWORDS", []) if k in txt)
    raw = (pos - neg) / max(1.0, pos + neg)
    return max(-1.0, min(1.0, raw))


def _headline_market_relevance(title: str) -> float:
    """Market relevance factor [0.6, 1.5] from configured macro keywords."""
    txt = str(title or "").lower()
    if not txt:
        return 0.6

    hit = sum(1 for k in getattr(C, "NEWS_MARKET_KEYWORDS", []) if k in txt)
    factor = 0.9 + 0.2 * hit
    return max(0.6, min(1.5, factor))


def _source_weight(source: str) -> float:
    """Return source reliability weight."""
    s = str(source or "").lower().strip()
    if s in {"", "nan", "none", "null", "unknown"}:
        return 1.0
    if s == "finviz":
        return float(getattr(C, "NEWS_SOURCE_WEIGHT_FINVIZ", 1.0))
    if s == "yahoo":
        return float(getattr(C, "NEWS_SOURCE_WEIGHT_YAHOO", 0.9))
    if s == "sec":
        return float(getattr(C, "NEWS_SOURCE_WEIGHT_SEC", 1.1))
    if s == "wsj":
        return float(getattr(C, "NEWS_SOURCE_WEIGHT_WSJ", 1.05))
    return 1.0


def _normalize_source_name(source: Any) -> str:
    """Normalize source label to prevent UI values like nan/unknown."""
    s = str(source or "").strip().lower()
    if s in {"", "nan", "none", "null"}:
        return "unknown"
    return s


def _time_decay_weight(published_at: datetime | None, now_utc: datetime) -> float:
    """Exponential time-decay so fresher headlines contribute more."""
    if published_at is None:
        return 0.75
    age_h = max(0.0, (now_utc - published_at).total_seconds() / 3600.0)
    half_life = max(0.5, float(getattr(C, "NEWS_TIME_DECAY_HALF_LIFE_HOURS", 6.0)))
    return math.exp(-age_h / half_life)


def _score_row(row: dict, now_utc: datetime) -> dict:
    """Compute weighted impact score per headline row."""
    title = str(row.get("title") or "")
    source = _normalize_source_name(row.get("source"))
    published_at = _to_utc(row.get("published_at"))

    sentiment = _headline_sentiment_score(title)
    relevance = _headline_market_relevance(title)
    src_w = _source_weight(source)
    time_w = _time_decay_weight(published_at, now_utc)

    weighted = sentiment * relevance * src_w * time_w
    if bool(row.get("stale_fallback")) and source == "wsj":
        weighted *= float(getattr(C, "NEWS_STALE_WSJ_IMPACT_MULT", 0.15))

    return {
        **row,
        "source": source,
        "published_at": published_at.isoformat() if published_at else None,
        "sentiment_score": round(float(sentiment), 4),
        "relevance_weight": round(float(relevance), 4),
        "source_weight": round(float(src_w), 4),
        "time_weight": round(float(time_w), 4),
        "weighted_impact": round(float(weighted), 4),
    }


def _direction(score: float) -> str:
    if score >= 8.0:
        return "BULLISH"
    if score <= -8.0:
        return "BEARISH"
    return "NEUTRAL"


def _confidence(headline_count: int, score_abs: float) -> str:
    if headline_count >= 20 and score_abs >= 20:
        return "HIGH"
    if headline_count >= 10 and score_abs >= 10:
        return "MEDIUM"
    return "LOW"


def _save_last(result: dict) -> None:
    """Persist latest digest snapshot for UI cached loading."""
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
    }
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("news_impact cache write failed: %s", exc)


def load_last_news_impact() -> dict:
    """Load cached latest pre-open news impact snapshot."""
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_preopen_news_impact() -> dict:
    """
    Build a pre-open market news impact digest from free sources.

    Returns
    -------
    dict
        JSON-safe digest payload for API/UI rendering.
    """
    if not bool(getattr(C, "NEWS_IMPACT_ENABLED", True)):
        return {
            "ok": False,
            "error": "NEWS_IMPACT_ENABLED is False",
            "market_impact_score": 0.0,
            "direction": "NEUTRAL",
            "confidence": "LOW",
            "headlines": [],
            "top_drivers": [],
        }

    now_utc = _utc_now()
    lookback_h = float(getattr(C, "NEWS_LOOKBACK_HOURS", 20))
    cutoff = now_utc - timedelta(hours=lookback_h)
    ticker_cap = int(getattr(C, "NEWS_IMPACT_TICKER_LIMIT", 20))
    per_source_cap = int(getattr(C, "NEWS_MAX_HEADLINES_PER_SOURCE", 25))

    market = {}
    try:
        market = assess(verbose=False)
    except Exception as exc:
        logger.warning("build_preopen_news_impact: market assess failed: %s", exc)

    tickers = _load_candidate_tickers(limit=ticker_cap)

    frames = []
    for t in tickers:
        try:
            df = get_news_fvf_normalized(ticker=t, max_rows=max(3, min(6, per_source_cap)))
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.debug("Finviz news fetch failed for %s: %s", t, exc)

    yahoo_df = get_yahoo_market_news_rss(max_rows=per_source_cap)
    if yahoo_df is not None and not yahoo_df.empty:
        frames.append(yahoo_df)

    sec_df = get_sec_current_8k_feed(max_rows=per_source_cap)
    if sec_df is not None and not sec_df.empty:
        frames.append(sec_df)

    wsj_df = get_wsj_market_news_rss(max_rows=per_source_cap)
    if wsj_df is not None and not wsj_df.empty:
        frames.append(wsj_df)

    if frames:
        merged = pd.concat(frames, ignore_index=True)
    else:
        merged = pd.DataFrame(columns=["source", "published_at", "title", "url", "ticker"])

    source_health = {}
    if not merged.empty and "source" in merged.columns:
        merged_health = merged.copy()
        merged_health["published_at"] = pd.to_datetime(merged_health["published_at"], errors="coerce", utc=True)
        for src in sorted(set(str(x) for x in merged_health["source"].fillna("unknown").tolist())):
            sub = merged_health[merged_health["source"].astype(str) == src]
            latest = sub["published_at"].max() if not sub.empty else pd.NaT
            latest_py = latest.to_pydatetime() if not pd.isna(latest) else None
            age_h = None
            if latest_py is not None:
                age_h = round((now_utc - latest_py).total_seconds() / 3600.0, 1)
            source_health[src] = {
                "items": int(len(sub)),
                "latest_published_at": latest_py.isoformat() if latest_py else None,
                "latest_age_hours": age_h,
            }

    if not merged.empty:
        merged["published_at"] = pd.to_datetime(merged["published_at"], errors="coerce", utc=True)
        merged = merged[merged["published_at"].isna() | (merged["published_at"] >= cutoff)]
        merged = merged.drop_duplicates(subset=["title", "url"], keep="first")
        merged = merged.sort_values("published_at", ascending=False, na_position="last")

    # WSJ fallback: if feed is stale and therefore filtered out by lookback,
    # optionally include a small number of WSJ headlines at reduced impact.
    if bool(getattr(C, "NEWS_INCLUDE_STALE_WSJ_FALLBACK", True)):
        has_wsj_fresh = (not merged.empty and "source" in merged.columns
                         and (merged["source"].astype(str).str.lower() == "wsj").any())
        if not has_wsj_fresh and wsj_df is not None and not wsj_df.empty:
            wsj_fallback = wsj_df.copy()
            wsj_fallback["published_at"] = pd.to_datetime(wsj_fallback["published_at"], errors="coerce", utc=True)
            max_days = int(getattr(C, "NEWS_STALE_WSJ_MAX_DAYS", 500))
            wsj_cutoff = now_utc - timedelta(days=max_days)
            wsj_fallback = wsj_fallback[wsj_fallback["published_at"].isna() | (wsj_fallback["published_at"] >= wsj_cutoff)]
            wsj_fallback = wsj_fallback.sort_values("published_at", ascending=False, na_position="last")
            wsj_fallback = wsj_fallback.head(int(getattr(C, "NEWS_STALE_WSJ_MAX_ITEMS", 5)))
            if not wsj_fallback.empty:
                wsj_fallback["stale_fallback"] = True
                merged = pd.concat([merged, wsj_fallback], ignore_index=True)

    scored = [_score_row(rec, now_utc) for rec in merged.to_dict(orient="records")]

    total = float(sum(x.get("weighted_impact", 0.0) for x in scored))
    score = max(-100.0, min(100.0, total * 25.0))
    score = round(score, 2)

    source_counts = {}
    for row in scored:
        src = _normalize_source_name(row.get("source"))
        source_counts[src] = source_counts.get(src, 0) + 1

    top_drivers = sorted(scored, key=lambda x: abs(float(x.get("weighted_impact", 0.0))), reverse=True)[:5]

    result = {
        "ok": True,
        "generated_at_utc": now_utc.isoformat(),
        "lookback_hours": lookback_h,
        "ticker_count": len(tickers),
        "headline_count": len(scored),
        "market_impact_score": score,
        "direction": _direction(score),
        "confidence": _confidence(len(scored), abs(score)),
        "regime": str((market or {}).get("regime") or "UNKNOWN"),
        "source_counts": source_counts,
        "source_health": source_health,
        "top_drivers": top_drivers,
        "headlines": scored[:80],
    }

    _save_last(result)
    return result
