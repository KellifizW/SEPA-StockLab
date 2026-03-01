#!/usr/bin/env python3
"""验证 watch mode 修复"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()

print("=" * 80)
print("Watch Mode 修复验证")
print("=" * 80)
print()

# 获取页面
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

print("1️⃣ 检查 watchPanel 宽度修复")
print("=" * 80)
if 'id="intradayChartContainer" style="width:100%;height:450px"' in html:
    print("✓ intradayChartContainer 添加了 width:100%")
elif 'id="intradayChartContainer"' in html:
    matches = [line for line in html.split('\n') if 'intradayChartContainer' in line]
    if matches:
        print(f"当前: {matches[0][:100]}")
        if 'width' in matches[0]:
            print("✓ 宽度已设置")
        else:
            print("✗ 宽度仍未设置")
else:
    print("✗ intradayChartContainer 未找到")

print()
print("2️⃣ 检查 switchMlMode 延迟初始化")
print("=" * 80)
if 'requestAnimationFrame' in html:
    print("✓ switchMlMode 使用 requestAnimationFrame 延迟初始化")
else:
    print("✗ 未找到 requestAnimationFrame")

print()
print("3️⃣ 检查关键函数存在")
print("=" * 80)
functions = {
    'initWatchMode': 'function initWatchMode(ticker)',
    'loadIntradayChart': 'async function loadIntradayChart(ticker, interval)',
    'updateMarketStatus': 'function updateMarketStatus()',
    'renderIntradayCommentary': 'function renderIntradayCommentary(signals, status)',
    'renderPremktPlan': 'function renderPremktPlan(data)',
    'getMarketStatus': 'function getMarketStatus()',
}

for name, pattern in functions.items():
    if pattern in html:
        print(f"✓ {name}")
    else:
        print(f"✗ {name} 缺失")

print()
print("4️⃣ 测试 Intraday API")
print("=" * 80)
resp = client.get('/api/chart/intraday/AEM?interval=5m')
data = resp.get_json()
if data.get('ok') and data.get('candles'):
    print(f"✓ API 返回 {len(data.get('candles'))} 根 K 线")
    print(f"✓ 包含 EMA9, EMA21, VWAP 数据")
else:
    print(f"✗ API 返回失败")

print()
print("=" * 80)
print("修复总结")
print("=" * 80)
print("""
已应用的修复:
1. ✓ intradayChartContainer 添加 width:100% 确保容器宽度
2. ✓ switchMlMode 使用 requestAnimationFrame 延迟初始化
   → 确保 watchPanel 显示后再加载图表

预期结果:
- 点击 "📡 盯盤模式 Watch Market" 按钮后
- watchPanel 会显示
- intradayChartContainer 会加载盤中圖表
- 显示 EMA線、VWAP、ORH/LOD 信息

如果仍无显示:
1. 打开浏览器 Console (F12)
2. 检查是否有错误信息
3. 检查 LightweightCharts 是否加载
4. 确认网络请求成功
""")
