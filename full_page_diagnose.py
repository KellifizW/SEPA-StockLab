#!/usr/bin/env python3
"""检查整个页面，包括 head"""
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
print("完整页面诊断")
print("=" * 80)

print(f"\n总长度: {len(html)} 字节")

# 检查 DOCTYPE
if html.startswith('<!DOCTYPE'):
    print("✓ DOCTYPE 正确")
else:
    print("✗ DOCTYPE 缺失")

# 检查 <head> 和 <body>
head_match = re.search(r'<head[^>]*>(.*?)</head>', html, re.DOTALL)
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)

if head_match:
    head_content = head_match.group(1)
    print(f"✓ <head> found: {len(head_content)} 字节")
    
    # 检查 <style> 块在 head 里
    style_count = len(re.findall(r'<style', head_content))
    print(f"  <style> 块数: {style_count}")
    
    # 检查 CSS 链接
    links = re.findall(r'<link[^>]+href="([^"]+)"', head_content)
    print(f"  <link> 链接数: {len(links)}")
    for link in links[:3]:
        print(f"    - {link}")
else:
    print("✗ <head> 缺失")

if body_match:
    body_content = body_match.group(1)
    print(f"✓ <body> found: {len(body_content)} 字节")
else:
    print("✗ <body> 缺失")

# 检查所有 <style> 块位置
print("\n【所有 <style> 块】")
style_blocks = re.finditer(r'<style[^>]*>(.*?)</style>', html, re.DOTALL)
for i, match in enumerate(style_blocks, 1):
    start = match.start()
    content = match.group(1)
    print(f"  块 {i}: 位置 {start}, 大小 {len(content)} 字节")
    # 显示前 200 字符
    preview = content[:200].replace('\n', ' ')
    print(f"    内容: {preview}...")

# 检查 title
title_match = re.search(r'<title>(.*?)</title>', html)
if title_match:
    print(f"\n✓ <title>: {title_match.group(1)}")
else:
    print("\n✗ <title> 缺失")

# 检查导航栏内容
print("\n【导航栏内容】")
nav_match = re.search(r'<nav[^>]*>(.*?)</nav>', body_content, re.DOTALL) if body_match else None
if nav_match:
    nav_text = re.sub(r'<[^>]+>', '', nav_match.group(1))
    nav_text = nav_text.strip()[:200]
    print(f"✓ 导航栏文本: {nav_text}")
else:
    print("✗ 导航栏缺失")

# 检查主容器
print("\n【主容器】")
container_match = re.search(r'<div class="container-fluid"[^>]*>(.*?)</div>', body_content, re.DOTALL) if body_match else None
if container_match:
    container_content = container_match.group(1)
    print(f"✓ container-fluid 找到: {len(container_content)} 字节")
    # 寻找标题
    h4_match = re.search(r'<h4[^>]*>(.*?)</h4>', container_content)
    if h4_match:
        h4_text = re.sub(r'<[^>]+>', '', h4_match.group(1))
        print(f"  ✓ h4 title: {h4_text[:100]}")
else:
    print("✗ container-fluid 缺失")

# 检查是否有错误
print("\n【检查错误页面标记】")
if 'CONTENT MISSING' in html:
    print("✗ 页面包含 'CONTENT MISSING' 标记 - 模板可能有问题")
if 'traceback' in html.lower():
    print("✗ 页面包含 traceback - 可能有 Python 错误")
if 'jinja2' in html.lower():
    print("✗ 页面包含 jinja2 错误")

print("\n" + "=" * 80)
