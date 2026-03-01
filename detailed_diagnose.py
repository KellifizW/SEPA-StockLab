#!/usr/bin/env python3
"""详细诊断：检查渲染的 HTML 具体内容"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import re

client = app.test_client()
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

print("=" * 80)
print("详细诊断：HTML 内容分析")
print("=" * 80)

# 找到 body 内容
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
if not body_match:
    print("\n❌ 找不到 <body> 标签！")
    sys.exit(1)

body_content = body_match.group(1)
print(f"\n✓ Body 内容大小: {len(body_content)} 字节")

# 移除脚本和样式
body_no_scripts = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
body_no_styles = re.sub(r'<style[^>]*>.*?</style>', '', body_no_scripts, flags=re.DOTALL)

print(f"✓ 去除脚本后: {len(body_no_scripts)} 字节")
print(f"✓ 去除脚本+样式后: {len(body_no_styles)} 字节")

# 检查关键元素
print("\n【检查关键 HTML 元素】")
elements = {
    '导航栏 <nav>': '<nav',
    '搜索框 <input>': '<input type="text" class="form-control form-control-sm"',
    '分析按钮': 'onclick="analyzeStock()"',
    '搜索框 id="tickerInput"': 'id="tickerInput"',
    '空状态 id="emptyState"': 'id="emptyState"',
    '结果区域 id="resultArea"': 'id="resultArea"',
    'ML 個股分析 (heading 中文)': 'ML 個股分析',
    'Martin Luk': 'Martin Luk',
}

for name, pattern in elements.items():
    if pattern in body_content:
        # 找到位置
        pos = body_content.find(pattern)
        context_start = max(0, pos - 100)
        context_end = min(len(body_content), pos + len(pattern) + 100)
        context = body_content[context_start:context_end]
        print(f"✓ {name:40} 找到 (位置: {pos})")
    else:
        print(f"✗ {name:40} 缺失")

# 检查结构
print("\n【检查 HTML 结构】")

# 寻找 container-fluid
container_count = len(re.findall(r'<div class="container-fluid', body_content))
print(f"  container-fluid: {container_count}")

# 寻找 row
row_count = len(re.findall(r'<div class="row', body_content))
print(f"  row: {row_count}")

# 寻找所有 div
div_count = len(re.findall(r'<div', body_content))
print(f"  总 div 数: {div_count}")

# 寻找所有可见的文本
print("\n【第一次看到的中文文本】")
lines = body_no_styles.split('\n')
text_found = False
for i, line in enumerate(lines):
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', line)
    if len(text.strip()) > 10 and any(ord(c) > 127 for c in text):  # 有中文
        print(f"  行 {i}: {text.strip()[:100]}")
        text_found = True
        if text_found:
            break

# 检查是否有隐藏的 CSS
print("\n【检查 CSS 隐藏规则】")
style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', body_content, re.DOTALL)
print(f"  找到 {len(style_blocks)} 个 <style> 块")

for i, style in enumerate(style_blocks):
    if 'display: none' in style or 'display:none' in style:
        print(f"  ✗ style 块 {i+1} 包含 display: none")
    if 'visibility: hidden' in style:
        print(f"  ✗ style 块 {i+1} 包含 visibility: hidden")
    if '.container-fluid' in style:
        print(f"  ✓ style 块 {i+1} 有 .container-fluid 规则")

# 尝试提取可见文本
print("\n【提取文本内容】")
# 移除所有 HTML 标签和脚本
text_only = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
text_only = re.sub(r'<style[^>]*>.*?</style>', '', text_only, flags=re.DOTALL)
text_only = re.sub(r'<[^>]+>', '', text_only)
text_only = re.sub(r'\s+', ' ', text_only)

print(f"  所有文本: {text_only[:200]}")

print("\n" + "=" * 80)
