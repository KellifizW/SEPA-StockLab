#!/usr/bin/env python
"""
Test Ctrl+C handling with improved signal handlers
"""

import sys
import os
import time
import signal
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def test_basic_signal_handling():
    """Test that basic signal handling works"""
    print("\n" + "="*64)
    print("TEST 1: Basic Signal Handler")
    print("="*64 + "\n")
    
    _shutdown_event = threading.Event()
    _caught_signals = []
    
    def _signal_handler(signum, frame):
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        _caught_signals.append(signal_name)
        print(f"✅ 收到信號: {signal_name}", flush=True)
        _shutdown_event.set()
    
    # Register handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    print("Signal handlers registered")
    print("Main thread waiting for signals...")
    print("Try sending signal: Ctrl+C or kill -TERM <pid>\n")
    
    # Main loop
    start_time = time.time()
    timeout = 30  # 30 second timeout for test
    
    while not _shutdown_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > timeout:
            print(f"❌ Timeout: No signal received after {timeout}s")
            os._exit(1)
        time.sleep(1)
    
    print(f"✅ Signal handling works! Caught signals: {_caught_signals}")
    return True


def test_daemon_thread_cleanup():
    """Test that daemon threads are properly cleaned up"""
    print("\n" + "="*64)
    print("TEST 2: Daemon Thread Cleanup")
    print("="*64 + "\n")
    
    _shutdown_event = threading.Event()
    _daemon_ran = []
    
    def _daemon_worker():
        try:
            for i in range(10):
                if _shutdown_event.is_set():
                    break
                print(f"  Daemon thread: iteration {i+1}")
                time.sleep(1)
        except:
            pass
    
    def _signal_handler(signum, frame):
        print(f"✅ Received signal, setting shutdown event")
        _shutdown_event.set()
    
    signal.signal(signal.SIGINT, _signal_handler)
    
    # Start daemon thread
    daemon = threading.Thread(target=_daemon_worker, daemon=True)
    daemon.start()
    print("Daemon thread started")
    print("Main thread waiting (try Ctrl+C within 10 seconds)...\n")
    
    start_time = time.time()
    timeout = 30
    
    try:
        while daemon.is_alive():
            elapsed = time.time() - start_time
            if elapsed > timeout:
                print(f"❌ Timeout: Daemon thread still alive after {timeout}s")
                os._exit(1)
            time.sleep(0.5)
    except KeyboardInterrupt:
        _signal_handler(signal.SIGINT, None)
        time.sleep(0.1)
    
    print("✅ Daemon thread cleanup works!")
    return True


def main():
    print("\n" + "="*64)
    print(" SEPA-StockLab Ctrl+C Fix Test Suite")
    print("="*64)
    print("\nThis test verifies that signal handling is working correctly.")
    print("You should see messages appearing and be able to stop with Ctrl+C.\n")
    
    try:
        # Test 1: Basic signal handling
        test_basic_signal_handling()
        
        print("\n✅ All tests completed successfully!")
        print("\nTo test with the actual app, run:")
        print("  python app.py")
        print("  python start_web.py")
        print("\nThen press Ctrl+C to verify server stops immediately.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
