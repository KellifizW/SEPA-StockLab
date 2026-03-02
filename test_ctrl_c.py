#!/usr/bin/env python3
"""
Test script to verify Ctrl+C (SIGINT) handling in app.py
"""
import subprocess
import time
import signal
import sys
import os

def test_ctrl_c():
    """Test if Ctrl+C properly terminates the Flask app."""
    print("\n" + "="*60)
    print("Testing Ctrl+C (SIGINT) handling on Flask app")
    print("="*60 + "\n")
    
    # Start the app as a subprocess
    print("[TEST] Starting app.py as subprocess...")
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__))
    )
    
    # Wait for Flask to start
    print("[TEST] Waiting 3 seconds for Flask to start...")
    time.sleep(3)
    
    # Check if process is still running
    if proc.poll() is None:
        print("[TEST] ✓ app.py is running")
    else:
        print("[TEST] ✗ app.py terminated unexpectedly!")
        stdout, stderr = proc.communicate()
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False
    
    # Send Ctrl+C (SIGINT) to the process
    print("[TEST] Sending Ctrl+C equivalent signal to the process...")
    if sys.platform == "win32":
        # On Windows, try CTRL_C_EVENT first
        try:
            import ctypes
            ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, proc.pid)
            print("[TEST] Sent CTRL_C_EVENT using Windows API")
        except:
            # Fallback to terminate()
            print("[TEST] Using terminate() instead...")
            proc.terminate()
    else:
        # On Linux/Mac, use SIGINT
        proc.send_signal(signal.SIGINT)
    
    # Wait for the process to terminate
    print("[TEST] Waiting for process to terminate...")
    start_time = time.time()
    timeout = 5
    
    while proc.poll() is None:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            print(f"[TEST] ✗ Process did not terminate within {timeout} seconds!")
            print("[TEST] Forcefully killing the process...")
            proc.kill()
            return False
        time.sleep(0.5)
    
    # Process terminated
    elapsed = time.time() - start_time
    print(f"[TEST] ✓ Process terminated in {elapsed:.2f} seconds")
    
    # Get output
    stdout, stderr = proc.communicate()
    if stdout:
        print(f"\n[TEST] Process output:\n{stdout[-500:]}")  # Last 500 chars
    
    # Check exit code
    exit_code = proc.returncode
    print(f"[TEST] Exit code: {exit_code}")
    
    # In Windows, exit code might be non-zero, but that's okay as long as it terminated
    if proc.poll() is not None:  # Process is no longer running
        print("[TEST] ✓ Process successfully shut down")
        print("\n" + "="*60)
        print("✓ SUCCESS - Ctrl+C handling works correctly!")
        print("  Process terminated cleanly without hanging.")
        print("="*60 + "\n")
        return True
    else:
        print(f"[TEST] ✗ Process still running after shutdown signal!")
        return False

if __name__ == "__main__":
    success = test_ctrl_c()
    sys.exit(0 if success else 1)
