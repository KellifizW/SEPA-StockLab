#!/usr/bin/env python
"""Test backtest API with logging."""

import requests
import time

print("[1] Submitting backtest for META...")
resp = requests.post("http://localhost:5000/api/backtest/run", json={
    "ticker": "META",
    "min_vcp_score": 35,
    "outcome_days": 60
})
data = resp.json()
print(f"    Job ID: {data['job_id']}")
print(f"    Log file: {data['log_file']}")

jid = data['job_id']

print("\n[2] Polling backtest progress...")
for i in range(30):
    resp = requests.get(f"http://localhost:5000/api/backtest/status/{jid}")
    status_data = resp.json()
    pct = status_data.get('pct', 0)
    msg = status_data.get('msg', '')
    status = status_data.get('status', '?')
    print(f"    [{pct:3d}%] {msg} [{status}]")
    if status == 'done':
        result = status_data.get('result', {})
        print(f"\n✓ Backtest complete!")
        print(f"  Signals: {result.get('summary',{}).get('total_signals')}")
        print(f"  Breakouts: {result.get('summary',{}).get('breakouts')}")
        print(f"  Win rate: {result.get('summary',{}).get('win_rate_pct')}%")
        break
    elif status == 'error':
        print(f"\n❌ Error: {status_data.get('error')}")
        break
    time.sleep(1)

print("\n[3] Checking LOG file...")
import os
log_file = f"logs/{data['log_file']}"
if os.path.exists(log_file):
    print(f"✓ LOG file exists: {log_file}")
    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    print(f"  Size: {len(lines)} lines")
    print(f"  First 3 lines:")
    for line in lines[:3]:
        print(f"    {line.rstrip()}")
    print(f"  Last 2 lines:")
    for line in lines[-2:]:
        print(f"    {line.rstrip()}")
else:
    print(f"❌ LOG file not found: {log_file}")
