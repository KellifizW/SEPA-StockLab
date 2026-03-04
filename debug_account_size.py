#!/usr/bin/env python3
"""Debug _get_account_size() function."""

import sys
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s'
)

import trader_config as C
from app import _get_account_size, _load_nav_cache, ibkr_client

print("="*80)
print("调试 _get_account_size() 函数")
print("="*80)
print()

# 1. Check IBKR status
print("1️⃣  IBKR 连接状态")
print("-"*80)
try:
    status = ibkr_client.get_status()
    print(f"IBKR Status:")
    for k, v in status.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"❌ Error getting IBKR status: {e}")

print()

# 2. Call _get_account_size()
print("2️⃣  调用 _get_account_size()")
print("-"*80)
try:
    account_size, nav_sync_time, nav_sync_status = _get_account_size()
    print(f"account_size:     {account_size}")
    print(f"nav_sync_time:   {nav_sync_time}")
    print(f"nav_sync_status: {nav_sync_status}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print()

# 3. Check cache content after call
print("3️⃣  调用后缓存内容")
print("-"*80)
try:
    cached = _load_nav_cache()
    if cached:
        print("Cache content:")
        print(json.dumps(cached, indent=2, ensure_ascii=False))
        
        if "account_currency" in cached:
            print(f"\n✅ account_currency 字段存在: {cached['account_currency']}")
        else:
            print(f"\n❌ account_currency 字段 NOT FOUND")
    else:
        print("❌ Cache is empty")
except Exception as e:
    print(f"❌ Error: {e}")

print()
print("="*80)
print("结论")
print("="*80)
if status.get("connected"):
    print("✅ IBKR 已连接")
    print("   应该通过 LIVE 路径，将 account_currency 保存到缓存")
else:
    print("❌ IBKR 未连接")
    print("   使用 CACHED 或 DEFAULT 路径，不会保存 account_currency")
    print(f"   这解释了为什么缓存中没有 account_currency 字段")
