#!/usr/bin/env python3
"""深入检查标题和内容区域"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import re

client = app.test_client()
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

# 获取 body
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
body_content = body_match.group(1) if body_match else ""

print("=" * 80)
print("深入检查标题和内容")
print("=" * 80)

# 寻找所有 h4 标签
h4s = re.findall(r'<h4[^>]*>(.*?)</h4>', body_content, re.DOTALL)
print(f"\n【找到 {len(h4s)} 个 h4 标签】")
for i, h4 in enumerate(h4s[:5]):
    text = re.sub(r'<[^>]+>', '', h4).strip()
    print(f"  h4 {i+1}: {text[:80]}")

# 寻找所有 h1, h2, h3 标签
for tag in ['h1', 'h2', 'h3']:
    tags = re.findall(f'<{tag}[^>]*>(.*?)</{tag}>', body_content, re.DOTALL)
    if tags:
        print(f"\n【找到 {len(tags)} 个 {tag} 标签】")
        for i, t in enumerate(tags[:3]):
            text = re.sub(r'<[^>]+>', '', t).strip()
            print(f"  {tag} {i+1}: {text[:80]}")

# 查找包含 "ML 個股分析" 的行
print("\n【包含 'ML 個股分析' 的上下文】")
ml_pos = body_content.find('ML 個股分析')
if ml_pos >= 0:
    start = max(0, ml_pos - 200)
    end = min(len(body_content), ml_pos + 400)
    context = body_content[start:end]
    print(context)
else:
    print("✗ 不能找到 'ML 個股分析' 文本")

# 检查 tickerInput 输入框
print("\n【tickerInput 输入框】")
input_match = re.search(r'<input[^>]*id="tickerInput"[^>]*>(.*?)', body_content)
if input_match:
    input_tag = input_match.group(0)[:200]
    print(f"✓ 找到: {input_tag}")
else:
    print("✗ 未找到")

# 检查分析按钮
print("\n【分析按钮】")
btn_match = re.search(r'<button[^>]*onclick="analyzeStock\(\)"[^>]*>(.*?)</button>', body_content, re.DOTALL)
if btn_match:
    btn_text = btn_match.group(1).strip()
    print(f"✓ 找到: {btn_text}")
else:
    print("✗ 未找到分析按钮")

# 获取最外层的 div 结构
print("\n【顶层 div 数量和内容】")
# 从 body 开始的第一个 <div 开始
first_div_match = re.search(r'<div[^>]*>', body_content)
if first_div_match:
    pos = first_div_match.start()
    tag_part = body_content[pos:pos+150]
    print(f"✓ 第一个 div: {tag_part}")

# 检查是否有大量空白或隐藏元素
print("\n【检查是否有 display:none 或 visibility:hidden】")
if 'display:none' in body_content or 'display: none' in body_content:
    print("✗ 页面含有 display:none")
if 'visibility:hidden' in body_content:
    print("✗ 页面含有 visibility:hidden")

# 统计标签
print("\n【HTML 元素统计】")
tags = set(re.findall(r'<(\w+)', body_content))
print(f"  总标签类型: {len(tags)}")
print(f"  标签列表: {sorted(list(tags))[:20]}")

print("\n" + "=" * 80)
