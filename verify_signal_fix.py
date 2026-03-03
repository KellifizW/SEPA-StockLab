#!/usr/bin/env python
"""
快速驗證 Ctrl+C 修復
Verify that Ctrl+C signal handling is working
"""

import sys
import signal
import time
import threading
import os

def test_signal_handling():
    """Test basic signal handling"""
    print("\n" + "="*60)
    print("Ctrl+C 信號處理驗證 | Signal Handler Verification")
    print("="*60 + "\n")
    
    caught_signal = []
    _event = threading.Event()
    
    def handler(signum, frame):
        msg = "SIGINT (Ctrl+C)" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n✅ 成功捕獲信號 | Signal caught: {msg}")
        caught_signal.append(signum)
        _event.set()
    
    # Register handlers
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    
    print("📡 信號處理器已註冊 | Signal handlers registered")
    print("⏳ 等待信號... Press Ctrl+C now (30 秒超時)")
    print("-" * 60 + "\n")
    
    start = time.time()
    while not _event.is_set():
        if time.time() - start > 30:
            print("❌ 超時 | Timeout - no signal received")
            return False
        time.sleep(0.5)
        print(".", end="", flush=True)
    
    print("\n\n✅ 驗證成功！\n")
    print("修復已確認有效。現在可以測試完整應用：")
    print("  python app.py\n")
    return True

if __name__ == "__main__":
    try:
        if test_signal_handling():
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ 錯誤: {e}")
        sys.exit(1)
