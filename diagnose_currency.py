#!/usr/bin/env python3
"""Diagnose which currency the account balance is in."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def check_currency_settings():
    """Check current currency setting."""
    settings_file = ROOT / "data" / "currency_settings.json"
    if settings_file.exists():
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        print("✓ Currency Settings Found:")
        print(f"  Current Currency: {settings.get('currency', 'NOT SET')}")
        print(f"  USD→HKD Rate: {settings.get('usd_hkd_rate', 'NOT SET')}")
        print(f"  Last Updated: {settings.get('last_updated', 'NOT SET')}\n")
        return settings.get('currency', 'USD')
    else:
        print("✗ Currency Settings NOT found (using default: USD)\n")
        return 'USD'

def check_ibkr_balance():
    """Check account balance from IBKR."""
    try:
        # Try to import and call the IBKR module
        from modules.ibkr_connector import IBKR
        
        ibkr = IBKR()
        nav, status = ibkr.get_nav()
        print("✓ IBKR Account Balance:")
        print(f"  NAV: ${nav:,.2f}")
        print(f"  Status: {status}")
        print(f"  Currency Unit: UNCLEAR (need to check IBKR object)\n")
        
        # Check if IBKR stores currency info
        if hasattr(ibkr, 'account_currency'):
            print(f"  Account Currency: {ibkr.account_currency}\n")
        
        return nav, status
    except Exception as e:
        print(f"✗ Cannot connect to IBKR: {e}\n")
        return None, None

def check_cached_data():
    """Check what's in the JSON cache files."""
    print("✓ Checking Cached Data Files:\n")
    
    # Check last_scan.json for any currency hints
    cache_file = ROOT / "data" / "last_scan.json"
    if cache_file.exists():
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if 'account_size' in data:
            print(f"  last_scan.json → account_size: ${data['account_size']:,.2f}")
    
    # Check watchlist.json
    watchlist_file = ROOT / "data" / "watchlist.json"
    if watchlist_file.exists():
        with open(watchlist_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            if 'currency' in first_item:
                print(f"  watchlist.json → currency: {first_item['currency']}")
    
    print()

def check_trader_config():
    """Check config defaults."""
    import trader_config as C
    
    print("✓ trader_config.py Constants:")
    print(f"  DEFAULT_CURRENCY: {C.DEFAULT_CURRENCY}")
    print(f"  USD_TO_HKD_RATE: {C.USD_TO_HKD_RATE}")
    print()

def main():
    print("=" * 60)
    print("CURRENCY VERIFICATION DIAGNOSTIC")
    print("=" * 60)
    print()
    
    # 1. Check config
    check_trader_config()
    
    # 2. Check current currency setting
    current_currency = check_currency_settings()
    
    # 3. Check IBKR balance
    nav, status = check_ibkr_balance()
    
    # 4. Check cached data
    check_cached_data()
    
    # 5. Print summary and questions
    print("=" * 60)
    print("ANALYSIS:")
    print("=" * 60)
    if nav:
        print(f"\n账户余额: ${nav:,.2f}")
        print(f"当前显示货币: {current_currency}")
        print()
        print("🔍 关键问题:")
        print(f"  1. IBKR 返回的 ${nav:,.2f} 是哪种货币?")
        print(f"     - 美元 (USD)?")
        print(f"     - 港元 (HKD)?")
        print()
        print(f"  2. 如果是 ${nav:,.2f} = HKD (港元):")
        print(f"     - 那么美元应该是: HK${nav:,.2f} / 7.85 = ${nav/7.85:,.2f}")
        print()
        print(f"  3. 如果是 ${nav:,.2f} = USD (美元):")
        print(f"     - 那么港元应该是: ${nav:,.2f} * 7.85 = HK${nav*7.85:,.2f}")
        print()
        print("💡 如何确认:")
        print("  - 查看 IBKR 账户设置 → 基础货币是什么?")
        print("  - 查看 IBKR 最后的现金余额显示")
        print("  - 告诉我 IBKR 客户端显示的确切货币符号")

if __name__ == "__main__":
    main()
