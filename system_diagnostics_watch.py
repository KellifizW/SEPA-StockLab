#!/usr/bin/env python3
"""
Complete watch mode diagnostics - run this to check the system state
"""
import subprocess
import time
import requests
from pathlib import Path

def check_flask_running():
    """Check if Flask app is running"""
    try:
        r = requests.get('http://127.0.0.1:5000/', timeout=2)
        return r.status_code == 200
    except:
        return False

def check_api_endpoint():
    """Check if intraday API endpoint works"""
    try:
        r = requests.get('http://127.0.0.1:5000/api/chart/intraday/AEM?interval=5m', timeout=5)
        if r.status_code == 200:
            data = r.json()
            return {
                'ok': data.get('ok'),
                'candles': len(data.get('candles', [])),
                'ema9': len(data.get('ema9', [])),
                'ema21': len(data.get('ema21', []))
            }
        return None
    except Exception as e:
        return f'Error: {e}'

def check_ml_analyze_page():
    """Check if ML analyze page loads"""
    try:
        r = requests.get('http://127.0.0.1:5000/ml/analyze?ticker=AEM', timeout=5)
        if r.status_code == 200:
            size = len(r.content)
            has_lwc = 'LightweightCharts' in r.text
            has_watch_panel = 'id="watchPanel"' in r.text
            return {
                'status': 200,
                'size_kb': round(size / 1024, 1),
                'has_lwc': has_lwc,
                'has_watch_panel': has_watch_panel
            }
        return None
    except Exception as e:
        return f'Error: {e}'

def main():
    print("=" * 70)
    print("WATCH MODE SYSTEM DIAGNOSTICS")
    print("=" * 70)
    
    # Check Flask
    print("\n1️⃣  Flask App Status:")
    if check_flask_running():
        print("   ✅ Flask app is RUNNING at http://127.0.0.1:5000")
    else:
        print("   ❌ Flask app is NOT running")
        print("   → Start the app: python app.py")
        return
    
    # Check page load
    print("\n2️⃣  ML Analyze Page:")
    page_check = check_ml_analyze_page()
    if isinstance(page_check, dict):
        print(f"   ✅ Page loads ({page_check['size_kb']}KB)")
        print(f"      • LightweightCharts lib: {'✅' if page_check['has_lwc'] else '❌'} {page_check['has_lwc']}")
        print(f"      • watchPanel element: {'✅' if page_check['has_watch_panel'] else '❌'} {page_check['has_watch_panel']}")
    else:
        print(f"   ❌ Error: {page_check}")
        return
    
    # Check API
    print("\n3️⃣  Intraday API Endpoint:")
    api_check = check_api_endpoint()
    if isinstance(api_check, dict):
        print(f"   ✅ API returns data")
        print(f"      • Candles: {api_check['candles']}")
        print(f"      • EMA 9: {api_check['ema9']}")
        print(f"      • EMA 21: {api_check['ema21']}")
        if api_check['candles'] > 0:
            print("   ✅ All data available for chart rendering")
        else:
            print("   ⚠️  No candles returned - check if market has data")
    else:
        print(f"   ❌ Error: {api_check}")
    
    print("\n" + "=" * 70)
    print("NEXT STEP: Test in Browser")
    print("=" * 70)
    print("""
    1. Open this URL in your browser:
       http://127.0.0.1:5000/ml/analyze?ticker=AEM
    
    2. Open DevTools (F12) and go to Console tab
    
    3. Click "📡 盯盤模式 Watch Market" button
    
    4. Check Console for debug messages starting with:
       🔄 (function calls)
       ✅ (successful operations)
       ❌ (errors)
       📐 (dimension info)
    
    5. If chart doesn't appear:
       → Take screenshot of console output
       → Share it for further diagnosis
    
    Key values to check in console:
    ✓ "📐 Container clientWidth:" should be > 0 (not 0 or undefined)
    ✓ "✅ API data received:" should show candles > 0
    ✓ "✅ Chart created successfully" must appear
    ✓ No "❌" error messages
    """)

if __name__ == '__main__':
    main()
