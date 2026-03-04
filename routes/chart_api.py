"""Chart API routes — enriched daily, weekly, intraday, QM/ML watch signals."""

import logging
import math as _math
import time as _time
from typing import Any, Optional, cast
import pandas as pd
from flask import Blueprint, request, jsonify

import trader_config as C
from routes.helpers import (
    _clean,
    _qm_earnings_cache, _qm_nasdaq_cache,
)

bp = Blueprint("chart_api", __name__)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _sf(v: Any, digits: int = 2) -> Optional[float]:
    """Safe float → None for NaN/inf."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (_math.isnan(f) or _math.isinf(f)) else round(f, digits)
    except Exception:
        return None


def _sf_at(s: pd.Series, key: Any, digits: int = 2) -> Optional[float]:
    """Safe float lookup in a Series by index key."""
    try:
        pos = s.index.get_loc(key)
        return _sf(s.iloc[pos], digits)  # type: ignore[arg-type]
    except Exception:
        return None


def _bool_at(s: Optional[pd.Series], key: Any) -> bool:
    """Safe boolean lookup in a boolean Series by index key."""
    if s is None:
        return False
    try:
        pos = s.index.get_loc(key)
        v = s.iloc[pos]  # type: ignore[arg-type]
        return bool(v) if not pd.isna(v) else False
    except Exception:
        return False


def _df_to_candles(df) -> list:
    """Convert a yfinance DataFrame to [{time,open,high,low,close,volume}, …]."""
    result = []
    for ts, row in df.iterrows():
        t_unix = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
        o, h, l, c, v = (
            row.get("Open"),
            row.get("High"),
            row.get("Low"),
            row.get("Close"),
            row.get("Volume", 0),
        )
        if any(
            x is None or (isinstance(x, float) and (_math.isnan(x) or _math.isinf(x)))
            for x in [o, h, l, c]
        ):
            continue
        result.append({
            "time": t_unix,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": int(v or 0),
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Enriched daily chart
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/chart/enriched/<ticker>", methods=["GET"])
def api_chart_enriched(ticker: str):
    """OHLCV + technical indicators for TradingView Lightweight Charts."""
    import pandas as pd

    days = int(request.args.get("days", 504))
    ticker_upper = ticker.upper()

    try:
        from modules.data_pipeline import get_enriched
        df = get_enriched(ticker_upper, period="2y", use_cache=True)
        if df.empty:
            df = get_enriched(ticker_upper, period="2y", use_cache=False)
        if df.empty:
            return jsonify({"ok": False, "error": "No price data available"})

        cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
        df = df[df.index >= cutoff].copy()
        if df.empty:
            return jsonify({"ok": False, "error": "No data in requested range"})

        candles, volume = [], []
        sma50, sma150, sma200 = [], [], []
        ema9, ema21, ema50, ema150 = [], [], [], []
        rsi_pts, bbl_pts, bbm_pts, bbu_pts = [], [], [], []
        atr_pts, bbw_pts, vol_ratio_pts = [], [], []
        avwap_high_pts, avwap_low_pts = [], []

        # AVWAP series for ML (Martin Luk) charts
        avwap_h_series = avwap_l_series = None
        avwap_h_anchor = avwap_l_anchor = None
        try:
            from modules.data_pipeline import get_avwap_from_swing_high, get_avwap_from_swing_low
            full_df = get_enriched(ticker_upper, period="2y", use_cache=True)
            if not full_df.empty:
                ah = get_avwap_from_swing_high(full_df)
                al = get_avwap_from_swing_low(full_df)
                if ah.get("avwap_series") is not None and not ah["avwap_series"].dropna().empty:
                    avwap_h_series = ah["avwap_series"]
                    avwap_h_anchor = {"date": ah.get("anchor_date"), "price": ah.get("anchor_price")}
                if al.get("avwap_series") is not None and not al["avwap_series"].dropna().empty:
                    avwap_l_series = al["avwap_series"]
                    avwap_l_anchor = {"date": al.get("anchor_date"), "price": al.get("anchor_price")}
        except Exception:
            pass

        # VCP signal pre-computation
        atr_raw = pd.to_numeric(df["ATR_14"], errors="coerce") if "ATR_14" in df.columns else None
        if atr_raw is None:
            atr_raw = pd.to_numeric(df["ATRr_14"], errors="coerce") if "ATRr_14" in df.columns else None
        bbu_raw = pd.to_numeric(df["BBU_20_2.0"], errors="coerce") if "BBU_20_2.0" in df.columns else None
        bbl_raw = pd.to_numeric(df["BBL_20_2.0"], errors="coerce") if "BBL_20_2.0" in df.columns else None
        bbm_raw = pd.to_numeric(df["BBM_20_2.0"], errors="coerce") if "BBM_20_2.0" in df.columns else None
        vol_raw = pd.to_numeric(df["Volume"], errors="coerce") if "Volume" in df.columns else None

        if atr_raw is not None:
            atr_fast = atr_raw.rolling(5, min_periods=5).mean()
            atr_prev = atr_fast.shift(5)
            atr_contracting_series = (atr_prev > 0) & ((atr_fast / atr_prev) < 0.85)
        else:
            atr_contracting_series = None

        if bbu_raw is not None and bbl_raw is not None and bbm_raw is not None:
            bb_width_pct_raw = (bbu_raw - bbl_raw) / bbm_raw.replace(0, float("nan")) * 100.0
            bb_fast = bb_width_pct_raw.rolling(5, min_periods=5).mean()
            bb_q25 = bb_width_pct_raw.rolling(40, min_periods=20).quantile(0.25)
            bb_contracting_series = bb_fast <= (bb_q25 * 1.1)
        else:
            bb_width_pct_raw = None
            bb_contracting_series = None

        if vol_raw is not None:
            vol_avg50 = vol_raw.rolling(50, min_periods=20).mean()
            vol_ratio_raw = vol_raw / vol_avg50.replace(0, float("nan"))
            vol_dry_series = vol_ratio_raw <= float(getattr(C, "VCP_VOLUME_DRY_THRESHOLD", 0.5))
        else:
            vol_ratio_raw = None
            vol_dry_series = None

        vcp_signal_events = []

        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(str(idx)).timestamp())
            except Exception:
                continue
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            if None in (o, h, lo, c):
                continue

            o_f = cast(float, o)  # None excluded by continue above
            h_f = cast(float, h)
            lo_f = cast(float, lo)
            c_f = cast(float, c)
            candles.append({"time": ts, "open": o_f, "high": h_f, "low": lo_f, "close": c_f})
            volume.append({"time": ts, "value": v,
                           "color": "#3fb950" if c_f >= o_f else "#f85149"})

            for series, colname in [
                (sma50, "SMA_50"), (sma150, "SMA_150"), (sma200, "SMA_200"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            for series, colname in [
                (ema9, "EMA_9"), (ema21, "EMA_21"), (ema50, "EMA_50"), (ema150, "EMA_150"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            if avwap_h_series is not None:
                try:
                    av = _sf(avwap_h_series.loc[idx])
                    if av is not None:
                        avwap_high_pts.append({"time": ts, "value": av})
                except Exception:
                    pass
            if avwap_l_series is not None:
                try:
                    av = _sf(avwap_l_series.loc[idx])
                    if av is not None:
                        avwap_low_pts.append({"time": ts, "value": av})
                except Exception:
                    pass

            rsi_v = _sf(row.get("RSI_14"))
            if rsi_v is not None:
                rsi_pts.append({"time": ts, "value": rsi_v})

            for series, colname in [
                (bbl_pts, "BBL_20_2.0"), (bbm_pts, "BBM_20_2.0"), (bbu_pts, "BBU_20_2.0"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

            atr_v = _sf(row.get("ATR_14") if "ATR_14" in row else row.get("ATRr_14"), 4)
            if atr_v is not None:
                atr_pts.append({"time": ts, "value": atr_v})

            bbw_v = None
            if bb_width_pct_raw is not None:
                bbw_v = _sf_at(bb_width_pct_raw, idx, 3)
            if bbw_v is not None:
                bbw_pts.append({"time": ts, "value": bbw_v})

            vr_v = None
            if vol_ratio_raw is not None:
                vr_v = _sf_at(vol_ratio_raw, idx, 3)
            if vr_v is not None:
                vol_ratio_pts.append({"time": ts, "value": vr_v})

            atr_sig = _bool_at(atr_contracting_series, idx)
            bb_sig = _bool_at(bb_contracting_series, idx)
            vol_sig = _bool_at(vol_dry_series, idx)
            sig_count = int(atr_sig) + int(bb_sig) + int(vol_sig)

            if sig_count >= 2:
                vcp_signal_events.append({
                    "time": ts,
                    "atr_contracting": atr_sig,
                    "bb_contracting": bb_sig,
                    "vol_dry": vol_sig,
                    "signal_count": sig_count,
                })

        return jsonify({
            "ok": True, "ticker": ticker_upper,
            "candles": candles, "volume": volume,
            "sma50": sma50, "sma150": sma150, "sma200": sma200,
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "ema150": ema150,
            "avwap_high": avwap_high_pts, "avwap_low": avwap_low_pts,
            "avwap_high_anchor": avwap_h_anchor,
            "avwap_low_anchor": avwap_l_anchor,
            "rsi": rsi_pts,
            "bbl": bbl_pts, "bbm": bbm_pts, "bbu": bbu_pts,
            "atr14": atr_pts,
            "bb_width_pct": bbw_pts,
            "vol_ratio_50d": vol_ratio_pts,
            "vcp_signal_events": vcp_signal_events,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ═══════════════════════════════════════════════════════════════════════════════
# Weekly chart
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/chart/weekly/<ticker>", methods=["GET"])
def api_chart_weekly(ticker: str):
    """Weekly OHLCV + EMA overlays for Martin Luk multi-timeframe analysis."""
    import pandas as pd

    weeks = int(request.args.get("weeks", 156))
    ticker_upper = ticker.upper()

    try:
        import yfinance as yf
        t = yf.Ticker(ticker_upper)
        df = t.history(period="5y", interval="1wk")
        if df.empty:
            return jsonify({"ok": False, "error": "No weekly data"})

        df = df.tail(weeks).copy()
        for n in (9, 21, 50):
            df[f"EMA_{n}"] = df["Close"].ewm(span=n, adjust=False).mean()

        candles, volume = [], []
        ema9w, ema21w, ema50w = [], [], []

        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(str(idx)).timestamp())
            except Exception:
                continue
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            if None in (o, h, lo, c):
                continue

            o_wf = cast(float, o)  # None excluded by continue above
            h_wf = cast(float, h)
            lo_wf = cast(float, lo)
            c_wf = cast(float, c)
            candles.append({"time": ts, "open": o_wf, "high": h_wf, "low": lo_wf, "close": c_wf})
            volume.append({"time": ts, "value": v,
                           "color": "#3fb950" if c_wf >= o_wf else "#f85149"})

            for series, colname in [
                (ema9w, "EMA_9"), (ema21w, "EMA_21"), (ema50w, "EMA_50"),
            ]:
                val = _sf(row.get(colname))
                if val is not None:
                    series.append({"time": ts, "value": val})

        return jsonify({
            "ok": True, "ticker": ticker_upper,
            "candles": candles, "volume": volume,
            "ema9w": ema9w, "ema21w": ema21w, "ema50w": ema50w,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ═══════════════════════════════════════════════════════════════════════════════
# QM Watch – helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def _get_next_earnings_date(ticker: str):
    """Return next earnings date (date obj), cached 24h. None if unavailable."""
    import datetime
    now = _time.time()
    cached = _qm_earnings_cache.get(ticker)
    if cached and now - cached["fetched_at"] < 86400:
        return cached["date"]
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if cal is not None:
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
            else:
                try:
                    dates = list(cal.loc["Earnings Date"])
                except Exception:
                    dates = []
            today = datetime.date.today()
            future = [d.date() if hasattr(d, "date") else d for d in dates if d is not None]
            future = [d for d in future if d >= today]
            result = min(future) if future else None
        else:
            result = None
    except Exception:
        result = None
    _qm_earnings_cache[ticker] = {"date": result, "fetched_at": now}
    return result


def _get_nasdaq_regime_snapshot() -> dict:
    """Return NASDAQ (QQQ) regime: full_power / caution / choppy / stop."""
    now = _time.time()
    cached = _qm_nasdaq_cache.get("snapshot")
    cache_secs = C.QM_NASDAQ_CACHE_MINUTES * 60
    if cached and now - cached["fetched_at"] < cache_secs:
        return cached

    try:
        import yfinance as yf
        import pandas as pd
        tkr = C.QM_NASDAQ_TICKER
        fast_p = C.QM_NASDAQ_SMA_FAST
        slow_p = C.QM_NASDAQ_SMA_SLOW
        slope_lb = C.QM_NASDAQ_SLOPE_LOOKBACK

        df = yf.download(tkr, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < slow_p + slope_lb:
            raise ValueError("insufficient data")

        close_raw = df["Close"].squeeze()
        if not isinstance(close_raw, pd.Series):
            raise ValueError("insufficient data")
        close: pd.Series = close_raw
        sma_fast = close.rolling(fast_p).mean().iloc[-1]
        sma_slow = close.rolling(slow_p).mean().iloc[-1]
        sma_fast_prev = close.rolling(fast_p).mean().iloc[-(slope_lb + 1)]
        price = float(close.iloc[-1])
        sma_fast = float(sma_fast)
        sma_slow = float(sma_slow)
        fast_slope = sma_fast - float(sma_fast_prev)

        if price > sma_fast and sma_fast > sma_slow and fast_slope > 0:
            regime = "full_power"
        elif price > sma_slow and (sma_fast <= sma_slow or fast_slope <= 0):
            regime = "caution"
        elif price < sma_slow and price > min(sma_fast, sma_slow) * 0.98:
            regime = "choppy"
        else:
            regime = "stop"

        result = {
            "regime": regime, "sma_fast": round(sma_fast, 2), "sma_slow": round(sma_slow, 2),
            "price": round(price, 2), "fast_slope": round(fast_slope, 4), "fetched_at": now,
        }
    except Exception as e:
        result = {"regime": "unknown", "sma_fast": None, "sma_slow": None,
                  "price": None, "fast_slope": None, "fetched_at": now, "error": str(e)}

    _qm_nasdaq_cache["snapshot"] = result
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# QM Watch – intraday signal engine
# ═══════════════════════════════════════════════════════════════════════════════

def _get_qm_intraday_signals(
    candles_5m: list, candles_1h: list,
    orh: Optional[float], orl: Optional[float], lod: Optional[float], hod: Optional[float],
    hl_swings: list,
    prev_close: float, gap_pct: float,
    atr_daily: float, ticker: str,
) -> dict:
    """Compute Qullamaggie intraday watch signals from candle lists + metadata."""
    import datetime
    signals: list = []
    gate_blocks: list = []

    # ── Earnings gate ─────────────────────────────────────────────────────
    earnings_date = _get_next_earnings_date(ticker)
    if earnings_date is not None:
        import datetime as dt
        today = dt.date.today()
        delta = (earnings_date - today).days
        if delta <= C.QM_EARNINGS_BLACKOUT_DAYS:
            gate_blocks.append("EARNINGS_BLACKOUT")
            signals.append(f"🔴 財報黑色期 Earnings in {delta}d  avoid new entries")
        elif delta <= C.QM_EARNINGS_WARN_DAYS:
            gate_blocks.append("EARNINGS_WARNING")
            signals.append(f"⚠️ 財報警告 Earnings in {delta}d  reduce size")

    # ── NASDAQ regime gate ────────────────────────────────────────────────
    nasdaq = _get_nasdaq_regime_snapshot()
    regime = nasdaq.get("regime", "unknown")
    if regime == "stop":
        gate_blocks.append("NASDAQ_STOP")
        signals.append("🔴 NASDAQ停損區 QQQ below both SMAs  no new longs")
    elif regime == "caution":
        gate_blocks.append("NASDAQ_CAUTION")
        signals.append("⚠️ NASDAQ警戒 QQQ caution zone  half-size only")

    # ── Gap filter (S30) ─────────────────────────────────────────────────
    gap_passed = abs(gap_pct) < C.QM_GAP_PASS_PCT
    gap_warning = abs(gap_pct) >= C.QM_GAP_WARN_PCT
    if gap_pct >= C.QM_GAP_PASS_PCT:
        signals.append(f"🔴 跳空過大 Gap {gap_pct:+.1f}%  {C.QM_GAP_PASS_PCT}%  wait for first 5-min range")
    elif gap_pct >= C.QM_GAP_WARN_PCT:
        signals.append(f"⚠️ 跳空注意 Gap {gap_pct:+.1f}%  confirm ORH before adding")
    gap_status = {"gap_pct": round(gap_pct, 2), "passed": gap_passed, "warning": gap_warning}

    # ── ORH levels (S29) ─────────────────────────────────────────────────
    def _orh_from_candles(candles: list, n: int) -> dict:
        if not candles or len(candles) < n:
            return {"hi": None, "lo": None, "broken_up": False, "broken_dn": False}
        opening = candles[:n]
        hi = max(c["high"] for c in opening)
        lo = min(c["low"] for c in opening)
        rest = candles[n:]
        broken_up = any(c["close"] > hi for c in rest) if rest else False
        broken_dn = any(c["close"] < lo for c in rest) if rest else False
        return {"hi": round(hi, 2), "lo": round(lo, 2),
                "broken_up": broken_up, "broken_dn": broken_dn}

    n_1m = C.QM_ORH_1M_CANDLES
    n_5m = C.QM_ORH_5M_CANDLES
    n_60m = C.QM_ORH_60M_CANDLES

    orh_5m_1bar = _orh_from_candles(candles_5m, n_5m)
    orh_5m_6bar = _orh_from_candles(candles_5m, n_60m)
    orh_1m_proxy = _orh_from_candles(candles_5m, n_1m)

    orh_levels = {"1m": orh_1m_proxy, "5m": orh_5m_1bar, "60m": orh_5m_6bar}

    current_price = candles_5m[-1]["close"] if candles_5m else (hod if hod else None)

    if orh_5m_6bar.get("broken_up"):
        signals.append(f"🟢 突破30分鐘高點 Price broke above 30-min ORH {orh_5m_6bar['hi']}")
    if orh_5m_1bar.get("broken_up"):
        signals.append(f"🟢 突破5分鐘高點 Price broke above 5-min ORH {orh_5m_1bar['hi']}")
    if orh_5m_6bar.get("broken_dn"):
        signals.append(f"🔴 跌破30分鐘低點 Price broke below 30-min ORL {orh_5m_6bar['lo']}")

    # ── ATR entry gate (S1/S31) ──────────────────────────────────────────
    atr_gate = {"current_price": current_price,
                "atr": round(atr_daily, 4) if atr_daily and atr_daily > 0 else None,
                "lod": round(lod, 2) if lod else None, "chase_status": "n/a",
                "max_buy_excellent": None, "max_buy_ideal": None, "max_buy_caution": None,
                "dist_atr_frac": None}
    if current_price and atr_daily and atr_daily > 0 and lod:
        max_buy_exc = lod + C.QM_ATR_CHASE_EXCELLENT * atr_daily
        max_buy_ideal = lod + C.QM_ATR_CHASE_IDEAL_MAX * atr_daily
        max_buy_caut = lod + C.QM_ATR_CHASE_CAUTION_MAX * atr_daily
        dist_frac = (current_price - lod) / atr_daily if atr_daily else None

        atr_gate.update({
            "max_buy_excellent": round(max_buy_exc, 2),
            "max_buy_ideal": round(max_buy_ideal, 2),
            "max_buy_caution": round(max_buy_caut, 2),
            "dist_atr_frac": round(dist_frac, 3) if dist_frac is not None else None,
        })
        if dist_frac is not None:
            if dist_frac < C.QM_ATR_CHASE_EXCELLENT:
                atr_gate["chase_status"] = "excellent"
                signals.append(f"🟢 入場極佳 Entry excellent: price only {dist_frac:.2f}ATR from LOD")
            elif dist_frac < C.QM_ATR_CHASE_IDEAL_MAX:
                atr_gate["chase_status"] = "ideal"
                signals.append(f"🟢 入場理想 Entry ideal: {dist_frac:.2f}ATR from LOD")
            elif dist_frac < C.QM_ATR_CHASE_CAUTION_MAX:
                atr_gate["chase_status"] = "caution"
                signals.append(f"⚠️ 入場謹慎 Entry caution: {dist_frac:.2f}ATR from LOD  feels like chasing")
            else:
                atr_gate["chase_status"] = "too_late"
                signals.append(f"🔴 追價過高 Too extended: {dist_frac:.2f}ATR from LOD  avoid")

    # ── MA signals (S11) ─────────────────────────────────────────────────
    def _sma(closes: list, period: int):
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period

    def _ema(closes: list, period: int):
        if len(closes) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(closes[:period]) / period
        for c in closes[period:]:
            ema_val = c * k + ema_val * (1 - k)
        return ema_val

    ma_signals: dict = {}
    if candles_5m:
        closes_5m = [c["close"] for c in candles_5m]
        sma10_5m = _sma(closes_5m, C.QM_WATCH_SMA_5M[0])
        sma20_5m = _sma(closes_5m, C.QM_WATCH_SMA_5M[1])
        ma_signals["5m_sma10"] = round(sma10_5m, 2) if sma10_5m else None
        ma_signals["5m_sma20"] = round(sma20_5m, 2) if sma20_5m else None
        if current_price and sma20_5m:
            above = current_price > sma20_5m
            ma_signals["price_vs_5m_sma20"] = "above" if above else "below"
            if above:
                signals.append(f"🟢 價格在5分SMA20之上 Price above 5-min SMA20 ({sma20_5m:.2f})")
            else:
                signals.append(f"🔴 價格跌破5分SMA20 Price below 5-min SMA20 ({sma20_5m:.2f})")

    if candles_1h:
        closes_1h = [c["close"] for c in candles_1h]
        ema10_1h = _ema(closes_1h, C.QM_WATCH_EMA_60M[0])
        ema20_1h = _ema(closes_1h, C.QM_WATCH_EMA_60M[1])
        ema65_1h = _ema(closes_1h, C.QM_WATCH_EMA_60M[2])
        ma_signals["1h_ema10"] = round(ema10_1h, 2) if ema10_1h else None
        ma_signals["1h_ema20"] = round(ema20_1h, 2) if ema20_1h else None
        ma_signals["1h_ema65"] = round(ema65_1h, 2) if ema65_1h else None
        if current_price and ema65_1h:
            above = current_price > ema65_1h
            ma_signals["price_vs_1h_ema65"] = "above" if above else "below"
            if above:
                signals.append(f"🟢 價格在60分EMA65之上 Price above 1-hr EMA65 ({ema65_1h:.2f})")
            else:
                signals.append(f"🔴 跌破60分EMA65 Price below 1-hr EMA65 ({ema65_1h:.2f})  key support lost")

    # ── Higher lows (S12) ────────────────────────────────────────────────
    higher_lows = {"count": 0, "valid": False, "last_swing_lo": None, "trend": "flat"}
    if hl_swings and len(hl_swings) >= C.QM_HL_MIN_SWINGS:
        lows = [s["low"] for s in hl_swings if "low" in s]
        if len(lows) >= C.QM_HL_MIN_SWINGS:
            hl_count = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
            valid = hl_count >= C.QM_HL_MIN_SWINGS - 1
            trend = "ascending" if valid else ("mixed" if hl_count > 0 else "descending")
            higher_lows = {
                "count": hl_count, "valid": valid,
                "last_swing_lo": round(lows[-1], 2) if lows else None,
                "trend": trend,
            }
            if valid:
                signals.append(f"🟢 更高低點 Higher lows confirmed ({hl_count} swings)  bullish structure")
            elif hl_count == 0 and len(lows) >= 2:
                signals.append("🔴 低點下移 Lower lows forming  weakening structure")

    # ── Breakout signals (S40) ───────────────────────────────────────────
    breakout_signals: list = []
    if current_price and hod:
        if current_price >= hod * 0.998:
            breakout_signals.append({"type": "HOD_CHALLENGE", "level": round(hod, 2),
                                     "current_price": current_price, "strength": "strong"})
            signals.append(f"🟢 挑戰日高 Price challenging HOD {hod}  watch for close above")
    if current_price and orh and current_price > orh * 1.001:
        breakout_signals.append({"type": "ORH_BREAK", "level": round(orh, 2),
                                 "current_price": current_price, "strength": "moderate"})

    # ── Signal counts ────────────────────────────────────────────────────
    bullish = sum(1 for s in signals if "🟢" in s)
    bearish = sum(1 for s in signals if "🔴" in s)
    neutral = sum(1 for s in signals if "⚠️" in s or "ℹ️" in s)

    # ── QM Dynamic Watch Score ───────────────────────────────────────────
    w_score = 0
    w_breakdown: list = []
    w_iron_rules: list = []

    # ORH breakthrough
    _orh_up = sum(1 for k in ("1m", "5m", "60m") if orh_levels.get(k, {}).get("broken_up"))
    _orh_dn = sum(1 for k in ("1m", "5m", "60m") if orh_levels.get(k, {}).get("broken_dn"))
    if _orh_up == 3:
        w_score += C.QM_WSCORE_ORH_ALL_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_ALL_UP, "note": "三級全部突破上行，強烈看好"})
    elif orh_levels.get("60m", {}).get("broken_up"):
        w_score += C.QM_WSCORE_ORH_60M_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_60M_UP, "note": "30分鐘 ORH 突破上行"})
    elif orh_levels.get("5m", {}).get("broken_up"):
        w_score += C.QM_WSCORE_ORH_5M_UP
        w_breakdown.append({"dim": "ORH 突破", "pts": C.QM_WSCORE_ORH_5M_UP, "note": "5分鐘 ORH 突破上行"})
    else:
        w_breakdown.append({"dim": "ORH 突破", "pts": 0, "note": "尚未突破任何 ORH"})
    if orh_levels.get("60m", {}).get("broken_dn"):
        w_score += C.QM_WSCORE_ORH_60M_DN
        w_breakdown.append({"dim": "ORH 失敗", "pts": C.QM_WSCORE_ORH_60M_DN, "note": "跌破30分鐘 ORL 下行  結構破裂"})

    # ATR entry gate
    _chase = atr_gate.get("chase_status", "n/a")
    _atr_map = {"excellent": C.QM_WSCORE_ATR_EXCELLENT, "ideal": C.QM_WSCORE_ATR_IDEAL,
                "caution": C.QM_WSCORE_ATR_CAUTION, "too_late": C.QM_WSCORE_ATR_TOOLATE}
    if _chase in _atr_map:
        w_score += _atr_map[_chase]
        _atr_label = {"excellent": "極佳 <0.4×ATR", "ideal": "理想 0.4-0.67×ATR",
                      "caution": "謹慎 0.67-1×ATR", "too_late": "超高 >1×ATR"}[_chase]
        w_breakdown.append({"dim": "ATR 入場", "pts": _atr_map[_chase], "note": f"{_atr_label}"})
    else:
        w_breakdown.append({"dim": "ATR 入場", "pts": 0, "note": "等待盤中價格數據"})
    if _chase == "too_late":
        w_iron_rules.append({"rule": "ATR_TOO_LATE",
                             "msg": "追價過高 (>1×ATR from LOD) — 強烈不建議追買",
                             "severity": "warn"})

    # NASDAQ regime
    _nas_map = {"full_power": C.QM_WSCORE_NASDAQ_FULL, "caution": C.QM_WSCORE_NASDAQ_CAUTION,
                "choppy": C.QM_WSCORE_NASDAQ_CHOPPY, "stop": C.QM_WSCORE_NASDAQ_STOP}
    _nas_label = {"full_power": "全力上揚", "caution": "警戒中", "choppy": "震盪", "stop": "停損區"}.get(regime, regime)
    if regime in _nas_map:
        w_score += _nas_map[regime]
        w_breakdown.append({"dim": "市場環境", "pts": _nas_map[regime], "note": f"NASDAQ {_nas_label}"})
    if regime == "stop":
        w_iron_rules.append({"rule": "NASDAQ_STOP",
                             "msg": "NASDAQ 停損區 — 鐵律禁止做多 (S5)",
                             "severity": "block"})

    # Earnings proximity
    if "EARNINGS_BLACKOUT" in gate_blocks:
        w_score += C.QM_WSCORE_EARNINGS_BLOCK
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_BLOCK, "note": "財報黑色期 ≤3天 — 鐵律禁止 (S2)"})
        w_iron_rules.append({"rule": "EARNINGS_BLACKOUT",
                             "msg": "財報黑色期 ≤3天 — 鐵律禁止新建倉",
                             "severity": "block"})
    elif "EARNINGS_WARNING" in gate_blocks:
        w_score += C.QM_WSCORE_EARNINGS_WARN
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_WARN, "note": "財報警告期 4-7天 — 建議減半倉位"})
    else:
        w_score += C.QM_WSCORE_EARNINGS_CLEAR
        w_breakdown.append({"dim": "財報期", "pts": C.QM_WSCORE_EARNINGS_CLEAR, "note": "財報遠離 — ≥8天安全"})

    # Gap (S30)
    if abs(gap_pct) >= C.QM_GAP_PASS_PCT:
        w_score += C.QM_WSCORE_GAP_BLOCK
        w_breakdown.append({"dim": "開盤跳空", "pts": C.QM_WSCORE_GAP_BLOCK,
                            "note": f"跳空 {gap_pct:+.1f}% — 超過上限，鐵律 PASS"})
        w_iron_rules.append({"rule": "GAP_EXTREME",
                             "msg": f"開盤跳空 {gap_pct:+.1f}% 過大 — 鐵律禁止入場 (S30)",
                             "severity": "block"})
    elif abs(gap_pct) >= C.QM_GAP_WARN_PCT:
        w_score += C.QM_WSCORE_GAP_WARN
        w_breakdown.append({"dim": "開盤跳空", "pts": C.QM_WSCORE_GAP_WARN,
                            "note": f"跳空 {gap_pct:+.1f}% — 需確認ORH再入場"})
    else:
        w_breakdown.append({"dim": "開盤跳空", "pts": 0, "note": f"跳空 {gap_pct:+.1f}% — 正常範圍"})

    # Higher lows (S40)
    if higher_lows.get("valid"):
        w_score += C.QM_WSCORE_HL_CONFIRMED
        w_breakdown.append({"dim": "低點結構", "pts": C.QM_WSCORE_HL_CONFIRMED, "note": "盤中確認更高低點 — 上升結構"})
    elif higher_lows.get("trend") == "descending":
        w_score += C.QM_WSCORE_HL_LOWER
        w_breakdown.append({"dim": "低點結構", "pts": C.QM_WSCORE_HL_LOWER, "note": "盤中出現更低低點 — 下降結構"})
    else:
        w_breakdown.append({"dim": "低點結構", "pts": 0, "note": "盤中低點持平/不明確"})

    # MA position (S11)
    if ma_signals.get("price_vs_5m_sma20") == "above":
        w_score += C.QM_WSCORE_MA_ABOVE_5M20
        w_breakdown.append({"dim": "5分MA", "pts": C.QM_WSCORE_MA_ABOVE_5M20, "note": "價格 > 5分SMA20 — 短期上升"})
    elif ma_signals.get("price_vs_5m_sma20") == "below":
        w_score += C.QM_WSCORE_MA_BELOW_5M20
        w_breakdown.append({"dim": "5分MA", "pts": C.QM_WSCORE_MA_BELOW_5M20, "note": "價格 < 5分SMA20 — 短期下降"})
    if ma_signals.get("price_vs_1h_ema65") == "above":
        w_score += C.QM_WSCORE_MA_ABOVE_1H65
        w_breakdown.append({"dim": "60分MA", "pts": C.QM_WSCORE_MA_ABOVE_1H65, "note": "價格 > 60分EMA65 — 中期向上"})
    elif ma_signals.get("price_vs_1h_ema65") == "below":
        w_score += C.QM_WSCORE_MA_BELOW_1H65
        w_breakdown.append({"dim": "60分MA", "pts": C.QM_WSCORE_MA_BELOW_1H65, "note": "價格 < 60分EMA65 — 中期支撐斷裂"})

    # HOD challenge
    if any(b["type"] == "HOD_CHALLENGE" for b in breakout_signals):
        w_score += C.QM_WSCORE_HOD_CHALLENGE
        w_breakdown.append({"dim": "突破強度", "pts": C.QM_WSCORE_HOD_CHALLENGE, "note": "挑戰日高  突破確認信號"})

    # Normalize → 0-100
    _max_raw = C.QM_WSCORE_MAX
    w_normalized = max(0, min(100, int((w_score + _max_raw) / (2 * _max_raw) * 100)))

    # Action recommendation
    _has_block = any(r["severity"] == "block" for r in w_iron_rules)
    if _has_block:
        w_action, w_action_zh = "BLOCK", "🚫 鐵律否決 — 不可買入"
    elif w_normalized >= 70:
        w_action, w_action_zh = "BUY", "🟢 動態看好 — 可以入場/加倉"
    elif w_normalized >= 50:
        w_action, w_action_zh = "WATCH", "🟡 中性觀望 — 等待更好信號"
    elif w_normalized >= 30:
        w_action, w_action_zh = "CAUTION", "🟠 偏弱謹慎 — 不建議新倉"
    else:
        w_action, w_action_zh = "AVOID", "🔴 偏空 — 不建議買入"

    watch_score = {
        "raw": w_score, "normalized": w_normalized,
        "action": w_action, "action_zh": w_action_zh,
        "breakdown": w_breakdown, "iron_rules": w_iron_rules,
        "has_block": _has_block, "timestamp": int(_time.time()),
    }

    return {
        "gate_blocks": gate_blocks,
        "nasdaq": {k: v for k, v in nasdaq.items() if k != "fetched_at"},
        "orh_levels": orh_levels, "atr_gate": atr_gate,
        "gap_status": gap_status, "higher_lows": higher_lows,
        "ma_signals": ma_signals, "breakout_signals": breakout_signals,
        "active_signals": signals,
        "signal_counts": {"total": len(signals), "bullish": bullish, "bearish": bearish, "neutral": neutral},
        "watch_score": watch_score,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# QM Watch Signals route
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/qm/watch_signals/<ticker>")
def qm_watch_signals(ticker: str):
    """QM 盯盤模式 — real-time intraday signal bundle."""
    import datetime
    try:
        ticker = ticker.upper().strip()
        _data_source = "intraday"

        import yfinance as yf

        try:
            df_5m = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=False)
            candles_5m = _df_to_candles(df_5m) if not df_5m.empty else []
        except Exception:
            candles_5m = []
        try:
            df_1h = yf.Ticker(ticker).history(period="60d", interval="1h", prepost=False)
            candles_1h = _df_to_candles(df_1h) if not df_1h.empty else []
        except Exception:
            candles_1h = []

        # Daily ATR
        df_d = None
        try:
            from modules.data_pipeline import get_historical, get_atr
            df_d = get_historical(ticker, period="3mo")
            if df_d is not None and not df_d.empty and len(df_d) >= 2:
                atr_daily = get_atr(df_d)
                if not atr_daily or atr_daily <= 0:
                    atr_raw = (df_d["High"] - df_d["Low"]).rolling(14).mean().iloc[-1]
                    atr_daily = float(atr_raw) if atr_raw == atr_raw else None
                prev_close = float(df_d["Close"].iloc[-2])
                current_open = float(df_d["Open"].iloc[-1])
                gap_pct = (current_open - prev_close) / prev_close * 100 if prev_close else 0.0
            else:
                atr_daily = prev_close = None
                gap_pct = 0.0
        except Exception as _e:
            logger.warning("ATR fetch failed for %s: %s", ticker, _e)
            atr_daily = prev_close = None
            gap_pct = 0.0

        # Session extremes
        if candles_5m:
            hod = max(c["high"] for c in candles_5m)
            lod = min(c["low"] for c in candles_5m)
            orh = candles_5m[0]["high"]
            orl = candles_5m[0]["low"]
        elif df_d is not None and not df_d.empty and len(df_d) >= 1:
            _data_source = "fallback_daily"
            hod = float(df_d["High"].iloc[-1])
            lod = float(df_d["Low"].iloc[-1])
            orh = orl = None
        else:
            hod = lod = orh = orl = None

        # Swing lows from 5m candles
        hl_swings: list = []
        lookback = min(C.QM_HL_LOOKBACK_CANDLES, len(candles_5m))
        if lookback >= 3:
            window = candles_5m[-lookback:]
            for i in range(1, len(window) - 1):
                if window[i]["low"] < window[i - 1]["low"] and window[i]["low"] < window[i + 1]["low"]:
                    hl_swings.append({"low": window[i]["low"], "index": i})

        # Run signal engine
        signals = _get_qm_intraday_signals(
            candles_5m=candles_5m, candles_1h=candles_1h,
            orh=orh, orl=orl, lod=lod, hod=hod,
            hl_swings=hl_swings,
            prev_close=prev_close or 0.0,
            gap_pct=gap_pct,
            atr_daily=atr_daily or 0.0,
            ticker=ticker,
        )

        current_price = candles_5m[-1]["close"] if candles_5m else (
            float(df_d["Close"].iloc[-1]) if df_d is not None and not df_d.empty else None
        )
        last_ts = candles_5m[-1]["time"] if candles_5m else None

        return jsonify(_clean({
            "ok": True, "ticker": ticker,
            "current_price": current_price, "last_ts": last_ts,
            "hod": hod, "lod": lod, "orh": orh, "orl": orl,
            "gap_pct": round(gap_pct, 2),
            "atr_daily": round(atr_daily, 4) if atr_daily else None,
            "data_source": _data_source,
            "refresh_ts": int(_time.time()),
            **signals,
        }))

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ML Watch – intraday signal engine
# ═══════════════════════════════════════════════════════════════════════════════

def _get_ml_intraday_signals(
    candles_5m: list,
    ema9: float, ema21: float, vwap: float,
    orh: float, orl: float, lod: float, hod: float,
    prev_close: float, avwap_high: float, avwap_low: float,
    daily_dim_g_score: float,
) -> dict:
    """Martin Luk intraday watch signal engine."""
    signals: list = []
    w_score = 0
    w_breakdown: list = []
    w_iron_rules: list = []

    current_price = candles_5m[-1]["close"] if candles_5m else 0

    # VWAP position (Chapter 6)
    vwap_diff = ((current_price - vwap) / vwap * 100) if vwap > 0 else 0
    vwap_above = current_price > vwap if vwap > 0 else False
    if vwap_above:
        signals.append("🟢 股價在 VWAP 上方 Price above VWAP — 多頭主導")
        w_score += C.ML_WSCORE_VWAP_ABOVE
        w_breakdown.append({"dim": "VWAP", "pts": C.ML_WSCORE_VWAP_ABOVE,
                            "note": f"股價在 VWAP ${vwap:.2f} 上方 ({vwap_diff:+.1f}%)"})
    elif vwap > 0:
        signals.append("🔴 股價在 VWAP 下方 Price below VWAP — 空方主導")
        w_score += C.ML_WSCORE_VWAP_BELOW
        w_breakdown.append({"dim": "VWAP", "pts": C.ML_WSCORE_VWAP_BELOW,
                            "note": f"股價在 VWAP ${vwap:.2f} 下方 ({vwap_diff:+.1f}%)"})
    else:
        w_breakdown.append({"dim": "VWAP", "pts": 0, "note": "VWAP 數據不可用"})

    # EMA position (Chapter 5, 7)
    ema9_diff = ((current_price - ema9) / ema9 * 100) if ema9 > 0 else 0
    ema21_diff = ((current_price - ema21) / ema21 * 100) if ema21 > 0 else 0
    above_ema9 = current_price > ema9 if ema9 > 0 else False
    above_ema21 = current_price > ema21 if ema21 > 0 else False

    if above_ema9:
        w_score += C.ML_WSCORE_EMA9_ABOVE
        signals.append(f"🟢 股價在 EMA9 ${ema9:.2f} 上方")
    else:
        w_score += C.ML_WSCORE_EMA9_BELOW
        signals.append(f"🔴 股價跌穿 EMA9 ${ema9:.2f}")

    if above_ema21:
        w_score += C.ML_WSCORE_EMA21_ABOVE
        signals.append(f"🟢 股價在 EMA21 ${ema21:.2f} 上方")
    else:
        w_score += C.ML_WSCORE_EMA21_BELOW
        signals.append(f"🔴 股價跌穿 EMA21 ${ema21:.2f} — 關鍵支撐失守")
    w_breakdown.append({"dim": "EMA 排列",
                        "pts": (C.ML_WSCORE_EMA9_ABOVE if above_ema9 else C.ML_WSCORE_EMA9_BELOW) +
                               (C.ML_WSCORE_EMA21_ABOVE if above_ema21 else C.ML_WSCORE_EMA21_BELOW),
                        "note": f"EMA9 {'▲' if above_ema9 else '▼'} EMA21 {'▲' if above_ema21 else '▼'}"})

    # ORH breakout / breakdown (Chapter 7)
    orh_broken = orl_broken = False
    orh_candle_count = max(1, C.ML_WATCH_ORH_WINDOW_MIN // 5)
    if len(candles_5m) > orh_candle_count and orh > 0:
        rest = candles_5m[orh_candle_count:]
        orh_broken = any(c["close"] > orh for c in rest)
        orl_broken = any(c["close"] < orl for c in rest)

    if orh_broken:
        w_score += C.ML_WSCORE_ORH_BREAK
        signals.append(f"🟢 突破 ORH ${orh:.2f} — Opening Range High 突破確認")
        w_breakdown.append({"dim": "ORH 突破", "pts": C.ML_WSCORE_ORH_BREAK, "note": f"ORH ${orh:.2f} 已突破"})
    elif orl_broken:
        w_score += C.ML_WSCORE_ORL_BREAK
        signals.append(f"🔴 跌破 ORL ${orl:.2f} — Opening Range Low 失守")
        w_breakdown.append({"dim": "ORH 突破", "pts": C.ML_WSCORE_ORL_BREAK, "note": f"ORL ${orl:.2f} 已跌破"})
    else:
        w_breakdown.append({"dim": "ORH 突破", "pts": 0, "note": "ORH 範圍內整理中"})

    # Chase distance from LOD (Chapter 9)
    chase_pct = ((current_price - lod) / lod * 100) if lod > 0 else 0
    chase_safe = chase_pct <= C.ML_WATCH_MAX_CHASE_PCT
    if chase_safe:
        w_score += C.ML_WSCORE_CHASE_OK
        w_breakdown.append({"dim": "追價距離", "pts": C.ML_WSCORE_CHASE_OK,
                            "note": f"距 LOD {chase_pct:.1f}% — 安全入場區"})
    else:
        w_score += C.ML_WSCORE_CHASE_HIGH
        signals.append(f"🔴 追價過高 距 LOD {chase_pct:.1f}% — Martin 規則：>{C.ML_WATCH_MAX_CHASE_PCT}% 禁入場")
        w_breakdown.append({"dim": "追價距離", "pts": C.ML_WSCORE_CHASE_HIGH,
                            "note": f"距 LOD {chase_pct:.1f}% — 超過安全線"})
        w_iron_rules.append({"rule": "CHASE_TOO_HIGH",
                             "msg": f"追價 {chase_pct:.1f}% 超過 {C.ML_WATCH_MAX_CHASE_PCT}% 上限",
                             "severity": "warn"})

    # Flush → V-recovery (Chapter 7)
    flush_detected = False
    flush_depth = flush_recovery = 0
    if len(candles_5m) >= 4:
        open_px = candles_5m[0]["open"]
        flush_window = candles_5m[:max(3, C.ML_FLUSH_MAX_MINUTES // 5)]
        min_low = min(c["low"] for c in flush_window)
        flush_depth = ((open_px - min_low) / open_px * 100) if open_px > 0 else 0
        if flush_depth >= C.ML_WATCH_FLUSH_V_MIN_PCT:
            recovery = (current_price - min_low) / (open_px - min_low) if open_px != min_low else 0
            flush_recovery = recovery
            if recovery >= C.ML_WATCH_FLUSH_V_RECOVERY_PCT:
                flush_detected = True
                w_score += C.ML_WSCORE_FLUSH_V
                signals.append(f"🟢 Flush→V 反彈信號 深度 {flush_depth:.1f}% 恢復 {recovery * 100:.0f}%")
                w_breakdown.append({"dim": "Flush V", "pts": C.ML_WSCORE_FLUSH_V,
                                    "note": f"V 形反彈確認 (深度{flush_depth:.1f}%)"})

    # Higher lows intraday (Chapter 7)
    hl_count = 0
    hl_valid = False
    hl_trend = "flat"
    if len(candles_5m) >= 6:
        swing_lows: list = []
        for i in range(1, len(candles_5m) - 1):
            if candles_5m[i]["low"] < candles_5m[i - 1]["low"] and candles_5m[i]["low"] < candles_5m[i + 1]["low"]:
                swing_lows.append(candles_5m[i]["low"])
        if len(swing_lows) >= 2:
            hl_count = sum(1 for i in range(1, len(swing_lows)) if swing_lows[i] > swing_lows[i - 1])
            hl_valid = hl_count >= 2
            hl_trend = "ascending" if hl_valid else ("mixed" if hl_count > 0 else "descending")

    if hl_valid:
        w_score += C.ML_WSCORE_HL_CONFIRMED
        signals.append(f"🟢 更高低點 Higher lows ({hl_count}) — 上升結構")
        w_breakdown.append({"dim": "低點結構", "pts": C.ML_WSCORE_HL_CONFIRMED, "note": f"{hl_count} 個更高低點"})
    elif hl_trend == "descending":
        w_score += C.ML_WSCORE_HL_LOWER
        signals.append("🔴 低點下移 — 弱勢結構")
        w_breakdown.append({"dim": "低點結構", "pts": C.ML_WSCORE_HL_LOWER, "note": "低點持續下移"})
    else:
        w_breakdown.append({"dim": "低點結構", "pts": 0, "note": "結構不明"})

    # AVWAP intraday position (Chapter 6)
    above_avwap_high = current_price > avwap_high if avwap_high and avwap_high > 0 else None
    above_avwap_low = current_price > avwap_low if avwap_low and avwap_low > 0 else None
    avwap_position = "UNKNOWN"
    if above_avwap_high and above_avwap_low:
        avwap_position = "ABOVE_BOTH"
        signals.append("🟢 股價在雙 AVWAP 上方 — 趨勢健康")
    elif above_avwap_low and not above_avwap_high:
        avwap_position = "BETWEEN"
        signals.append("⚠️ 股價在支撐/阻力 AVWAP 之間 — 等待方向")
    elif not above_avwap_low and not above_avwap_high:
        avwap_position = "BELOW_BOTH"
        signals.append("🔴 股價在雙 AVWAP 下方 — 空方主導")

    # Market regime from daily analysis (Dim G)
    if daily_dim_g_score >= 0:
        w_score += C.ML_WSCORE_MKT_BULL
        w_breakdown.append({"dim": "市場環境", "pts": C.ML_WSCORE_MKT_BULL, "note": "日線市場環境有利"})
    else:
        w_score += C.ML_WSCORE_MKT_BEAR
        w_breakdown.append({"dim": "市場環境", "pts": C.ML_WSCORE_MKT_BEAR, "note": "日線市場環境不利"})
        if daily_dim_g_score <= -0.5:
            w_iron_rules.append({"rule": "MARKET_BEARISH",
                                 "msg": "市場環境嚴重不利 — 建議觀望不入場",
                                 "severity": "block"})

    # Clamp 0-100
    w_score = max(0, min(100, w_score + 50))

    # Setup advice
    n_candles = len(candles_5m)
    if n_candles <= 1:
        setup_advice = "premarket_plan"
    elif n_candles <= 3:
        setup_advice = "early_entry_watch"
    elif flush_detected:
        setup_advice = "flush_v_recovery"
    elif not chase_safe:
        setup_advice = "avoid_chase"
    elif orh_broken and above_ema9 and vwap_above:
        setup_advice = "strong_entry"
    elif above_ema21 and vwap_above:
        setup_advice = "mid_entry_breakout"
    else:
        setup_advice = "monitoring"

    # Signal counts
    bullish = sum(1 for s in signals if "🟢" in s)
    bearish = sum(1 for s in signals if "🔴" in s)
    neutral = sum(1 for s in signals if "⚠️" in s)

    return {
        "active_signals": signals,
        "signal_counts": {"total": len(signals), "bullish": bullish, "bearish": bearish, "neutral": neutral},
        "vwap_status": {"price": round(vwap, 2) if vwap else None, "diff_pct": round(vwap_diff, 2), "above": vwap_above},
        "ema_status": {"ema9": round(ema9, 2) if ema9 else None, "ema9_diff": round(ema9_diff, 2),
                       "ema21": round(ema21, 2) if ema21 else None, "ema21_diff": round(ema21_diff, 2),
                       "above_both": above_ema9 and above_ema21},
        "orh_status": {"orh": round(orh, 2) if orh else None, "orl": round(orl, 2) if orl else None,
                       "orh_broken": orh_broken, "orl_broken": orl_broken},
        "chase_status": {"chase_pct": round(chase_pct, 2), "safe": chase_safe},
        "flush_v_status": {"detected": flush_detected, "depth_pct": round(flush_depth, 2),
                           "recovery_pct": round(flush_recovery * 100, 1)},
        "higher_lows": {"count": hl_count, "valid": hl_valid, "trend": hl_trend},
        "avwap_intraday": {
            "high": round(avwap_high, 2) if avwap_high else None,
            "low": round(avwap_low, 2) if avwap_low else None,
            "above_high": above_avwap_high, "above_low": above_avwap_low,
            "position": avwap_position},
        "watch_score": w_score,
        "watch_breakdown": w_breakdown,
        "watch_iron_rules": w_iron_rules,
        "setup_advice": setup_advice,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ML Watch Signals route
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/api/ml/watch_signals/<ticker>")
def ml_watch_signals(ticker: str):
    """ML 盯盤模式 — real-time intraday signal bundle for Martin Luk watch mode."""
    import datetime
    try:
        ticker = ticker.upper().strip()
        _data_source = "intraday"

        import yfinance as yf

        try:
            df_5m = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=False)
            candles_5m = _df_to_candles(df_5m) if not df_5m.empty else []
        except Exception:
            candles_5m = []

        # Daily data for AVWAP, EMA, ATR
        df_d = None
        avwap_high_val = avwap_low_val = None
        daily_dim_g = 0.0
        try:
            from modules.data_pipeline import (
                get_historical, get_atr,
                get_avwap_from_swing_high, get_avwap_from_swing_low,
            )
            df_d = get_historical(ticker, period="6mo")
            if df_d is not None and not df_d.empty:
                for n in (9, 21, 50, 150):
                    df_d[f"EMA_{n}"] = df_d["Close"].ewm(span=n, adjust=False).mean()
                ah = get_avwap_from_swing_high(df_d)
                al = get_avwap_from_swing_low(df_d)
                avwap_high_val = ah.get("avwap_value") if ah else None
                avwap_low_val = al.get("avwap_value") if al else None

                try:
                    from modules.db import query_market_env_history
                    _mkt_df = query_market_env_history(days=7)
                    mkt_regime = ""
                    if _mkt_df is not None and not _mkt_df.empty:
                        mkt_regime = str(_mkt_df.iloc[0].get("regime", ""))
                    g_map = {
                        "BULL_CONFIRMED": 0.8, "CONFIRMED_UPTREND": 0.8,
                        "BULL_UNCONFIRMED": 0.4,
                        "BOTTOM_FORMING": 0.3, "BULL_EARLY": 0.3,
                        "TRANSITION": 0.0, "UPTREND_UNDER_PRESSURE": 0.0,
                        "BEAR_RALLY": -0.5, "CHOPPY": -0.5,
                        "BEAR_CONFIRMED": -1.5, "MARKET_IN_CORRECTION": -1.5,
                        "DOWNTREND": -2.0,
                    }
                    daily_dim_g = g_map.get(mkt_regime, 0.0)
                except Exception:
                    daily_dim_g = 0.0
        except Exception as _e:
            logger.warning("ML watch daily data failed for %s: %s", ticker, _e)

        # Session extremes
        hod = lod = orh = orl = None
        prev_close = None
        if candles_5m:
            hod = max(c["high"] for c in candles_5m)
            lod = min(c["low"] for c in candles_5m)
            orh_n = max(1, C.ML_WATCH_ORH_WINDOW_MIN // 5)
            if len(candles_5m) >= orh_n:
                orh = max(c["high"] for c in candles_5m[:orh_n])
                orl = min(c["low"] for c in candles_5m[:orh_n])
        elif df_d is not None and not df_d.empty:
            _data_source = "fallback_daily"
            hod = float(df_d["High"].iloc[-1])
            lod = float(df_d["Low"].iloc[-1])

        if df_d is not None and not df_d.empty and len(df_d) >= 2:
            prev_close = float(df_d["Close"].iloc[-2])

        # Compute intraday EMA/VWAP from 5m candles
        ema9_val = ema21_val = vwap_val = 0
        if candles_5m:
            closes = [c["close"] for c in candles_5m]
            k9, k21 = 2.0 / 10, 2.0 / 22
            if len(closes) >= 9:
                ema = sum(closes[:9]) / 9
                for c in closes[9:]:
                    ema = c * k9 + ema * (1 - k9)
                ema9_val = ema
            if len(closes) >= 21:
                ema = sum(closes[:21]) / 21
                for c in closes[21:]:
                    ema = c * k21 + ema * (1 - k21)
                ema21_val = ema
            tp_vol_sum = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in candles_5m)
            vol_sum = sum(c["volume"] for c in candles_5m)
            if vol_sum > 0:
                vwap_val = tp_vol_sum / vol_sum

        # Run ML signal engine
        ml_signals = _get_ml_intraday_signals(
            candles_5m=candles_5m,
            ema9=ema9_val, ema21=ema21_val, vwap=vwap_val,
            orh=orh or 0, orl=orl or 0, lod=lod or 0, hod=hod or 0,
            prev_close=prev_close or 0,
            avwap_high=avwap_high_val or 0, avwap_low=avwap_low_val or 0,
            daily_dim_g_score=daily_dim_g,
        )

        current_price = candles_5m[-1]["close"] if candles_5m else (
            float(df_d["Close"].iloc[-1]) if df_d is not None and not df_d.empty else None)
        last_ts = candles_5m[-1]["time"] if candles_5m else None

        daily_emas = {}
        if df_d is not None and not df_d.empty:
            for n in (9, 21, 50, 150):
                col = f"EMA_{n}"
                if col in df_d.columns:
                    daily_emas[f"ema{n}"] = round(float(df_d[col].iloc[-1]), 2)

        return jsonify(_clean({
            "ok": True, "ticker": ticker,
            "current_price": current_price, "last_ts": last_ts,
            "hod": hod, "lod": lod, "orh": orh, "orl": orl,
            "prev_close": prev_close,
            "data_source": _data_source,
            "refresh_ts": int(_time.time()),
            "daily_emas": daily_emas,
            "avwap_high": round(avwap_high_val, 2) if avwap_high_val else None,
            "avwap_low": round(avwap_low_val, 2) if avwap_low_val else None,
            **ml_signals,
        }))

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Intraday chart — 5m, 15m, 1h (Martin Luk 盤中)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_intraday_signals(candles: list, orh: float, orl: float, lod: float, hod: float,
                          ema9_val: float, ema21_val: float, vwap_val: float,
                          premarket_end_candle_count: int) -> dict:
    """Extract real-time signals for intraday trading per Martin Luk method."""
    if not candles:
        return {"setup_advice": "no_data"}

    curr_close = candles[-1].get("close", 0)
    chase_pct = ((curr_close - lod) / lod * 100.0) if lod > 0 else 0.0

    orh_broken = False
    for i, candle in enumerate(candles):
        if i > premarket_end_candle_count and candle.get("high", 0) >= orh:
            orh_broken = True
            break

    min_price_in_first_hour = min([c.get("low", float("inf")) for c in candles[:12]])
    flush_recovery = (min_price_in_first_hour <= ema21_val <= (min_price_in_first_hour + ema21_val) * 0.02) and curr_close > ema21_val

    ema9_diff = ((curr_close - ema9_val) / ema9_val * 100.0) if ema9_val > 0 else 0
    ema21_diff = ((curr_close - ema21_val) / ema21_val * 100.0) if ema21_val > 0 else 0
    vwap_diff = ((curr_close - vwap_val) / vwap_val * 100.0) if vwap_val > 0 else 0

    if len(candles) <= premarket_end_candle_count:
        setup_advice = "premarket_plan"
    elif premarket_end_candle_count < len(candles) <= 3:
        setup_advice = "early_entry_watch"
    elif 3 < len(candles) <= 12 and flush_recovery:
        setup_advice = "mid_entry_breakout"
    elif chase_pct > 3.0:
        setup_advice = "avoid_chase"
    elif len(candles) > 80:
        setup_advice = "closed"
    else:
        setup_advice = "mid_entry_hold"

    return {
        "curr_close": round(curr_close, 2),
        "chase_pct": round(chase_pct, 2),
        "orh_broken": orh_broken,
        "flush_recovery": flush_recovery,
        "ema9_diff": round(ema9_diff, 2),
        "ema21_diff": round(ema21_diff, 2),
        "vwap_diff": round(vwap_diff, 2),
        "setup_advice": setup_advice,
    }


@bp.route("/api/chart/intraday/<ticker>", methods=["GET"])
def api_chart_intraday(ticker: str):
    """Intraday OHLCV + EMA + VWAP for 5m, 15m, 1h charts."""
    import pandas as pd
    import pytz

    interval = request.args.get("interval", "5m")
    days = int(request.args.get("days", 2 if interval in ("5m", "15m") else 60))
    ticker_upper = ticker.upper()

    def _is_premarket(ts_unix: int) -> bool:
        try:
            dt = pd.Timestamp(ts_unix, unit="s", tz="UTC").astimezone(pytz.timezone("US/Eastern"))
            return dt.hour < 9 or (dt.hour == 9 and dt.minute < 30)
        except Exception:
            return False

    try:
        import yfinance as yf

        t = yf.Ticker(ticker_upper)
        df = t.history(period=f"{days}d", interval=interval, prepost=True)
        if df.empty:
            return jsonify({"ok": False, "error": f"No {interval} data"})

        for n in (9, 21):
            df[f"EMA_{n}"] = df["Close"].ewm(span=n, adjust=False).mean()

        df["typical_price"] = (df["High"] + df["Low"] + df["Close"]) / 3
        df["tp_x_vol"] = df["typical_price"] * df["Volume"]
        df["cumul_tp_vol"] = df["tp_x_vol"].cumsum()
        df["cumul_vol"] = df["Volume"].cumsum()

        et_tz = pytz.timezone("US/Eastern")
        dti: pd.DatetimeIndex = df.index  # type: ignore[assignment]
        if hasattr(dti, "tz_convert"):
            df["date_et"] = dti.tz_convert(et_tz).normalize()  # type: ignore[attr-defined]
        else:
            df["date_et"] = dti.normalize() if hasattr(dti, "normalize") else pd.Series(dti.date, index=df.index)  # type: ignore[attr-defined]

        for date in df["date_et"].unique():
            date_mask = df["date_et"] == date
            date_df = df[date_mask]
            if len(date_df) > 0:
                first_post_idx = None
                for i, ts in enumerate(date_df.index):  # type: ignore[attr-defined]
                    dt_et = ts.tz_convert(et_tz)
                    if dt_et.hour >= 9 and dt_et.minute >= 30:
                        first_post_idx = i
                        break
                if first_post_idx is not None:
                    mask_d = df["date_et"] == date
                    sub = df[mask_d]
                    df.loc[mask_d, "cumul_tp_vol"] = sub["tp_x_vol"].cumsum().to_numpy()
                    df.loc[mask_d, "cumul_vol"] = sub["Volume"].cumsum().to_numpy()

        df["vwap"] = df["cumul_tp_vol"] / df["cumul_vol"].replace(0, float("nan"))

        today_et = pd.Timestamp.now(tz=et_tz).normalize()
        today_mask = df["date_et"] == today_et
        today_df = df[today_mask]

        orh_val = orl_val = lod_val = hod_val = None
        if len(today_df) > 0:
            orh_val = _sf(today_df.head(3)["High"].max())
            orl_val = _sf(today_df.head(3)["Low"].min())
            lod_val = _sf(today_df["Low"].min())
            hod_val = _sf(today_df["High"].max())

        candles, volume_pts = [], []
        ema9_pts, ema21_pts, vwap_pts = [], [], []
        premarket_count = 0

        for idx, row in df.iterrows():
            try:
                ts = int(pd.Timestamp(str(idx)).timestamp())
            except Exception:
                continue
            o = _sf(row.get("Open"))
            h = _sf(row.get("High"))
            lo = _sf(row.get("Low"))
            c = _sf(row.get("Close"))
            v = int(row.get("Volume") or 0)
            if None in (o, h, lo, c):
                continue

            is_pm = _is_premarket(ts)
            if is_pm:
                premarket_count += 1

            o_id = cast(float, o)  # None excluded by continue above
            h_id = cast(float, h)
            lo_id = cast(float, lo)
            c_id = cast(float, c)
            candles.append({"time": ts, "open": o_id, "high": h_id, "low": lo_id, "close": c_id, "is_premarket": is_pm})
            volume_pts.append({"time": ts, "value": v,
                               "color": "#3fb95033" if is_pm else ("#3fb950" if c_id >= o_id else "#f85149")})

            e9 = _sf(row.get("EMA_9"))
            if e9 is not None:
                ema9_pts.append({"time": ts, "value": e9})
            e21 = _sf(row.get("EMA_21"))
            if e21 is not None:
                ema21_pts.append({"time": ts, "value": e21})
            vwap_v = _sf(row.get("vwap"))
            if vwap_v is not None:
                vwap_pts.append({"time": ts, "value": vwap_v})

        curr_ema9 = ema9_pts[-1]["value"] if ema9_pts else 0
        curr_ema21 = ema21_pts[-1]["value"] if ema21_pts else 0
        curr_vwap = vwap_pts[-1]["value"] if vwap_pts else 0

        intraday_signals = _get_intraday_signals(
            candles, orh_val or 0, orl_val or 0, lod_val or 0, hod_val or 0,
            curr_ema9, curr_ema21, curr_vwap, premarket_count,
        )

        return jsonify({
            "ok": True, "ticker": ticker_upper, "interval": interval,
            "candles": candles, "volume": volume_pts,
            "ema9": ema9_pts, "ema21": ema21_pts, "vwap": vwap_pts,
            "orh": orh_val, "orl": orl_val, "lod": lod_val, "hod": hod_val,
            "chase_pct": intraday_signals.get("chase_pct", 0),
            "signals": intraday_signals,
            "market_date": pd.Timestamp.now(tz=et_tz).strftime("%Y-%m-%d"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
