#!/usr/bin/env python3
"""Test currency conversion directly without Flask."""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C

print("="*80)
print("直接测试货币转换逻辑")
print("="*80)
print()

# 1. Check cache content
print("1️⃣  检查缓存")
print("-"*80)
cache_file = ROOT / "data" / "ibkr_nav_cache.json"
if cache_file.exists():
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    print(f"缓存文件: {cache_file}")
    print(json.dumps(cache, indent=2, ensure_ascii=False))
else:
    print("❌ 缓存文件不存在")
    cache = {}

print()

# 2. Check currency settings
print("2️⃣  检查货币设置")
print("-"*80)
currency_file = ROOT / "data" / "currency_settings.json"
if currency_file.exists():
    currency_data = json.loads(currency_file.read_text(encoding="utf-8"))
    current_currency = currency_data.get("currency", "USD")
    rate = currency_data.get("usd_hkd_rate", C.USD_TO_HKD_RATE)
    print(f"当前显示货币: {current_currency}")
    print(f"USD->HKD 汇率: {rate}")
else:
    print("❌ 货币设置文件不存在")
    current_currency = "USD"
    rate = C.USD_TO_HKD_RATE

print()

# 3. Test account size from cache
print("3️⃣  账户大小（从缓存）")
print("-"*80)
account_size = cache.get("nav", C.ACCOUNT_SIZE)
account_currency = cache.get("account_currency", "USD")
print(f"账户余额: {account_size}")
print(f"账户基础货币: {account_currency}")
print()

# 4. Simulate _convert_amount logic
print("4️⃣  转换逻辑模拟")
print("-"*80)
amount = account_size
target_currency = current_currency
print(f"输入: amount={amount}, target_currency={target_currency}")
print()

# Step 1: Convert from account base currency to USD (if needed)
if account_currency == "HKD":
    amount_usd = amount / rate
    print(f"Step 1: 账户基础货币是 HKD")
    print(f"        HK${amount:,.2f} ÷ {rate} = ${amount_usd:,.2f} USD")
else:
    amount_usd = amount
    print(f"Step 1: 账户基础货币是 {account_currency} (或未知，默认USD)")
    print(f"        amount_usd = {amount_usd:,.2f}")

print()

# Step 2: Convert from USD to target currency
if target_currency == "HKD":
    final_amount = amount_usd * rate
    display = f"HK${final_amount:,.2f}"
    print(f"Step 2: 目标货币是 HKD")
    print(f"        ${amount_usd:,.2f} × {rate} = {display}")
else:
    final_amount = amount_usd
    display = f"${final_amount:,.2f}"
    print(f"Step 2: 目标货币是 USD")
    print(f"        display = {display}")

print()

print("="*80)
print("✨ 最终结果")
print("="*80)
print(f"显示: {display}")
print()

# Check if this is correct
print("💡 问题诊断")
print("-"*80)
if account_currency == "HKD" and current_currency == "USD":
    print(f"假设：账户基础货币是港元，用户要求美元显示")
    print(f"  原值: HK${account_size:,.2f}")
    print(f"  正确显示: ${account_size / rate:,.2f}")
    print(f"  系统显示: {display}")
    if abs(float(display.replace("$", "")) - (account_size / rate)) < 0.01:
        print(f"  ✅ 正确!")
    else:
        print(f"  ❌ 错误!")
elif account_currency == "HKD" and current_currency == "HKD":
    print(f"假设：账户基础货币是港元，用户要求港元显示")
    print(f"  原值: HK${account_size:,.2f}")
    print(f"  正确显示: HK${account_size:,.2f}")
    print(f"  系统显示: {display}")
    if display.startswith("HK$"):
        amount_in_display = float(display.replace("HK$", "").replace(",", ""))
        if abs(amount_in_display - account_size) < 0.01:
            print(f"  ✅ 正确!")
        else:
            print(f"  ❌ 错误!")
else:
    print(f"账户基础货币: {account_currency}")
    print(f"目标显示货币: {current_currency}")
    print(f"系统显示: {display}")

print()
print("🔑 关键问题:")
print("-"*80)
print(f"1. IBKR 账户的实际基础货币是什么? (现在系统认为: {account_currency})")
print(f"2. 系统显示的 {display} 是对还是错?")
if account_size < 200000:
    print(f"3. 如果金额小于 200,000，很可能是港元还是美元?")
    if account_currency == "USD":
        suggested = "可能应该是 HKD"
    else:
        suggested = "可能应该是 USD"
    print(f"   💡 建议: {suggested}")
