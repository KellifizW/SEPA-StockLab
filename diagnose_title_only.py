#!/usr/bin/env python3
"""
诊断：为什么只看得见标题
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import re

client = app.test_client()

print("=" * 80)
print("诊断: 为什么 ML 分析页面只显示标题")
print("=" * 80)

# 获取页面
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

print(f"\n✓ 页面状态码: {resp.status_code}")
print(f"✓ 页面大小: {len(html)} 字节")

# 1. 检查标题是否存在
if '<title>' in html:
    title_match = re.search(r'<title>(.*?)</title>', html)
    if title_match:
        print(f"\n✓ 页面标题: {title_match.group(1)}")

# 2. 检查 body 和 container 的可见性
print("\n【检查容器可见性】")

# 找到 body
body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
if body_match:
    body_content = body_match.group(0)
    
    # 检查 nav 标签
    nav_count = len(re.findall(r'<nav', body_content))
    print(f"  - <nav> 标签数: {nav_count}")
    
    # 检查 container-fluid
    container_count = len(re.findall(r'class="container-fluid', body_content))
    print(f"  - container-fluid 数: {container_count}")
    
    # 检查主要内容区域
    ticker_input = '<input type="text" class="form-control form-control-sm"' in body_content
    empty_state = 'id="emptyState"' in body_content
    result_area = 'id="resultArea"' in body_content
    
    print(f"  - 搜索框存在: {'✓' if ticker_input else '✗'}")
    print(f"  - emptyState 存在: {'✓' if empty_state else '✗'}")
    print(f"  - resultArea 存在: {'✓' if result_area else '✗'}")

# 3. 找到实际的初始可见性
print("\n【检查初始可见性 (d-none 类)】")

# 找 emptyState
empty_match = re.search(r'<div id="emptyState"[^>]*class="([^"]*)"', html)
if empty_match:
    classes = empty_match.group(1)
    has_d_none = 'd-none' in classes
    print(f"  emptyState class=\"{classes}\"")
    print(f"    → 初始可见: {'✗ 隐藏 (d-none)' if has_d_none else '✓ 显示'}")
else:
    print("  emptyState: 找不到！")

# 找 resultArea
result_match = re.search(r'<div id="resultArea"[^>]*class="([^"]*)"', html)
if result_match:
    classes = result_match.group(1)
    has_d_none = 'd-none' in classes
    print(f"  resultArea class=\"{classes}\"")
    print(f"    → 初始可见: {'✗ 隐藏 (d-none)' if has_d_none else '✓ 显示'}")
else:
    print("  resultArea: 找不到！")

# 4. 检查 fallback CSS
print("\n【检查 Fallback CSS】")
if '*:not(script):not(style)' in html:
    print("  ✓ Fallback CSS 规则已添加")
    # 找出 fallback 部分
    fallback_match = re.search(r'/\* Ensures basic visibility.*?\*/', html)
    if fallback_match:
        print(f"  ✓ 位置: 在 <style> 块中")
else:
    print("  ✗ Fallback CSS 未找到！")

# 5. 检查 container-fluid 是否可见
print("\n【检查关键 CSS 规则】")

# 找所有 style 块
styles = re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL)
print(f"  找到 {len(styles)} 个 <style> 块")

# 检查是否有隐藏容器的规则
for i, style in enumerate(styles):
    if 'container-fluid' in style:
        print(f"  ✓ style 块 {i+1} 包含 container-fluid 规则")
        # 提取规则
        container_rules = re.findall(r'\.container-fluid\s*\{([^}]*)\}', style)
        for rule in container_rules:
            print(f"    {rule[:60]}...")

# 6. 检查是否有全局隐藏规则
print("\n【检查是否有全局隐藏规则】")
has_display_none = re.search(r'(body|html|\.container-fluid)\s*\{[^}]*(display\s*:\s*none|visibility\s*:\s*hidden)[^}]*\}', html)
if has_display_none:
    print("  ✗ 找到可能隐藏内容的 CSS 规则!")
    print(f"    规则: {has_display_none.group(0)[:80]}...")
else:
    print("  ✓ 未找到隐藏整个容器的规则")

# 7. 检查 JavaScript 错误
print("\n【检查 JavaScript】")

# 寻找可能导致问题的代码
if 'DOMContentLoaded' in html:
    print("  ✓ 有 DOMContentLoaded 事件监听")
    
if 'console.log' in html:
    print("  ✓ 有诊断日志代码")

# 8. 最重要：检查内容是否在 block content 中
print("\n【检查 Jinja2 模板结构】")
if '{% block content %}' in html:
    # 找到 block content 和 {% endblock %} 之间的内容
    content_match = re.search(r'{{% block content %}(.*?){%% endblock %%}', html.replace('\\n', '\n'), re.DOTALL)
    if content_match:
        content = content_match.group(1)
        content_size = len(content)
        print(f"  ✓ block content 中有内容: {content_size} 个字节")
        
        # 计算内容大小比例
        content_ratio = (content_size / len(html)) * 100
        print(f"  ✓ 占总页面的 {content_ratio:.1f}%")
    else:
        print("  ✗ 找不到 block content 内容！")
else:
    print("  (页面已渲染，block content 标记已替换)")

print("\n" + "=" * 80)
print("【总结和修复方案】")
print("=" * 80)

# 分析可能的原因
issues = []

# 检查 emptyState 是否被隐藏
if empty_match and 'd-none' in empty_match.group(1):
    issues.append("❌ emptyState 初始被设置为隐藏 (d-none)")

# 检查是否没有 fallback CSS
if '*:not(script):not(style)' not in html:
    issues.append("❌ 没有 Fallback CSS，Bootstrap CDN 失败时无法显示")

# 检查是否有全局隐藏
if has_display_none:
    issues.append("❌ 有 CSS 规则可能隐藏了整个容器")

if not issues:
    print("\n✓ 没有发现明显的 CSS/模板问题")
    print("\n最可能的原因:")
    print("  1. Bootstrap CSS 未加载 (CDN 失败)")
    print("  2. JavaScript 执行错误阻止了页面初始化")
    print("  3. 浏览器特定问题 (兼容性)")
    print("\n诊断步骤:")
    print("  1. 按 F12 打开 DevTools")
    print("  2. 查看 Console 是否有红色错误")
    print("  3. 查看 Elements 检查 body 内是否有内容")
else:
    print("\n找到的问题:")
    for issue in issues:
        print(f"  {issue}")

print("=" * 80)
