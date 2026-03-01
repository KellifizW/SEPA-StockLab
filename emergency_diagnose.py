#!/usr/bin/env python3
"""紧急诊断：为什么页面完全空白"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import traceback

client = app.test_client()

print("=" * 80)
print("🚨 紧急诊断：页面完全空白")
print("=" * 80)

try:
    resp = client.get('/ml/analyze')
    html = resp.data.decode('utf-8')
    
    print(f"\n✓ 页面状态码: {resp.status_code}")
    print(f"✓ 页面大小: {len(html)} 字节")
    
    if resp.status_code != 200:
        print(f"❌ 服务器错误！")
        print(f"内容: {html[:500]}")
    else:
        # 检查是否有内容
        if len(html) < 100:
            print("❌ 页面内容极少！(<100 字节)")
            print(f"内容:\n{html}")
        else:
            # 检查关键标签
            print("\n【检查关键 HTML 标签】")
            checks = {
                'DOCTYPE': '<!DOCTYPE',
                'HTML': '<html',
                'BODY': '<body',
                'TITLE': '<title>',
                'NAVBAR': '<nav',
                'CONTENT': '{% block content',  # 模板标记应该被替换
                'TEXT': 'ML 個股分析',
            }
            
            for name, pattern in checks.items():
                found = pattern in html
                status = '✓' if found else '✗'
                print(f"  {status} {name:20} {'found' if found else 'MISSING'}")
            
            # 显示前 500 字符
            print("\n【页面开头内容】")
            print(html[:500])
            print("\n...")
            
            # 显示后 500 字符
            print("\n【页面结尾内容】")
            print(html[-500:])

except Exception as e:
    print(f"\n❌ 错误: {e}")
    traceback.print_exc()

print("\n" + "=" * 80)
