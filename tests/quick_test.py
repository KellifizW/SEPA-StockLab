#!/usr/bin/env python
"""Quick test of backtest API after template fix."""

import requests
import json

print("Testing backtest API...")
resp = requests.post("http://localhost:5000/api/backtest/run", json={
    "ticker": "NVDA",
    "min_vcp_score": 35,
    "outcome_days": 60
})

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")

if resp.status_code == 200:
    data = resp.json()
    print(f"\n✓ Success!")
    print(f"  Job ID: {data.get('job_id')}")
    print(f"  Log file: {data.get('log_file')}")
else:
    print(f"\n❌ Error: {resp.status_code}")
