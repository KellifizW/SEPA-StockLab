#!/usr/bin/env python3
"""
Diagnostic script to identify where "truth value is ambiguous" error occurs.
Monitors Flask app logs in real-time during combined scan.
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime
from threading import Thread
import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

class DiagnosticMonitor:
    def __init__(self):
        self.logs_dir = ROOT / "logs"
        self.latest_log = None
        self.last_position = 0
        self.errors_found = []
        self.api_errors = []
        
    def check_latest_log(self):
        """Find and monitor latest combined_scan log."""
        try:
            logs = list(self.logs_dir.glob("combined_scan_*.log"))
            if not logs:
                return None
            
            latest = sorted(logs, key=lambda x: x.stat().st_mtime, reverse=True)[0]
            
            if self.latest_log != latest:
                self.latest_log = latest
                self.last_position = 0
                print(f"\n[MONITOR] New log file: {latest.name}")
            
            return latest
        except Exception as e:
            print(f"[ERROR] Failed to check logs: {e}")
            return None
    
    def read_new_logs(self):
        """Read and display new log entries."""
        log_file = self.check_latest_log()
        if not log_file or not log_file.exists():
            return
        
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(self.last_position)
                lines = f.readlines()
                self.last_position = f.tell()
                
                for line in lines:
                    # Check for error patterns
                    if any(x in line.lower() for x in ['error', 'exception', 'traceback', 'ambiguous', 'truth value']):
                        print(f"[SCAN LOG ERROR] {line.rstrip()}")
                        self.errors_found.append({
                            'timestamp': datetime.now().isoformat(),
                            'source': 'scan_log',
                            'message': line.rstrip()
                        })
                    elif 'DEBUG' not in line:  # Skip debug-only messages
                        print(f"[SCAN LOG] {line.rstrip()}")
        except Exception as e:
            print(f"[ERROR] Failed to read logs: {e}")
    
    def test_endpoints(self):
        """Test Flask API endpoints."""
        base_url = "http://localhost:5000"
        endpoints = [
            "/api/combined/scan/run",
            "/api/combined/scan/status/test",
            "/api/scan/run",
            "/api/qm/scan/run",
        ]
        
        print("\n[CONNECTIVITY] Testing Flask endpoints...")
        for endpoint in endpoints:
            try:
                # Use safe method first
                response = requests.get(base_url + endpoint, timeout=5)
                status = "✓" if response.status_code < 500 else "✗"
                print(f"{status} {endpoint}: {response.status_code}")
            except requests.exceptions.ConnectionError:
                print(f"✗ {endpoint}: Connection refused (server not running?)")
            except Exception as e:
                print(f"✗ {endpoint}: {e}")
    
    def start_scan(self):
        """Start a combined scan via API."""
        base_url = "http://localhost:5000"
        print("\n[ACTION] Starting combined scan...")
        
        try:
            response = requests.post(
                base_url + "/api/combined/scan/run",
                json={"refresh_rs": False, "min_star": 3.0, "top_n": None},
                timeout=10
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    jid = data.get("job_id")
                    print(f"✓ Scan started with job_id: {jid}")
                    return jid
                except json.JSONDecodeError as e:
                    print(f"✗ Failed to parse response JSON: {e}")
                    print(f"  Response text: {response.text[:200]}")
                    self.api_errors.append({
                        'timestamp': datetime.now().isoformat(),
                        'endpoint': '/api/combined/scan/run',
                        'status': response.status_code,
                        'error': f"JSON decode error: {e}",
                        'response': response.text[:500]
                    })
                    return None
            else:
                print(f"✗ Scan start failed: HTTP {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                self.api_errors.append({
                    'timestamp': datetime.now().isoformat(),
                    'endpoint': '/api/combined/scan/run',
                    'status': response.status_code,
                    'error': response.text[:500]
                })
                return None
        except requests.exceptions.ConnectionError:
            print(f"✗ Cannot connect to Flask server at {base_url}")
            print("  Is the server running? Run: python app.py")
            return None
        except Exception as e:
            print(f"✗ Failed to start scan: {e}")
            self.api_errors.append({
                'timestamp': datetime.now().isoformat(),
                'endpoint': '/api/combined/scan/run',
                'error': str(e)
            })
            return None
    
    def poll_status(self, jid, interval=10, timeout=600):
        """Poll scan status until complete."""
        base_url = "http://localhost:5000"
        endpoint = f"/api/combined/scan/status/{jid}"
        start_time = time.time()
        last_status = None
        
        print(f"\n[POLLING] Monitoring scan {jid}...")
        print(f"[POLLING] Checking every {interval}s, timeout in {timeout}s\n")
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(base_url + endpoint, timeout=10)
                
                if response.status_code != 200:
                    print(f"[POLL ERROR] HTTP {response.status_code}")
                    print(f"  Response: {response.text[:200]}")
                    self.api_errors.append({
                        'timestamp': datetime.now().isoformat(),
                        'endpoint': endpoint,
                        'status': response.status_code,
                        'error': response.text[:500]
                    })
                else:
                    try:
                        data = response.json()
                        status = data.get("status")
                        
                        if status != last_status:
                            print(f"[STATUS] {status}")
                            last_status = status
                        
                        # Show progress if available
                        if status == "pending" and "progress" in data:
                            progress = data["progress"]
                            pct = progress.get("pct", "?")
                            msg = progress.get("msg", "")
                            print(f"  Progress: {pct}% - {msg}")
                        
                        # Check if done
                        if status == "done":
                            print("\n✓ Scan completed!")
                            result = data.get("result", {})
                            sepa_count = result.get("sepa", {}).get("count", 0)
                            qm_count = result.get("qm", {}).get("count", 0)
                            print(f"  Results: SEPA={sepa_count} QM={qm_count}")
                            return True
                        
                        elif status == "error":
                            print(f"\n✗ Scan error!")
                            error = data.get("error", "Unknown error")
                            print(f"  Error message: {error}")
                            self.errors_found.append({
                                'timestamp': datetime.now().isoformat(),
                                'source': 'api_response',
                                'status': 'error',
                                'message': error
                            })
                            return False
                    
                    except json.JSONDecodeError as e:
                        print(f"[POLL ERROR] Failed to parse status JSON: {e}")
                        print(f"  Response text: {response.text[:200]}")
                        self.api_errors.append({
                            'timestamp': datetime.now().isoformat(),
                            'endpoint': endpoint,
                            'error': f"JSON decode error: {e}",
                            'response': response.text[:500]
                        })
            
            except requests.exceptions.ConnectionError:
                print(f"[POLL ERROR] Connection lost to server")
                break
            except Exception as e:
                print(f"[POLL ERROR] {e}")
            
            print(f"[WAITING] {interval}s until next poll...", end='\r')
            time.sleep(interval)
        
        print(f"\n✗ Scan polling timeout after {timeout}s")
        return False
    
    def report_findings(self):
        """Print diagnostic report."""
        print("\n" + "="*70)
        print("DIAGNOSTIC REPORT")
        print("="*70)
        
        print(f"\n[SCAN LOG ERRORS FOUND] {len(self.errors_found)}")
        for error in self.errors_found:
            print(f"  Time: {error.get('timestamp')}")
            print(f"  Source: {error.get('source')}")
            print(f"  Message: {error.get('message')}")
        
        print(f"\n[API ERRORS FOUND] {len(self.api_errors)}")
        for error in self.api_errors:
            print(f"  Time: {error.get('timestamp')}")
            print(f"  Endpoint: {error.get('endpoint')}")
            if 'status' in error:
                print(f"  Status: {error.get('status')}")
            if 'error' in error:
                print(f"  Error: {error.get('error')}")
        
        if not self.errors_found and not self.api_errors:
            print("\n✓ No errors detected during scan!")
        
        # Save report to file
        report_file = ROOT / "DIAGNOSTIC_REPORT.json"
        report_data = {
            'timestamp': datetime.now().isoformat(),
            'scan_log_errors': self.errors_found,
            'api_errors': self.api_errors,
        }
        
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2)
        
        print(f"\n[REPORT SAVED] {report_file}")
        print("="*70)


def main():
    print("="*70)
    print("COMBINED SCAN DIAGNOSTIC MONITOR")
    print("="*70)
    print("\nThis script will:")
    print("1. Check Flask API connectivity")
    print("2. Start a combined scan")
    print("3. Monitor scan progress and logs")
    print("4. Capture any errors that occur")
    print("5. Generate diagnostic report")
    
    monitor = DiagnosticMonitor()
    
    # Test endpoints first
    monitor.test_endpoints()
    
    # Ask user for confirmation
    response = input("\n[QUESTION] Start scan now? (y/n): ").strip().lower()
    if response != 'y':
        print("[CANCELLED] Diagnostic cancelled")
        return
    
    # Start scan
    jid = monitor.start_scan()
    if not jid:
        print("[ERROR] Failed to start scan, cannot proceed")
        return
    
    # Start log monitoring in background
    def monitor_logs():
        while True:
            monitor.read_new_logs()
            time.sleep(2)
    
    log_thread = Thread(target=monitor_logs, daemon=True)
    log_thread.start()
    
    # Poll status
    success = monitor.poll_status(jid, interval=5, timeout=600)
    
    # Generate report
    time.sleep(2)  # Give logs time to flush
    monitor.report_findings()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
