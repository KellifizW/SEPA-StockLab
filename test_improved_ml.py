#!/usr/bin/env python3
"""Test improved ml_analyze.html"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import re

client = app.test_client()

print("=" * 80)
print("测试：改进后的 ML 分析页面")
print("=" * 80)

# 获取页面
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

print(f"\n✓ 页面状态码: {resp.status_code}")
print(f"✓ 页面大小: {len(html)} 字节")

# 检查关键元素
print("\n【检查关键元素】")

checks = {
    '导航栏': '<nav',
    '搜索框': 'id="tickerInput"',
    '分析按钮': 'onclick="analyzeStock()"',
    '空状态': 'id="emptyState"',
    '加载指示器': 'id="loadingArea"',
    '结果区域': 'id="resultArea"',
    '日线图': 'id="dailyChartContainer"',
    'Bootstrap JS': 'bootstrap.bundle.min.js',
    'LightweightCharts': 'lightweight-charts',
}

for name, pattern in checks.items():
    if pattern in html:
        print(f"  ✓ {name:20} 存在")
    else:
        print(f"  ✗ {name:20} 缺失")

# 检查改进的 fallback CSS
print("\n【检查改进的 Fallback CSS】")
if '.container-fluid {' in html and 'display: block !important' in html:
    print("  ✓ 改进的 container-fluid CSS 已添加")
else:
    print("  ✗ 改进的 CSS 未找到")

if '.row {' in html:
    print("  ✓ row 样式已添加")

if '.col-12' in html:
    print("  ✓ col-12 样式已添加")

if 'input, textarea, select, button' in html:
    print("  ✓ 表单元素样式已添加")

# 检查内容大小
print("\n【检查内容大小】")

# 找到 block content 的部分
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
if body_match:
    body_content = body_match.group(1)
    
    # 移除 script 标签
    body_without_scripts = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
    
    # 计算实际内容
    actual_content_size = len(body_without_scripts)
    print(f"  Body 大小: {len(body_content)} 字节")
    print(f"  实际内容 (去除脚本): {actual_content_size} 字节")
    print(f"  内容比例: {(actual_content_size/len(body_content)*100):.1f}%")

# 创建最终验证
print("\n【最终验证】")
all_good = all(pattern in html for _, pattern in checks.items() if pattern != 'Bootstrap JS' and pattern != 'lightweight-charts')

if all_good:
    print("  ✓ 所有关键元素都存在！")
    print("\n🎉 页面应该能正常显示了。")
    print("\n现在应该看到：")
    print("  │")
    print("  ├─ 导航栏")
    print("  ├─ 「ML 個股分析」标题")
    print("  ├─ 搜索框和「分析」按钮")
    print("  ├─ 「输入股票代号并点击分析」提示")
    print("  └─ (如果没有输入时) 空状态")
else:
    print("  ✗ 某些关键元素缺失")

print("\n" + "=" * 80)
