#!/usr/bin/env python
"""Test position add via Flask API."""

import urllib.request
import json
import time

print("\nTesting position add via Flask API...\n")

# Wait for Flask to start
print("Waiting for Flask server...")
for i in range(10):
    try:
        urllib.request.urlopen('http://localhost:5000', timeout=1)
        print("✅ Flask is ready\n")
        break
    except:
        time.sleep(0.5)

# Test 1: Check current positions
print("1️⃣ Fetching current positions...")
try:
    response = urllib.request.urlopen('http://localhost:5000/api/positions')
    current = json.loads(response.read().decode())
    print(f"   Found {len(current)} existing positions")
    for ticker in list(current.keys())[:3]:
        print(f"   - {ticker}")
except Exception as e:
    print(f"   Error: {e}")

# Test 2: Add a new position
print("\n2️⃣ Adding new position TEST_API...")
try:
    data = json.dumps({
        'ticker': 'TEST_API',
        'buy_price': 150.0,
        'shares': 5,
        'stop_loss': 140.0,
        'target': 180.0,
        'note': 'Added via API test'
    }).encode()
    
    req = urllib.request.Request(
        'http://localhost:5000/api/positions/add',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    response = urllib.request.urlopen(req)
    result = json.loads(response.read().decode())
    
    if result.get('ok'):
        print("   ✅ Position added via API")
        positions = result.get('positions', {})
        if 'TEST_API' in positions:
            pos = positions['TEST_API']
            print(f"   Position data returned:")
            print(f"      buy_price: {pos.get('buy_price')}")
            print(f"      entry_date: {repr(pos.get('entry_date'))}")
            print(f"      note: {pos.get('note')}")
        else:
            print("   ⚠️  Position not in API response")
    else:
        print(f"   ❌ API error: {result.get('error')}")
        
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 3: Fetch again to verify persistence
print("\n3️⃣ Fetching positions to verify persistence...")
try:
    time.sleep(1)  # Wait for backend to persist
    response = urllib.request.urlopen('http://localhost:5000/api/positions')
    current = json.loads(response.read().decode())
    
    if 'TEST_API' in current:
        print(f"   ✅ TEST_API found after reload!")
        pos = current['TEST_API']
        print(f"      buy_price: {pos.get('buy_price')}")
        print(f"      entry_date: {repr(pos.get('entry_date'))}")
    else:
        print(f"   ❌ TEST_API not found in positions")
        print(f"      Available: {list(current.keys())}")
        
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n✅ Test complete\n")
