#!/usr/bin/env python3
"""Test currency conversion with updated logic."""

import sys
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')

import trader_config as C
from app import _get_account_base_currency, _load_currency_setting, _convert_amount, _get_account_size

print("="*80)
print("测试更新后的货币转换逻辑")
print("="*80)
print()

# 1. Check config
print("1️⃣  配置检查")
print("-"*80)
print(f"trader_config.ACCOUNT_BASE_CURRENCY: {C.ACCOUNT_BASE_CURRENCY}")
print()

# 2. Get account base currency
print("2️⃣  获取账户基础货币")
print("-"*80)
account_base_currency = _get_account_base_currency()
print(f"最终账户基础货币: {account_base_currency}")
print()

# 3. Get account size
print("3️⃣  获取账户大小")
print("-"*80)
account_size, nav_sync_time, nav_sync_status = _get_account_size()
print(f"账户大小: {account_size}")
print(f"同步时间: {nav_sync_time}")
print(f"同步状态: {nav_sync_status}")
print()

# 4. Get currency setting
print("4️⃣  获取显示货币设置")
print("-"*80)
display_currency, rate = _load_currency_setting()
print(f"显示货币: {display_currency}")
print(f"汇率: {rate}")
print()

# 5. Test convert_amount
print("5️⃣  测试转换")
print("-"*80)
_, _, display = _convert_amount(account_size, display_currency)
print(f"最终显示: {display}")
print()

print("="*80)
print("✨ 结果验证")
print("="*80)
if account_base_currency == "HKD" and display_currency == "USD":
    print(f"账户: HKD | 显示: USD")
    expected_usd = account_size / rate
    print(f"  预期: ${expected_usd:,.2f}")
    print(f"  实际: {display}")
    print()
    actual_amount = float(display.replace("$", "").replace("HK$", "").replace(",", ""))
    if abs(actual_amount - expected_usd) < 0.01:
        print("  ✅ 正确!")
    else:
        print("  ❌ 错误!")
elif account_base_currency == "HKD" and display_currency == "HKD":
    print(f"账户: HKD | 显示: HKD")
    print(f"  预期: HK${account_size:,.2f}")
    print(f"  实际: {display}")
    print()
    if display.startswith("HK$"):
        print("  ✅ 正确!")
    else:
        print("  ❌ 错误!")
else:
    print(f"账户: {account_base_currency} | 显示: {display_currency}")
    print(f"  显示: {display}")
