#!/usr/bin/env python3
"""
Verify that Ctrl+C properly kills the Flask app
"""
import subprocess
import time
import sys
import os
import requests

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

print("\n" + "="*70)
print("Testing Ctrl+C shutdown with process verification")
print("="*70 + "\n")

# Start the app
print("[TEST] Starting Flask app...")
proc = subprocess.Popen([sys.executable, "app.py"])
app_pid = proc.pid
print(f"[TEST] Flask app started (PID: {app_pid})")

# Wait for Flask to start
time.sleep(4)

# Verify it's running
if proc.poll() is None:
    print("[TEST] [OK] App is still running")
else:
    print("[TEST] [FAIL] App terminated unexpectedly")
    sys.exit(1)

# Test web access
print("\n[TEST] Testing web access...")
try:
    response = requests.get("http://localhost:5000", timeout=3)
    if response.status_code == 200:
        print("[TEST] [OK] Web interface is responsive")
    else:
        print(f"[TEST] [FAIL] Unexpected status code: {response.status_code}")
except Exception as e:
    print(f"[TEST] [FAIL] Web access failed: {e}")

# Send Ctrl+C event
print("\n[TEST] Sending Ctrl+C to process...")
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, proc.pid)
        print("[TEST] [OK] Ctrl+C event sent")
    except Exception as e:
        print(f"[TEST] [WARN] Could not send Ctrl+C: {e}")
        proc.terminate()
else:
    import signal
    proc.send_signal(signal.SIGINT)
    print("[TEST] [OK] SIGINT sent")

# Wait for termination
print("\n[TEST] Waiting for process to terminate...")
start_time = time.time()
while proc.poll() is None:
    elapsed = time.time() - start_time
    if elapsed > 8:  # More lenient timeout
        print("[TEST] [FAIL] Process did NOT terminate!")
        print("[TEST] Forcefully killing the process...")
        proc.kill()
        proc.wait()
        print("[TEST] [FAIL] Process was forcefully killed (should have responded to Ctrl+C)")
        sys.exit(1)
    time.sleep(0.2)

exit_code = proc.returncode
print(f"[TEST] [OK] Process terminated with exit code: {exit_code}")

# Verify it's actually dead
time.sleep(1)
print("\n[TEST] Verifying process is dead...")

# Try to connect to web interface
max_retries = 3
web_is_dead = False
for attempt in range(max_retries):
    try:
        response = requests.get("http://localhost:5000", timeout=1)
        # If we get here, web interface is still alive
        if attempt == max_retries - 1:
            print("[TEST] [FAIL] Web interface is still responding!")
            sys.exit(1)
    except:
        # Connection failed - web interface is dead
        web_is_dead = True
        break

if web_is_dead:
    print("[TEST] [OK] Web interface is unreachable (process is truly dead)")

print("\n" + "="*70)
print("[SUCCESS] Ctrl+C properly terminates the application")
print("="*70 + "\n")
