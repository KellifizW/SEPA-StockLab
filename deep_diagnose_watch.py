#!/usr/bin/env python3
"""深入诊断 watch mode 无法显示的问题"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()

print("=" * 80)
print("📊 Watch Mode 深度诊断 - 寻找无法显示的原因")
print("=" * 80)
print()

# 获取完整的 HTML
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

lines = html.split('\n')

print("1️⃣ 检查 watchPanel HTML 结构完整性")
print("=" * 80)

# 找到 watchPanel 开始和结束位置
watch_start = None
watch_end = None
for i, line in enumerate(lines):
    if 'id="watchPanel"' in line:
        watch_start = i
    if watch_start is not None and '</div>' in line and i > watch_start:
        # 寻找匹配的结束标签
        pass

if watch_start:
    print(f"✓ watchPanel 开始于第 {watch_start + 1} 行")
    # 检查 watchPanel 内部的关键元素
    watchpanel_content = '\n'.join(lines[watch_start:watch_start+100])
    
    elements = {
        'watchTicker': 'id="watchTicker"',
        'watchPrice': 'id="watchPrice"',
        'marketStatusBadge': 'id="marketStatusBadge"',
        'watchCountdown': 'id="watchCountdown"',
        'intradayChartContainer': 'id="intradayChartContainer"',
        'premktWarning': 'id="premktWarning"',
        'closedNotice': 'id="closedNotice"',
        'intraday-btn 按钮': 'class="intraday-btn',
        'intradayCommentaryArea': 'id="intradayCommentaryArea"',
    }
    
    for name, selector in elements.items():
        if selector in watchpanel_content:
            print(f"  ✓ {name}: 存在")
        else:
            print(f"  ✗ {name}: 缺失")
else:
    print("✗ watchPanel 元素未找到！")

print()
print("2️⃣ 检查 switchMlMode 函数实现")
print("=" * 80)

# 查找 switchMlMode 函数
sw_match = None
for i, line in enumerate(lines):
    if 'function switchMlMode(mode)' in line:
        sw_match = i
        break

if sw_match:
    print(f"✓ switchMlMode 函数在第 {sw_match + 1} 行")
    # 显示函数内容
    for j in range(sw_match, min(sw_match + 30, len(lines))):
        print(f"  {j+1:4d}: {lines[j]}")
else:
    print("✗ switchMlMode 函数未找到")

print()
print("3️⃣ 检查 initWatchMode 函数")
print("=" * 80)

init_match = None
for i, line in enumerate(lines):
    if 'function initWatchMode(ticker)' in line:
        init_match = i
        break

if init_match:
    print(f"✓ initWatchMode 函数在第 {init_match + 1} 行")
    # 显示函数前几行
    for j in range(init_match, min(init_match + 20, len(lines))):
        print(f"  {j+1:4d}: {lines[j][:100]}")
else:
    print("✗ initWatchMode 函数未找到")

print()
print("4️⃣ 检查 LightweightCharts 和其他依赖加载")
print("=" * 80)

deps = {
    'LightweightCharts CDN': 'lightweight-charts',
    'Bootstrap CDN': 'bootstrap@5.3.3',
    'Chart.js': 'chart.js',
    'Bootstrap Icons': 'bootstrap-icons',
}

for name, pattern in deps.items():
    if pattern in html:
        print(f"  ✓ {name}: 存在")
    else:
        print(f"  ✗ {name}: 缺失")

print()
print("5️⃣ 检查全局变量初始化")
print("=" * 80)

vars_to_check = [
    '_mlWatchTicker',
    '_mlWatchCurrentInterval',
    '_mlWatchChart',
    '_mlWatchData',
    '_mlWatchInterval',
    '_mlCountdownInterval',
    '_mlCountdownRemaining',
]

for var in vars_to_check:
    if f'let {var}' in html or f'var {var}' in html:
        print(f"  ✓ {var}: 声明")
    else:
        print(f"  ✗ {var}: 未声明")

print()
print("6️⃣ 检查 watchPanel 的 CSS 类")
print("=" * 80)

# 查找 watchPanel 行
for i, line in enumerate(lines):
    if 'id="watchPanel"' in line and 'class=' in line:
        print(f"  <div id=\"watchPanel\" {line[line.find('class='):line.find('class=')+50]}...")
        if 'd-none' in line:
            print("  ✓ 默认隐藏状态（d-none 存在）")
        elif 'display' in line:
            print("  ℹ️ 有其他 display 属性")
        break

print()
print("=" * 80)
print("💡 可能的问题和解决方案")
print("=" * 80)
print("""
情况 1: 如果 watchPanel 元素不完整
→ watchPanel div 可能被截断或部分内容缺失
→ 检查 HTML 是否有语法错误

情况 2: 如果 initWatchMode 没有被调用
→ switchMlMode 的逻辑可能有问题
→ 检查是否有 JavaScript 执行错误

情况 3: 如果 LightweightCharts 未加载
→ CDN 可能无法访问
→ 检查浏览器 Network 标签中的 CDN 请求

情况 4: 如果全局变量未初始化
→ JavaScript 脚本执行顺序错误
→ 可能有其他代码阻止了变量声明

情况 5: 如果元素存在但不显示
→ CSS 可能有问题（例如宽度仍然是 0）
→ 检查浏览器 DevTools 中的元素尺寸

建议的调试步骤:
1. 打开浏览器 DevTools (F12)
2. 点击 "📡 盯盤模式" 按钮
3. 在 Console 标签中输入: console.log(document.getElementById('watchPanel'))
   → 如果返回 null，表示元素未找到
   → 如果返回元素，检查 classList（是否包含 'd-none'）
4. 输入: console.log(_mlWatchTicker)
   → 应该显示股票代号（如 'AEM'）
5. 在 Network 标签中查看 /api/chart/intraday/AEM 的请求
   → 应该返回 200 OK + JSON 数据

如果以上都正常，问题可能在图表库的渲染。
""")
