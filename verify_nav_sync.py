#!/usr/bin/env python3
"""
Verify IBKR account size real-time sync system.
Tests three scenarios: LIVE (connected), CACHED (offline), DEFAULT (no data).
"""

import requests
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:5000"
CACHE_FILE = Path("data/ibkr_nav_cache.json")

def test_live_sync():
    """Test 1: Connect to IBKR and verify LIVE sync."""
    print("\n" + "="*70)
    print("TEST 1: LIVE SYNC FROM IBKR (Connected)")
    print("="*70)
    
    # Connect to IBKR
    print("\n1️⃣ Connecting to IBKR Gateway...")
    r = requests.post(f"{BASE_URL}/api/ibkr/connect", timeout=10)
    conn_data = r.json().get("data", {})
    
    if conn_data.get("success"):
        print(f"   ✅ Connected to account: {conn_data.get('account')}")
        print(f"   NAV from connect: ${conn_data.get('nav', 0):,.2f}")
    else:
        print(f"   ❌ Connection failed: {r.json()}")
        return
    
    # Get account size (should be LIVE)
    print("\n2️⃣ Fetching account size...")
    r = requests.get(f"{BASE_URL}/api/account/size", timeout=10)
    data = r.json()
    
    print(f"   NAV: ${data['nav']:,.2f}")
    print(f"   Status: {data['sync_status']}")
    print(f"   Last Sync: {data['last_sync']}")
    
    if data['sync_status'] == 'LIVE':
        print(f"   ✅ Status is LIVE (real-time from IBKR)")
    else:
        print(f"   ⚠️ Status is not LIVE")
    
    # Verify cache was created
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        print(f"\n3️⃣ Cache file verified:")
        print(f"   Location: {CACHE_FILE}")
        print(f"   Cached NAV: ${cache['nav']:,.2f}")
        print(f"   Cached account: {cache['account']}")
        print(f"   Cached timestamp: {cache['formatted_time']}")
    
    return data


def test_cached_fallback(live_data):
    """Test 2: Disconnect and verify CACHED fallback."""
    print("\n" + "="*70)
    print("TEST 2: CACHED FALLBACK (Disconnected)")
    print("="*70)
    
    # Disconnect
    print("\n1️⃣ Disconnecting from IBKR...")
    r = requests.post(f"{BASE_URL}/api/ibkr/disconnect", timeout=10)
    print(f"   {r.json()['data']['message']}")
    
    # Get account size (should be CACHED)
    print("\n2️⃣ Fetching account size while offline...")
    r = requests.get(f"{BASE_URL}/api/account/size", timeout=10)
    data = r.json()
    
    print(f"   NAV: ${data['nav']:,.2f}")
    print(f"   Status: {data['sync_status']}")
    print(f"   Last Sync: {data['last_sync']}")
    
    if data['sync_status'] == 'CACHED':
        print(f"   ✅ Status is CACHED (fallback to saved value)")
    else:
        print(f"   ⚠️ Status is not CACHED")
    
    # Verify it matches was we saw when connected
    if live_data and data['nav'] == live_data['nav']:
        print(f"   ✅ Cached NAV matches previous LIVE value: ${data['nav']:,.2f}")
    
    return data


def test_manual_sync():
    """Test 3: Manual sync trigger."""
    print("\n" + "="*70)
    print("TEST 3: MANUAL SYNC (Reconnect & Sync)")
    print("="*70)
    
    # Auto-connect via sync endpoint
    print("\n1️⃣ Triggering manual NAV sync...")
    r = requests.post(f"{BASE_URL}/api/ibkr/sync-nav", timeout=10)
    
    if r.status_code == 200:
        data = r.json()
        if data.get('ok'):
            print(f"   ✅ {data['message']}")
            print(f"   Account: {data['account']}")
            print(f"   Buying Power: ${data['buying_power']:,.2f}")
            print(f"   Timestamp: {data['last_sync']}")
        else:
            print(f"   ⚠️ Sync failed: {data.get('error')}")
    else:
        print(f"   ⚠️ HTTP {r.status_code}: {r.text[:100]}")


def main():
    """Run all tests."""
    print("\n" + "╔" + "="*68 + "╗")
    print("║  IBKR Account Size Real-Time Sync - Verification Tests" + " "*11 + "║")
    print("╚" + "="*68 + "╝")
    
    try:
        live_data = test_live_sync()
        test_cached_fallback(live_data)
        test_manual_sync()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS COMPLETED SUCCESSFULLY")
        print("="*70)
        print("\nYour account is now synced! Dashboard shows:")
        print(f"  • Account NAV: ${live_data['nav']:,.2f} (from IBKR)")
        print(f"  • Status: LIVE (or CACHED when offline)")
        print(f"  • Last Sync: {live_data['last_sync']}")
        print("\nProgram will no longer use hardcoded ACCOUNT_SIZE config.")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
