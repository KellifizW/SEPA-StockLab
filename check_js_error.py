#!/usr/bin/env python3
"""检查渲染后的 HTML 第 303 行和第 1667 行"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

lines = html.split('\n')
print(f"总行数: {len(lines)}")
print()

print("=" * 80)
print("第 303 行附近（±5 行）")
print("=" * 80)
if 303 < len(lines):
    for i in range(max(0, 298), min(len(lines), 309)):
        marker = ">>> " if i == 302 else "    "
        print(f"{marker}{i+1:4d}: {lines[i]}")
else:
    print(f"❌ 文件没有第 303 行（总共只有 {len(lines)} 行）")

print()
print("=" * 80)
print("第 1667 行附近（±5 行）")
print("=" * 80)
if 1667 < len(lines):
    for i in range(max(0, 1662), min(len(lines), 1673)):
        marker = ">>> " if i == 1666 else "    "
        print(f"{marker}{i+1:4d}: {lines[i]}")
else:
    print(f"❌ 文件没有第 1667 行（总共只有 {len(lines)} 行）")

print()
print("=" * 80)
print("检查 analyzeStock 函数位置")
print("=" * 80)
for i, line in enumerate(lines):
    if 'async function analyzeStock' in line or 'function analyzeStock' in line:
        print(f"✓ 找到 analyzeStock 在第 {i+1} 行")
        # 显示上下 3 行
        for j in range(max(0, i-3), min(len(lines), i+10)):
            marker = ">>> " if j == i else "    "
            print(f"{marker}{j+1:4d}: {lines[j]}")
