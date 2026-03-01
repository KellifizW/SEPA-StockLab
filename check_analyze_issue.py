#!/usr/bin/env python3
"""找出 analyzeStock 相关的问题"""
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
print("检查 analyzeStock 调用顺序")
print("=" * 80)

# 找到 analyzeStock 定义
definition_line = None
for i, line in enumerate(lines):
    if re.search(r'async function analyzeStock\s*\(', line):
        definition_line = i
        print(f"\n✓ analyzeStock 函数定义在第 {i+1} 行")
        break

# 找到所有 analyzeStock 调用
for i, line in enumerate(lines):
    if 'analyzeStock()' in line or 'analyzeStock ()' in line:
        if definition_line is None or i == definition_line:
            continue
        relation = "在定义之前" if i < definition_line else "在定义之后"
        print(f"  第 {i+1} 行 ({relation}): {line.strip()[:100]}")

print()
print("=" * 80)
print("检查 analyzeStock 函数本身")
print("=" * 80)

if definition_line is not None:
    # 显示函数前后
    for j in range(max(0, definition_line - 3), min(len(lines), definition_line + 20)):
        marker = ">>>" if j == definition_line else "   "
        print(f"{marker} {j+1:4d}: {lines[j]}")

print()
print("=" * 80)
print("检查是否有语法错误信号")
print("=" * 80)

# 检查是否有明显的语法错误（括号不匹配）
bracket_count = 0
for i, line in enumerate(lines):
    if i > 450 and i < 750:  # JavaScript 部分  
        bracket_count += line.count('{') - line.count('}')
        if bracket_count > 10:
            print(f"⚠️ 第 {i+1} 行之后可能有括号不匹配 (balance: {bracket_count})")
            break

print("\n✓ 括号平衡检查完成")
