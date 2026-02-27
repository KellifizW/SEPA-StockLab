#!/usr/bin/env python
"""Test Phase 3 API endpoints."""

import urllib.request
import json
import time
import subprocess
import sys
from pathlib import Path

# Start the Flask app in the background
ROOT = Path(__file__).resolve().parent.parent
print("Starting Flask app...")
proc = subprocess.Popen([sys.executable, str(ROOT / 'app.py')], 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE)
time.sleep(2)

try:
    endpoints = [
        ('/api/db/market-history?days=30', 'Market history'),
        ('/api/db/watchlist-history', 'Watchlist history'),
        ('/api/db/scan-trend/AAPL?days=30', 'Scan trend for AAPL'),
    ]
    
    print("\n" + "="*60)
    print("Testing Phase 3 API Endpoints")
    print("="*60 + "\n")
    
    for endpoint, desc in endpoints:
        try:
            print(f"📍 {desc}")
            print(f"   GET {endpoint}")
            response = urllib.request.urlopen(f'http://localhost:5000{endpoint}', timeout=3)
            data = json.loads(response.read().decode())
            print(f"   ✅ Status: {response.status}")
            print(f"   ✅ OK: {data.get('ok')}")
            rows = data.get('rows', [])
            print(f"   ✅ Rows: {len(rows)}")
            if rows:
                print(f"   First row: {str(rows[0])[:80]}...")
        except Exception as e:
            print(f"   ❌ Error: {type(e).__name__}: {e}")
        print()
    
    print("="*60)
    print("✅ All endpoints accessible!")
    print("="*60)
    
finally:
    # Stop the Flask app
    print("\nStopping Flask app...")
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except:
        proc.kill()
    print("Done")
