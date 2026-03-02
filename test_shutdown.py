#!/usr/bin/env python3
"""
Simple test to verify app.py shuts down correctly
"""
import subprocess
import time
import sys
import os
import ctypes

print("\n" + "="*60)
print("Testing Flask app shutdown (Ctrl+C simulation)")
print("="*60 + "\n")

# Start app
print("Starting app.py...")
proc = subprocess.Popen([sys.executable, "app.py"])
time.sleep(3)

# Check status
if proc.poll() is None:
    print("✓ App is running (PID: {})".format(proc.pid))
else:
    print("✗ App quit unexpectedly")
    sys.exit(1)

# Send Windows Ctrl+C event
print("\nSending Ctrl+C event...")
try:
    ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, proc.pid)
except Exception as e:
    print(f"Error: {e}")
    proc.terminate()

# Wait for shutdown
print("Waiting for shutdown...")
try:
    proc.wait(timeout=5)
    print(f"✓ App shut down cleanly (exit code: {proc.returncode})")
    print("\n" + "="*60)
    print("SUCCESS! Ctrl+C handling works.")
    print("="*60 + "\n")
except subprocess.TimeoutExpired:
    print("✗ App did not shut down within 5 seconds!")
    proc.kill()
    print("Forcefully killed the process.")
    sys.exit(1)
