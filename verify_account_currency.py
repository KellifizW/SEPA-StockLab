#!/usr/bin/env python3
"""Enhanced currency diagnosis - check IBKR account currency and verify balance."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def main():
    print("=" * 70)
    print("IBKR 账户货币诊断")
    print("=" * 70)
    print()
    
    # 1. Check cached NAV
    print("📊 检查缓存的账户数据:")
    print("-" * 70)
    nav_cache = ROOT / "data" / "ibkr_nav_cache.json"
    if nav_cache.exists():
        with open(nav_cache, 'r', encoding='utf-8') as f:
            nav_data = json.load(f)
        print(f"✓ 找到缓存: {nav_cache}")
        print(f"  NAV (账户净值): ${nav_data.get('nav', 'N/A')}")
        print(f"  账户ID: {nav_data.get('account', 'N/A')}")
        print(f"  同步时间: {nav_data.get('formatted_time', 'N/A')}")
        print()
    else:
        print(f"✗ 未找到缓存文件")
        print()
    
    # 2. Check currency settings
    print("💱 检查货币设置:")
    print("-" * 70)
    currency_file = ROOT / "data" / "currency_settings.json"
    if currency_file.exists():
        with open(currency_file, 'r', encoding='utf-8') as f:
            currency_data = json.load(f)
        current_currency = currency_data.get('currency', 'USD')
        print(f"✓ 当前显示货币: {current_currency}")
        print()
    else:
        current_currency = "USD"
        print(f"✓ 使用默认货币: {current_currency}")
        print()
    
    # 3. Import trader_config
    import trader_config as C
    print("⚙️  配置常数:")
    print("-" * 70)
    print(f"  DEFAULT_CURRENCY: {C.DEFAULT_CURRENCY}")
    print(f"  ACCOUNT_SIZE: ${C.ACCOUNT_SIZE:,.2f}")
    print(f"  USD_TO_HKD_RATE: {C.USD_TO_HKD_RATE}")
    print()
    
    # 4. Analysis
    print("🔍 分析:")
    print("-" * 70)
    if nav_cache.exists():
        nav_value = nav_data.get('nav', C.ACCOUNT_SIZE)
    else:
        nav_value = C.ACCOUNT_SIZE
    
    print(f"账户显示的余额: ${nav_value:,.2f}")
    print()
    print(f"假设场景 A: 余额是美元 (USD)")
    print(f"  - 原值: ${nav_value:,.2f} USD")
    print(f"  - 转换为港元: HK${nav_value * 7.85:,.2f} HKD")
    print()
    print(f"假设场景 B: 余额是港元 (HKD)")
    print(f"  - 原值: HK${nav_value:,.2f} HKD")
    print(f"  - 转换为美元: ${nav_value / 7.85:,.2f} USD")
    print()
    
    # 5. Key questions for user
    print("❓ 需要验证:")
    print("-" * 70)
    print(f"❌ IBKR 账户的基础货币是什么?")
    print(f"   - 登录 IBKR 客户端")
    print(f"   - 查看 账户 → 账户设置 → 基础货币")
    print()
    print(f"❌ IBKR 客户端显示的净资产值是多少? 用什么货币符号?")
    print(f"   - e.g. \"USD 101,932.99\" 或 \"HK$ 101,932.99\"")
    print()
    print(f"❌ 用户界面上显示的金额是: $101,932.99")
    print(f"   - 这个金额应该在 IBKR 哪里看到?")
    print()
    
    print("=" * 70)
    print("\n💡 我可以帮您:")
    print("   1. 如果您告诉我 IBKR 账户基础货币是 USD 还是 HKD")
    print("   2. 如果您告诉我 IBKR 显示的确切净资产值和货币")
    print("   3. 我可以修复代码，确保显示正确的货币符号和转换")
    print()

if __name__ == "__main__":
    main()
