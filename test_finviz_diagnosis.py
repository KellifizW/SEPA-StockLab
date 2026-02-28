#!/usr/bin/env python3
"""
test_finviz_diagnosis.py
━━━━━━━━━━━━━━━━━━━━━━━━
Diagnose finvizfinance screening performance and timeout issues.
Tests individual views and measures response times.
"""

import sys
import time
import logging
from pathlib import Path

# Setup logging to see all debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)-8s] %(name)s — %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("=" * 80)
print("FINVIZFINANCE DIAGNOSIS TEST")
print("=" * 80)
print()

# Quick check: Can we import finvizfinance?
try:
    from finvizfinance.screener.overview import Overview
    print("[OK] finvizfinance imported successfully")
except ImportError as e:
    print(f"[FAIL] FAILED to import finvizfinance: {e}")
    sys.exit(1)

print()
print("Testing finvizfinance screener_view() with timeout diagnostics...")
print("-" * 80)
print()

# Minimal test: Create screener and try to get results
views_to_test = ["Overview", "Performance"]
timeout_sec = 45

for view_name in views_to_test:
    print(f"\n>>> Testing {view_name} view (timeout={timeout_sec}s)...")
    print("-" * 40)
    
    start = time.time()
    try:
        # Import appropriate screener class
        if view_name == "Overview":
            from finvizfinance.screener.overview import Overview as ViewClass
        elif view_name == "Performance":
            from finvizfinance.screener.performance import Performance as ViewClass
        else:
            continue
        
        screener = ViewClass()
        
        # Set minimal filters
        filters = {'Price': 'Over $5'}
        print(f"[{time.time()-start:.1f}s] Setting filters: {filters}")
        screener.set_filter(filters_dict=filters)
        
        # Call screener_view with timing
        print(f"[{time.time()-start:.1f}s] Calling screener_view()...")
        
        import threading
        import socket
        
        result_container = {"df": None, "error": None, "completed": False}
        
        def fetch():
            try:
                # Set socket timeout to force HTTP requests to fail after timeout
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(50.0)
                try:
                    df = screener.screener_view()
                    result_container["df"] = df
                    result_container["completed"] = True
                finally:
                    socket.setdefaulttimeout(old_timeout)
            except Exception as e:
                result_container["error"] = e
                result_container["completed"] = True
        
        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
        thread.join(timeout=timeout_sec)
        
        elapsed = time.time() - start
        
        if thread.is_alive():
            print(f"[{elapsed:.1f}s] [TIMEOUT] Thread still alive after {timeout_sec}s")
            thread.join(timeout=5)  # Try to be patient
            if thread.is_alive():
                print(f"[{elapsed+5:.1f}s] [HARD_TIMEOUT] Thread will not join.")
        elif result_container["error"]:
            print(f"[{elapsed:.1f}s] [ERROR] {type(result_container['error']).__name__}")
            print(f"    Message: {str(result_container['error'])[:200]}")
        elif result_container["df"] is not None:
            df = result_container["df"]
            print(f"[{elapsed:.1f}s] [SUCCESS] Got DataFrame: shape={df.shape}")
            if df.shape[0] > 0:
                print(f"    First 3 tickers: {list(df.iloc[:3, 0].values) if df.shape[1] > 0 else 'N/A'}")
            else:
                print(f"    WARNING: DataFrame is empty (0 rows)")
        else:
            print(f"[{elapsed:.1f}s] [??] No result, no error, not completed")
    
    except Exception as e:
        elapsed = time.time() - start
        print(f"[{elapsed:.1f}s] [ERROR] Exception during test: {type(e).__name__}: {e}")

print()
print("=" * 80)
print("DIAGNOSIS COMPLETE")
print("=" * 80)
print()
print("If you see:")
print("  [SUCCESS] → finvizfinance works, but checks are slow (network latency)")
print("  [TIMEOUT] → finvizfinance is hanging (likely finviz.com blocking or very slow)")
print("  [ERROR] → Network error or finvizfinance bug (see error message above)")
print()
