"""
Test actual page rendering from Flask.
"""
import sys
from pathlib import Path
import io

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Configure Flask to use test client
from app import app

# Create test client
client = app.test_client()

print("=" * 70)
print("TESTING PAGE RENDERING")
print("=" * 70)

# Test 1: GET /ml/analyze
print("\n[TEST 1] GET /ml/analyze (no ticker)")
try:
    resp = client.get('/ml/analyze')
    print(f"  Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        html = resp.get_data(as_text=True)
        print(f"  Response size: {len(html)} bytes")
        
        # Check for critical content
        if '<html' in html.lower():
            print("  ✓ Valid HTML returned")
        if '<!DOCTYPE' in html:
            print("  ✓ DOCTYPE found")
        if 'switchMlMode' in html:
            print("  ✓ JavaScript function switchMlMode present")
        if 'watchPanel' in html:
            print("  ✓ Watch panel HTML present")
        if 'ML 個股分析' in html or 'ML' in html:
            print("  ✓ Title/content present")
        if '<title>' in html.lower():
            title = html[html.lower().find('<title>')+7:html.lower().find('</title>')].strip()
            print(f"  ✓ Page title: {title}")
        if 'Error' in html or 'error' in html:
            print("  ⚠ Page contains 'error' text (may be expected)")
    else:
        print(f"  ✗ Unexpected status code")
        print(f"  Response: {resp.get_data(as_text=True)[:500]}")
        
except Exception as e:
    print(f"  ✗ Exception: {e}")
    import traceback
    traceback.print_exc()

# Test 2: GET /ml/analyze?ticker=AAPL
print("\n[TEST 2] GET /ml/analyze?ticker=AAPL")
try:
    resp = client.get('/ml/analyze?ticker=AAPL')
    print(f"  Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        html = resp.get_data(as_text=True)
        if 'AAPL' in html:
            print("  ✓ Ticker AAPL in response")
        print(f"  ✓ Response size: {len(html)} bytes")
    else:
        print(f"  ✗ Unexpected status: {resp.status_code}")
        
except Exception as e:
    print(f"  ✗ Exception: {e}")

# Test 3: POST /api/ml/analyze with AAPL
print("\n[TEST 3] POST /api/ml/analyze (AAPL)")
try:
    resp = client.post('/api/ml/analyze', 
                       json={'ticker': 'AAPL'},
                       content_type='application/json')
    print(f"  Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            if data.get('ok'):
                print("  ✓ API returned ok=True")
                result = data.get('result', {})
                if result:
                    print(f"  ✓ Result data present")
                    if 'capped_stars' in result:
                        print(f"    - Stars: {result.get('capped_stars')}")
                    if 'recommendation' in result:
                        print(f"    - Recommendation: {result.get('recommendation')}")
            else:
                error = data.get('error', 'Unknown error')
                print(f"  ✗ API returned ok=False: {error}")
        else:
            print(f"  ✗ No JSON response")
    else:
        print(f"  ✗ Status: {resp.status_code}")
        print(f"  Response: {resp.get_data(as_text=True)[:200]}")
        
except Exception as e:
    print(f"  ✗ Exception: {e}")
    import traceback
    traceback.print_exc()

# Test 4: GET /api/chart/intraday/AAPL
print("\n[TEST 4] GET /api/chart/intraday/AAPL?interval=5m")
try:
    resp = client.get('/api/chart/intraday/AAPL?interval=5m')
    print(f"  Status Code: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.get_json()
        if data:
            if data.get('ok'):
                print("  ✓ API returned ok=True")
                if 'candles' in data:
                    print(f"    - Candles: {len(data.get('candles', []))} data points")
                if 'ema9' in data:
                    print(f"    - EMA9: {len(data.get('ema9', []))} data points")
                if 'signals' in data:
                    print(f"    - Signals: {data.get('signals', {}).get('setup_advice')}")
            else:
                print(f"  ✗ API error: {data.get('error')}")
    else:
        print(f"  ✗ Status: {resp.status_code}")
        
except Exception as e:
    print(f"  ✗ Exception: {e}")

print("\n" + "=" * 70)
print("RENDERING TEST COMPLETE")
print("=" * 70)
print("""
If all tests passed (✓):
  - Server/routes are working
  - Templates are rendering
  - APIs are functioning

Next step: Start the server and test in browser:
  python run_app.py
  Then open: http://127.0.0.1:5000/ml/analyze

If you see an error message, please:
  1. Share the error text
  2. Open DevTools (F12) and check Console tab
  3. Share any red error messages
""")
