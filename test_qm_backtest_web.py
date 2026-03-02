#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test QM backtest through Flask API with comprehensive error checking
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def test_backtest():
    print("=" * 80)
    print("QM BACKTEST WEB API TEST")
    print("=" * 80)
    
    # Test 1: Start NVDA backtest
    print("\n[1] Starting NVDA backtest via API...")
    payload = {
        "ticker": "NVDA",
        "min_star": 3.0,
        "max_hold_days": 120,
        "debug_mode": True
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/api/qm/backtest/run", json=payload, timeout=10)
        print(f"  Status: {resp.status_code}")
        result = resp.json()
        print(f"  Response: {json.dumps(result, indent=2)}")
        
        if "job_id" not in result:
            print("  [ERROR] No job_id in response!")
            return False
        
        job_id = result["job_id"]
        print(f"  [OK] Job ID: {job_id}")
        
    except Exception as e:
        print(f"  [ERROR]: {e}")
        return False
    
    # Test 2: Poll status until completion
    print("\n[2] Polling backtest status...")
    max_polls = 60
    poll_count = 0
    
    while poll_count < max_polls:
        try:
            resp = requests.get(f"{BASE_URL}/api/qm/backtest/status/{job_id}", timeout=10)
            status_data = resp.json()
            
            if "status" in status_data:
                print(f"  [{poll_count+1}] Status: {status_data['status']} - {status_data.get('message', '')}")
                
                if status_data["status"] == "completed":
                    print(f"  [OK] Backtest completed!")
                    
                    # Inspect result structure
                    if "result" in status_data and status_data["result"]:
                        result_obj = status_data["result"]
                        print(f"\n[3] Inspecting result structure:")
                        print(f"  - Type: {type(result_obj)}")
                        
                        if isinstance(result_obj, dict):
                            print(f"  - Top-level keys: {list(result_obj.keys())}")
                            
                            if "summary" in result_obj:
                                summary = result_obj["summary"]
                                print(f"\n  [Summary fields]")
                                print(f"    - total_signals: {summary.get('total_signals')} (type: {type(summary.get('total_signals'))})")
                                print(f"    - win_rate: {summary.get('win_rate')} (is None: {summary.get('win_rate') is None})")
                                print(f"    - avg_realized_gain: {summary.get('avg_realized_gain')} (is None: {summary.get('avg_realized_gain') is None})")
                                print(f"    - profit_factor: {summary.get('profit_factor')} (is None: {summary.get('profit_factor') is None})")
                                print(f"    - best_gain: {summary.get('best_gain')} (is None: {summary.get('best_gain') is None})")
                                print(f"    - worst_gain: {summary.get('worst_gain')} (is None: {summary.get('worst_gain') is None})")
                                
                            if "signals" in result_obj and result_obj["signals"]:
                                sig = result_obj["signals"][0]
                                print(f"\n  [First signal fields]")
                                print(f"    - star_rating: {sig.get('star_rating')} (is None: {sig.get('star_rating') is None})")
                                print(f"    - signal_close: {sig.get('signal_close')} (is None: {sig.get('signal_close') is None})")
                                print(f"    - breakout_level: {sig.get('breakout_level')} (is None: {sig.get('breakout_level') is None})")
                                print(f"    - max_gain: {sig.get('max_gain')} (is None: {sig.get('max_gain') is None})")
                                print(f"    - realized_gain: {sig.get('realized_gain')} (is None: {sig.get('realized_gain') is None})")
                    
                    print(f"\n[OK] TEST PASSED: No JavaScript errors expected now!")
                    return True
                    
                elif status_data["status"] == "error":
                    print(f"  [ERROR] Backend error: {status_data.get('message', '')}")
                    return False
            
            time.sleep(1)
            poll_count += 1
            
        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Request error: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"  [ERROR] JSON decode error: {e}")
            print(f"     Raw response: {resp.text[:200]}")
            return False
    
    print(f"  [ERROR] Timeout: Backtest did not complete after {max_polls} polls")
    return False

if __name__ == "__main__":
    success = test_backtest()
    sys.exit(0 if success else 1)
