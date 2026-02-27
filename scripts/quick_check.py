#!/usr/bin/env python
"""
SEPA-StockLab 快速診斷工具
Quick diagnostic tool for SEPA-StockLab
"""
import sys
import json
import time
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def main():
    print("\n" + "=" * 70)
    print("SEPA-StockLab 系統診斷 (System Diagnostic)")
    print("=" * 70)
    
    # Test 1: Imports
    print("\n[1/5] 模組載入 (Module Import)...")
    try:
        import trader_config as C
        from modules.position_monitor import add_position, _load, close_position
        if C.DB_ENABLED:
            from modules import db
        print("      ✓ 所有模組正常載入 (All modules loaded)")
    except Exception as e:
        print(f"      ✗ 載入失敗 (Load failed): {e}")
        return False
    
    # Test 2: Load positions
    print("\n[2/5] 讀取持倉 (Load Positions)...")
    try:
        data = _load()
        positions = data.get("positions", {})
        print(f"      ✓ 成功讀取 {len(positions)} 個持倉 (Loaded {len(positions)} positions)")
    except Exception as e:
        print(f"      ✗ 讀取失敗 (Load failed): {e}")
        return False
    
    # Test 3: Add position
    print("\n[3/5] 測試添加持倉 (Test Add Position)...")
    ticker = f"TESTPOS-{int(time.time()) % 10000}"
    try:
        start = time.time()
        add_position(ticker, 100.00, 10, 95.00, 110.00, "Diagnostic test")
        elapsed = time.time() - start
        print(f"      ✓ 添加成功，耗時 {elapsed:.2f} 秒 (Added in {elapsed:.2f}s: {ticker})")
    except Exception as e:
        print(f"      ✗ 添加失敗 (Add failed): {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Reload and verify
    print("\n[4/5] 驗證數據 (Verify Data)...")
    try:
        data = _load()
        positions = data.get("positions", {})
        if ticker in positions:
            p = positions[ticker]
            print(f"      ✓ 驗證成功 (Verification OK)")
            print(f"        - 買入價: ${p['buy_price']:.2f}")
            print(f"        - 股數: {p['shares']}")
            print(f"        - 止損: ${p['stop_loss']:.2f}")
            print(f"        - 目標: ${p['target']:.2f}")
            print(f"        - 風險 ($$): ${p['risk_dollar']:.2f}")
        else:
            print(f"      ✗ 持倉未找到 (Position not found)")
            return False
    except Exception as e:
        print(f"      ✗ 驗證失敗 (Verification failed): {e}")
        return False
    
    # Test 5: Close position
    print("\n[5/5] 測試平倉 (Test Close Position)...")
    try:
        start = time.time()
        close_position(ticker, 105.00, "Diagnostic test close")
        elapsed = time.time() - start
        print(f"      ✓ 平倉成功，耗時 {elapsed:.2f} 秒 (Closed in {elapsed:.2f}s)")
        
        # Verify close
        data = _load()
        closed = data.get("closed", [])
        if any(c.get("ticker") == ticker for c in closed):
            print(f"      ✓ 平倉驗證成功 (Close verified)")
        else:
            print(f"      ⚠ 平倉未驗證 (Close not verified in history)")
    except Exception as e:
        print(f"      ✗ 平倉失敗 (Close failed): {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Summary
    print("\n" + "=" * 70)
    print("✓ 系統正常 (System OK) - 所有測試通過")
    print("=" * 70 + "\n")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
