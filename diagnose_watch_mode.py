#!/usr/bin/env python3
"""测试 watch mode 的问题"""
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()

print("=" * 80)
print("Watch Mode 诊断")
print("=" * 80)
print()

# 首先分析 HTML
print("1️⃣ 检查 HTML 结构")
print("=" * 80)
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

checks = {
    'resultArea div': '<div id="resultArea"' in html,
    'watchPanel div': '<div id="watchPanel"' in html,
    'watchTicker element': '<span id="watchTicker"' in html,
    'watchPrice element': '<span id="watchPrice"' in html,
    'intradayChartContainer': '<div id="intradayChartContainer"' in html,
    'switchMlMode button': 'onclick="switchMlMode(\'watch\')"' in html or 'switchMlMode("watch")' in html or "switchMlMode('watch')" in html,
    'mode-btn-group': 'class="mode-btn-group' in html,
}

for name, result in checks.items():
    print(f"  {'✓' if result else '✗'} {name}")

print()
print("2️⃣ 检查 JavaScript 函数定义")
print("=" * 80)

js_checks = {
    'switchMlMode function': 'function switchMlMode(mode)' in html,
    'initWatchMode function': 'function initWatchMode(ticker)' in html,
    'loadIntradayChart function': 'async function loadIntradayChart(ticker, interval)' in html,
    'getMarketStatus function': 'function getMarketStatus()' in html,
    'updateMarketStatus function': 'function updateMarketStatus()' in html,
}

for name, result in checks.items():
    print(f"  {'✓' if result else '✗'} {name}")

print()
print("3️⃣ 测试 API - /api/chart/intraday/AEM")
print("=" * 80)

resp = client.get('/api/chart/intraday/AEM?interval=5m')
print(f"状态码: {resp.status_code}")

if resp.status_code == 200:
    data = resp.get_json()
    print(f"ok: {data.get('ok')}")
    if data.get('ok'):
        print(f"K 线数: {len(data.get('candles', []))}")
        print(f"EMA9 点数: {len(data.get('ema9', []))}")
        print(f"EMA21 点数: {len(data.get('ema21', []))}")
        print(f"VWAP 点数: {len(data.get('vwap', []))}")
        print(f"ORH: {data.get('orh')}")
        print(f"LOD: {data.get('lod')}")
        print(f"signals 存在: {'signals' in data}")
        if data.get('signals'):
            print(f"  - setup_advice: {data.get('signals', {}).get('setup_advice')}")
        print("✓ API 返回数据正常")
    else:
        print(f"✗ API 返回 error: {data.get('error')}")
else:
    print(f"✗ API 请求失败")

print()
print("4️⃣ 检查 watchPanel 显示状态")
print("=" * 80)

# 检查 watchPanel 默认的 d-none 类
if 'id="watchPanel" class="d-none"' in html:
    print("✓ watchPanel 默认隐藏 (d-none 类)")
else:
    print("✗ watchPanel 初始状态异常")

print()
print("=" * 80)
print("可能的原因:")
print("=" * 80)
print("""
1. switchMlMode('watch') 没有正确执行
   → 检查浏览器 Console 是否有错误

2. initWatchMode 没有被调用
   → switchMlMode 中的条件可能有问题

3. loadIntradayChart API 失败
   → 检查网络请求是否成功

4. LightweightCharts 库未加载
   → 检查 CDN 是否可用

5. HTML 元素缺失
   → 检查 watchPanel div 是否完整

建议调试步骤:
1. 打开浏览器 DevTools (F12)
2. 切换到 Console 标签
3. 点击 "📡 盯盤模式 Watch Market" 按钮
4. 查看是否有错误消息
5. 检查 Network 标签中 /api/chart/intraday 的请求
""")
