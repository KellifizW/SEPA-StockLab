#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Debug ATR gate issue in QM watch mode"""
import sys
sys.path.insert(0, '.')
import math as _math

ticker = 'NVDA'

# Step 1: Fetch 5m candles
print('[1] Fetching 5m candles...')
import yfinance as yf

def _df_to_candles(df):
    result = []
    for ts, row in df.iterrows():
        t_unix = int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(ts)
        o = row.get('Open')
        h = row.get('High')
        l = row.get('Low')
        c = row.get('Close')
        v = row.get('Volume', 0)
        if any(x is None or (isinstance(x, float) and (_math.isnan(x) or _math.isinf(x))) for x in [o, h, l, c]):
            continue
        result.append({'time': t_unix, 'open': float(o), 'high': float(h),
                        'low': float(l), 'close': float(c), 'volume': int(v or 0)})
    return result

try:
    df_5m = yf.Ticker(ticker).history(period='2d', interval='5m', prepost=False)
    candles_5m = _df_to_candles(df_5m) if not df_5m.empty else []
except Exception as e:
    candles_5m = []
    print(f'    ERROR fetching 5m: {e}')

print(f'    candles_5m count: {len(candles_5m)}')
if candles_5m:
    print(f'    last candle: {candles_5m[-1]}')
    lod = min(c['low'] for c in candles_5m)
    hod = max(c['high'] for c in candles_5m)
    current_price = candles_5m[-1]['close']
    print(f'    LOD={lod}, HOD={hod}, current_price={current_price}')
else:
    lod = hod = current_price = None
    print('    NO 5m candles!')

# Step 2: Fetch daily ATR
print()
print('[2] Fetching daily ATR via get_historical + get_atr...')
try:
    from modules.data_pipeline import get_historical, get_atr
    df_d = get_historical(ticker, period='3mo')
    print(f'    df_d shape: {df_d.shape if df_d is not None else None}')
    
    if df_d is not None and not df_d.empty and len(df_d) >= 2:
        atr_daily = get_atr(df_d)
        print(f'    get_atr() = {atr_daily}')
        if not atr_daily or atr_daily <= 0:
            atr_raw = (df_d['High'] - df_d['Low']).rolling(14).mean().iloc[-1]
            atr_daily = float(atr_raw) if atr_raw == atr_raw else None
            print(f'    fallback atr_daily = {atr_daily}')
        prev_close = float(df_d['Close'].iloc[-2])
        current_open = float(df_d['Open'].iloc[-1])
        gap_pct = (current_open - prev_close) / prev_close * 100 if prev_close else 0.0
        print(f'    prev_close={prev_close}, current_open={current_open}, gap_pct={gap_pct:.2f}%')
    else:
        atr_daily = None
        print('    df_d is empty or too short!')
except Exception as e:
    atr_daily = None
    print(f'    ERROR: {e}')

# Step 3: Simulate the ATR gate condition
print()
print('[3] Testing ATR gate condition...')
print(f'    current_price = {current_price}, type = {type(current_price)}')
print(f'    atr_daily     = {atr_daily}, type = {type(atr_daily)}')
print(f'    lod           = {lod}, type = {type(lod)}')
print()

c1 = bool(current_price)
c2 = bool(atr_daily) if atr_daily is not None else False
c3 = (atr_daily > 0) if atr_daily is not None else False
c4 = bool(lod)

print(f'    bool(current_price) = {c1}')
print(f'    bool(atr_daily)     = {c2}')
print(f'    atr_daily > 0       = {c3}')
print(f'    bool(lod)           = {c4}')
print()

if c1 and c2 and c3 and c4:
    dist_frac = (current_price - lod) / atr_daily
    print(f'    ✅ ATR GATE PASSES! dist_frac = {dist_frac:.3f}')
else:
    print(f'    ❌ ATR GATE FAILS! Check which condition is False above.')

# Step 4: Check what _get_qm_intraday_signals returns for atr_gate
print()
print('[4] Checking what _get_qm_intraday_signals returns...')
try:
    # Call the actual route
    import requests
    resp = requests.get(f'http://localhost:5000/api/qm/watch_signals/{ticker}', timeout=15)
    data = resp.json()
    print(f'    Response ok: {data.get("ok")}')
    atr_gate = data.get('atr_gate', {})
    print(f'    atr_gate: {atr_gate}')
    print(f'    atr_gate["atr"] = {atr_gate.get("atr")}')
    print(f'    atr_gate["chase_status"] = {atr_gate.get("chase_status")}')
except Exception as e:
    print(f'    Cannot test API (Flask not running): {e}')
    print('    Skipping API test - run Flask first to verify.')
