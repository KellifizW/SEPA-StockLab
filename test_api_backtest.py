#!/usr/bin/env python
"""Test the QM backtest via the Flask API."""

import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Import Flask app and test client
from app import app

client = app.test_client()

print("Testing QM backtest API endpoint...")
print("=" * 60)

# Test 1: Start backtest job
print("\n1. Starting backtest job...")
response = client.post('/api/qm/backtest/run', json={
    'ticker': 'PLTR',
    'debug': True,
    'min_star': 3.0
})

if response.status_code != 200:
    print(f"❌ Failed: {response.status_code}")
    print(response.get_json())
    sys.exit(1)

result = response.get_json()
print(f"✅ Job started: {result}")
job_id = result.get('job_id')

# Test 2: Poll job status
print(f"\n2. Checking job status (job_id={job_id})...")
import time
for i in range(30):  # Check for up to 30 seconds
    response = client.get(f'/api/qm/backtest/status/{job_id}')
    if response.status_code != 200:
        print(f"❌ Failed: {response.status_code}")
        break
    
    result = response.get_json()
    progress = result.get('progress', {})
    pct = progress.get('pct', 0)
    msg = progress.get('msg', '')
    status = result.get('status')
    
    print(f"  [{i}s] {status}: {pct}% - {msg}")
    
    if status == 'completed':
        backtest_result = result.get('result', {})
        print(f"\n✅ Backtest completed!")
        print(f"  Signals found: {len(backtest_result.get('signals', []))}")
        if len(backtest_result.get('signals', [])) > 0:
            print(f"  Summary: {backtest_result.get('summary', {})}")
        break
    
    if status == 'error':
        print(f"❌ Job error: {result.get('error')}")
        break
    
    time.sleep(1)
else:
    print("⏱️  Timeout - backtest took too long")

print("\n" + "=" * 60)
print("API test complete!")
