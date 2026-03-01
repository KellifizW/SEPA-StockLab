#!/usr/bin/env python3
"""更详细的 JavaScript 分析"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

lines = html.split('\n')

print("=" * 80)
print("查找 DOMContentLoaded 块")
print("=" * 80)
for i, line in enumerate(lines):
    if 'DOMContentLoaded' in line:
        print(f"第 {i+1} 行: {line.strip()[:100]}")
        # 显示前后 5 行
        for j in range(max(0, i-2), min(len(lines), i+15)):
            marker = ">>>" if j == i else "   "
            print(f"{marker} {j+1:4d}: {lines[j][:120]}")
        print()

print("=" * 80)
print("查找所有函数定义")
print("=" * 80)
for i, line in enumerate(lines):
    if re.match(r'\s*(async\s+)?function\s+\w+\s*\(', line):
        print(f"第 {i+1} 行: {line.strip()[:80]}")

print()
print("=" * 80)
print("查找 onclick 调用")
print("=" * 80)
for i, line in enumerate(lines):
    if 'onclick=' in line.lower():
        print(f"第 {i+1} 行: {line.strip()[:100]}")

print()
print("=" * 80)
print("检查 <script> 块数量和位置")
print("=" * 80)
script_count = 0
for i, line in enumerate(lines):
    if '<script' in line:
        script_count += 1
        print(f"<script> #{script_count} 在第 {i+1} 行")
    if '</script>' in line:
        print(f"</script> 在第 {i+1} 行")
