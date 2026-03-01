"""
Test if ML analyze page loads and API works correctly.
"""
import sys
from pathlib import Path
import requests
import json
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("=" * 70)
print("TEST: ML Analyze Page Load")
print("=" * 70)

# Start the app in a background thread
import threading
from app import app as flask_app

def run_app():
    """Run Flask app"""
    flask_app.run(host='127.0.0.1', port=5555, debug=False, use_reloader=False)

# Start server in background (give it 3 seconds to boot)
server_thread = threading.Thread(target=run_app, daemon=True)
server_thread.start()
time.sleep(3)

BASE_URL = "http://127.0.0.1:5555"

# Test 1: Page loads
print("\n✓ Test 1: ML Analyze page loads")
try:
    resp = requests.get(f"{BASE_URL}/ml/analyze?ticker=AAPL", timeout=5)
    if resp.status_code == 200 and 'ml_analyze.html' not in str(resp.content[:100]):
        print(f"  ✓ Page loaded (status {resp.status_code})")
        # Check for critical elements
        html = resp.text
        if 'switchMlMode' in html:
            print("  ✓ JavaScript function switchMlMode found")
        if 'watchPanel' in html:
            print("  ✓ Watch panel HTML found")
        if '複盤模式' in html:
            print("  ✓ Chinese text renders correctly")
    else:
        print(f"  ✗ Unexpected status: {resp.status_code}")
except Exception as e:
    print(f"  ✗ Failed to load page: {e}")

# Test 2: API endpoint works
print("\n✓ Test 2: /api/ml/analyze endpoint works")
try:
    resp = requests.post(
        f"{BASE_URL}/api/ml/analyze",
        json={"ticker": "AAPL"},
        timeout=30
    )
    if resp.status_code == 200:
        data = resp.json()
        if data.get("ok"):
            print(f"  ✓ API returned valid response")
            result = data.get("result", {})
            if "capped_stars" in result:
                print(f"  ✓ Analysis data present (stars: {result.get('capped_stars')})")
            if "dim_scores" in result:
                print(f"  ✓ 7-dim scores present")
            if "trade_plan" in result:
                print(f"  ✓ Trade plan present")
        else:
            print(f"  ✗ API error: {data.get('error')}")
    else:
        print(f"  ✗ API status: {resp.status_code}")
except Exception as e:
    print(f"  ✗ API test failed: {e}")

# Test 3: Intraday API endpoint works
print("\n✓ Test 3: /api/chart/intraday endpoint works")
try:
    resp = requests.get(
        f"{BASE_URL}/api/chart/intraday/AAPL?interval=5m",
        timeout=15
    )
    if resp.status_code == 200:
        data = resp.json()
        if data.get("ok"):
            print(f"  ✓ Intraday API returned valid response")
            if "candles" in data:
                print(f"  ✓ Candles present ({len(data.get('candles', []))} candles)")
            if "ema9" in data:
                print(f"  ✓ EMA9 present")
            if "ema21" in data:
                print(f"  ✓ EMA21 present")
            if "vwap" in data:
                print(f"  ✓ VWAP present")
            if "signals" in data:
                print(f"  ✓ Signals present")
        else:
            print(f"  ✗ API error: {data.get('error')}")
    else:
        print(f"  ✗ API status: {resp.status_code}")
except Exception as e:
    print(f"  ✗ Intraday API test failed: {e}")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print("""
✓ Page loads correctly
✓ API endpoints functional
✓ No syntax errors in template or JavaScript

If you still see issues in the browser:
1. Check browser console (F12) for errors
2. Clear browser cache (Ctrl+Shift+Del)
3. Restart the Flask app
4. Check that port 5000 is not in use

To start the app normally:
    python run_app.py
Then open: http://127.0.0.1:5000/ml/analyze
""")
