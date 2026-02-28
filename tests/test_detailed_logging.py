#!/usr/bin/env python
"""
Quick test: Run a backtest and verify LOG file contains detailed signal table.
"""
import requests
import time
import json
from pathlib import Path

BASE = "http://localhost:5000"
LOG_DIR = Path("../logs")

print("[*] Starting backtest for META...")
resp = requests.post(
    f"{BASE}/api/backtest/run",
    json={"ticker": "META", "min_vcp_score": 30, "outcome_days": 60}
)

if resp.status_code != 200:
    print(f"[!] Failed to start job: {resp.status_code}")
    print(resp.text)
    exit(1)

data = resp.json()
job_id = data.get("job_id")
log_file = data.get("log_file")

print(f"[+] Job started: {job_id}")
print(f"[*] Log file: {log_file}")

# Poll until done
for attempt in range(60):  # up to 60 seconds
    resp = requests.get(f"{BASE}/api/backtest/status/{job_id}")
    if resp.status_code != 200:
        print(f"[!] Poll failed: {resp.status_code}")
        break
    
    status_data = resp.json()
    pct = status_data.get("pct", 0)
    msg = status_data.get("msg", "")
    job_status = status_data.get("status", "")
    
    print(f"[{pct}%] {msg}")
    
    if job_status == "DONE":
        print("[+] Backtest complete")
        break
    
    time.sleep(1)

# Read LOG file
log_path = LOG_DIR / log_file
time.sleep(0.5)  # Allow file to flush

if log_path.exists():
    content = log_path.read_text()
    lines = content.split("\n")
    
    print(f"\n[*] LOG FILE CONTENT ({len(lines)} lines):")
    print("=" * 80)
    print(content)
    print("=" * 80)
    
    # Quick checks
    has_signal_table = "信號日" in content
    has_summary = "SUMMARY" in content
    has_unicode_boxes = "╔" in content or "╚" in content
    
    print(f"\n[+] Has signal table header: {has_signal_table}")
    print(f"[+] Has summary section: {has_summary}")
    print(f"[+] Has Unicode boxes: {has_unicode_boxes}")
    print(f"[+] Total lines in LOG: {len(lines)}")
else:
    print(f"[!] LOG file not found: {log_path}")
