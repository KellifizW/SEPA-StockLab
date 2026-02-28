#!/usr/bin/env python3
"""
Quick test: Verify Stage 1 progress updates work correctly.
This bypasses web server and directly calls run_qm_stage1() to observe progress.
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

def test_stage1_progress():
    """Run Stage 1 and monitor progress updates."""
    from modules.qm_screener import run_qm_stage1, get_qm_scan_progress
    import threading
    import time
    
    print("\n" + "="*80)
    print("TEST: Stage 1 with Live Progress Monitoring")
    print("="*80)
    
    # Launch Stage 1 in a thread so we can monitor progress
    results = []
    exception = []
    
    def run_scan():
        try:
            tickers = run_qm_stage1(verbose=True)
            results.append(tickers)
        except Exception as e:
            exception.append(e)
    
    thread = threading.Thread(target=run_scan, daemon=False)
    thread.start()
    
    # Monitor progress in main thread
    print("\n▶ Polling progress every 0.5 seconds...\n")
    last_pct = -1
    start_time = time.time()
    
    while thread.is_alive():
        progress = get_qm_scan_progress()
        pct = progress.get("pct", 0)
        stage = progress.get("stage", "")
        msg = progress.get("msg", "")
        ticker = progress.get("ticker", "")
        
        # Only print on change
        if pct != last_pct or msg:
            elapsed = time.time() - start_time
            text = f"  [{stage}] {pct}% — {msg}"
            if ticker:
                text += f" ({ticker})"
            print(f"  {elapsed:.1f}s: {text}")
            last_pct = pct
        
        time.sleep(0.5)
    
    thread.join()
    
    print("\n✓ Stage 1 completed.")
    
    if exception:
        print(f"✗ Error: {exception[0]}")
        return False
    
    if results:
        tickers = results[0]
        print(f"\n✓ Found {len(tickers)} candidates")
        if tickers:
            print(f"  First 5: {tickers[:5]}")
        return len(tickers) > 0
    
    return False

if __name__ == "__main__":
    success = test_stage1_progress()
    sys.exit(0 if success else 1)
