#!/usr/bin/env python3
"""Final debug - trace exact execution path."""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C

print("="*80)
print("最终调试 - 追踪执行路径")
print("="*80)
print()

# Manually simulate the exact steps of _convert_amount
print("1️⃣  模拟 dashboard() 路由执行")
print("-"*80)

# Step 1: _get_account_size()
from app import _get_account_size

account_size, nav_sync_time, nav_sync_status = _get_account_size()
print(f"_get_account_size():")
print(f"  account_size = {account_size}")
print(f"  nav_sync_time = {nav_sync_time}")
print(f"  nav_sync_status = {nav_sync_status}")
print()

# Step 2: _load_currency_setting()
from app import _load_currency_setting

currency, rate = _load_currency_setting()
print(f"_load_currency_setting():")
print(f"  currency = {currency}")
print(f"  rate = {rate}")
print()

# Step 3: _convert_amount()
from app import _convert_amount

converted, symbol, display = _convert_amount(account_size, currency)
print(f"_convert_amount(account_size={account_size}, currency={currency}):")
print(f"  converted = {converted}")
print(f"  symbol = {symbol}")
print(f"  display = {display}")
print()

print("="*80)
print("2️⃣  验证配置")
print("-"*80)
print(f"C.ACCOUNT_BASE_CURRENCY = {C.ACCOUNT_BASE_CURRENCY}")
print()

print("="*80)
print("3️⃣  验证缓存")
print("-"*80)
cache_file = ROOT / "data" / "ibkr_nav_cache.json"
if cache_file.exists():
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    print(json.dumps(cache, indent=2, ensure_ascii=False))
else:
    print("❌ Cache file not found")
print()

print("="*80)
print("4️⃣  最终检查")
print("-"*80)
if display == "$12,984.96":
    print("✅ 转换正确！HK$101,931.93 ÷ 7.85 = $12,984.96")
elif display == "$101,931.31":
    print("❌ 转换错误！仍然显示为美元而不是转换后的值")
    print("   这意味着 account_base_currency 可能不是 HKD")
else:
    print(f"❓ 未知显示: {display}")
