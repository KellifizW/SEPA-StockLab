#!/usr/bin/env python3
"""保存完整渲染的 HTML 到文件"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

# 保存完整 HTML
output_file = ROOT / 'ml_analyze_rendered.html'
output_file.write_text(html, encoding='utf-8')

print(f"✓ 完整 HTML 已保存到: {output_file}")
print(f"  大小: {len(html)} 字节")
print(f"  可以使用浏览器打开查看具体样子")

# 统计信息
import re
h4_count = len(re.findall(r'<h4', html))
input_count = len(re.findall(r'<input', html))
button_count = len(re.findall(r'<button', html))
script_count = len(re.findall(r'<script', html))
style_count = len(re.findall(r'<style', html))

print(f"\n元素统计:")
print(f"  h4 标签: {h4_count}")
print(f"  input 标签: {input_count}")
print(f"  button 标签: {button_count}")
print(f"  script 块: {script_count}")
print(f"  style 块: {style_count}")

# 检查 navbar 和 content 区域
navbar_match = re.search(r'<nav[^>]*>.*?</nav>', html, re.DOTALL)
content_match = re.search(r'<div class="container-fluid py-3 px-4">(.*?)</div>\s*<div id="toast-container">', html, re.DOTALL)

if navbar_match:
    print(f"\n✓ navbar 区域: {len(navbar_match.group(0))} 字节")
if content_match:
    content = content_match.group(1)
    print(f"✓ content 区域: {len(content)} 字节")
    # 获取前 500 字符
    print(f"  内容预览: {content[:500]}")
