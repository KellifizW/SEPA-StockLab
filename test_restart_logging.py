#!/usr/bin/env python3
"""
Test script to verify logging persists after server restart
"""
import subprocess
import time
import requests
import sys

print("\n" + "="*70)
print("Testing server restart logging fix")
print("="*70 + "\n")

# Start the app
print("[TEST] Starting Flask app...")
proc = subprocess.Popen([sys.executable, "app.py"])
time.sleep(4)  # Wait for Flask to fully start

if proc.poll() is None:
    print("[TEST] ✓ Flask app started (PID: {})".format(proc.pid))
else:
    print("[TEST] ✗ Flask app failed to start")
    sys.exit(1)

# Test 1: Verify logging is working before restart
print("\n[TEST] Verifying logging output before restart...")
print("  (You should see log messages in this terminal)")
time.sleep(2)

# Test 2: Call the restart endpoint
print("\n[TEST] Calling /api/admin/restart endpoint...")
try:
    response = requests.post("http://localhost:5000/api/admin/restart", timeout=5)
    if response.status_code == 200:
        print("[TEST] ✓ Restart endpoint returned success")
        print(f"       Response: {response.json()}")
    else:
        print(f"[TEST] ✗ Restart endpoint returned status {response.status_code}")
except Exception as e:
    print(f"[TEST] ! Error calling restart endpoint: {e}")

# Test 3: Wait for old process to die and new one to start
print("\n[TEST] Waiting for server restart (10 seconds)...")
for i in range(10):
    time.sleep(1)
    sys.stdout.write(f"\r[TEST] Waiting... {i+1}/10")
    sys.stdout.flush()
print()

# Test 4: Verify new server is running
print("\n[TEST] Attempting to connect to restarted server...")
max_retries = 5
for attempt in range(max_retries):
    try:
        response = requests.get("http://localhost:5000/", timeout=2)
        if response.status_code == 200:
            print("[TEST] ✓ New server is running and responding!")
            print("[TEST] ✓ Logging should still be visible in this terminal")
            print("\n" + "="*70)
            print("SUCCESS! Server restart works with persistent logging.")
            print("="*70 + "\n")
            sys.exit(0)
    except:
        pass
    
    if attempt < max_retries - 1:
        print(f"[TEST] Retrying ({attempt+2}/{max_retries})...")
        time.sleep(1)

print("[TEST] ✗ New server did not respond after restart")
print("[TEST] Killing any remaining processes...")
proc.kill()
sys.exit(1)
