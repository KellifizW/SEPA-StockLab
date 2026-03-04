#!/usr/bin/env python3
"""
Inspect all IBKR account values to find currency field.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C

def main():
    print("=" * 80)
    print("IBKR 账户字段检查 - 寻找货币信息")
    print("=" * 80)
    print()
    
    if not C.IBKR_ENABLED:
        print("❌ IBKR 在 trader_config.py 中被禁用")
        print("   设置: IBKR_ENABLED = True")
        return
    
    print("正在连接 IBKR...")
    try:
        from modules import ibkr_client
        
        # 获取连接状态
        status = ibkr_client.get_status()
        print(f"\n连接状态: {status.get('state')}")
        print(f"已连接: {status.get('connected')}")
        
        if not status.get('connected'):
            print("\n❌ 未连接到 IBKR")
            print("   请确保 IBKR 客户端正在运行 (TWS 或 Gateway)")
            return
        
        print(f"账户: {status.get('account')}")
        print()
        
        # 现在尝试直接访问 _ib 对象来获取所有字段
        print("📊 从 IBKR 获取所有账户字段...")
        print("-" * 80)
        
        # 我们需要内部访问 ibkr_client 的 _ib 对象
        # 让我们创建一个临时函数在 ibkr_client 中调用
        
        # 方案：我们可以通过修改 ibkr_client 来导出所有字段
        # 或者我们可以创建一个测试脚本
        
        # 让我尝试直接连接
        from ib_insync import IB
        
        ib = IB()
        ib.connect('127.0.0.1', 7497, clientId=999)  # 使用不同的 clientId
        
        print("\n✓ 已连接到本地 IBKR")
        
        # 获取账户值
        acct_vals = ib.accountValues()
        
        print(f"\n找到 {len(acct_vals)} 个账户字段:")
        print()
        
        # 按照标签排序并分类
        currency_related = []
        cash_related = []
        other_values = []
        
        for av in acct_vals:
            tag = av.tag
            value = av.value
            
            # 分类
            if 'currency' in tag.lower() or 'curr' in tag.lower():
                currency_related.append((tag, value))
            elif 'cash' in tag.lower() or 'balance' in tag.lower():
                cash_related.append((tag, value))
            else:
                other_values.append((tag, value))
        
        # 打印货币相关字段
        if currency_related:
            print("💱 货币相关字段:")
            print("-" * 80)
            for tag, value in currency_related:
                print(f"  {tag:30} = {value}")
            print()
        
        # 打印现金相关字段
        if cash_related:
            print("💰 现金/余额相关字段:")
            print("-" * 80)
            for tag, value in cash_related:
                print(f"  {tag:30} = {value}")
            print()
        
        # 打印所有字段（用于参考）
        print("📋 所有账户字段:")
        print("-" * 80)
        for tag, value in sorted(other_values + currency_related + cash_related):
            print(f"  {tag:30} = {value}")
        
        print()
        print("=" * 80)
        print("\n🔍 结论:")
        
        # 尝试找到货币字段
        all_tags = {tag: value for tag, value in acct_vals}
        if 'Currency' in all_tags:
            print(f"✓ 找到货币字段: Currency = {all_tags['Currency']}")
        elif 'BaseCurrency' in all_tags:
            print(f"✓ 找到基础货币字段: BaseCurrency = {all_tags['BaseCurrency']}")
        else:
            print("⚠️  在 accountValues() 中未找到直接的货币字段")
            print("   但找到的货币相关字段：")
            for tag, value in currency_related:
                print(f"     - {tag} = {value}")
        
        print("\n💡 建议:")
        if currency_related:
            print("   可以从上面的货币相关字段中提取账户基础货币")
        else:
            print("   可能需要从 NetLiquidation, CashBalance 等字段的货币标签中推导")
        
        ib.disconnect()
        
    except ConnectionRefusedError:
        print("❌ 无法连接到 IBKR (127.0.0.1:7497)")
        print("   请确保:")
        print("   1. TWS (Trader Workstation) 或 IB Gateway 正在运行")
        print("   2. Socket API 已启用")
        print("   3. 端口设置正确 (默认: 7497 for TWS, 4002 for Gateway)")
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
