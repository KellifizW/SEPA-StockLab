#!/usr/bin/env python3
"""测试JavaScript语法有效性"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

# 提取所有脚本
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)

print("=" * 80)
print("JavaScript 脚本检查（查找明显语法错误）")
print("=" * 80)
print()

for script_idx, script in enumerate(scripts):
    if not script.strip():
        continue
        
    print(f"脚本块 #{script_idx + 1}:")
    
    # 检查引号平衡
    temp = script.replace('\\"', '').replace("\\'", '')  # 移除转义的引号
    single_quotes = temp.count("'")
    double_quotes = temp.count('"')
    
    print(f"  行数: {len(script.split(chr(10)))}")
    print(f"  大小: {len(script)} 字节")
    
    if single_quotes % 2 != 0:
        print(f"  ⚠️ 单引号总数: {single_quotes} (不平衡！)")
    else:
        print(f"  ✓ 单引号: {single_quotes} (平衡)")
    
    if double_quotes % 2 != 0:
        print(f"  ⚠️ 双引号总数: {double_quotes} (不平衡！)")
    else:
        print(f"  ✓ 双引号: {double_quotes} (平衡)")
    
    # 检查括号
    temp_no_strings = re.sub(r'"[^"]*"', '', script)  # 移除字符串
    temp_no_strings = re.sub(r"'[^']*'", '', temp_no_strings)
    
    brackets = temp_no_strings.count('{') - temp_no_strings.count('}')
    parens = temp_no_strings.count('(') - temp_no_strings.count(')')
    
    if brackets != 0:
        print(f"  ⚠️ 花括号: 差 {brackets}")
    else:
        print(f"  ✓ 花括号: 平衡")
    
    if parens != 0:
        print(f"  ⚠️ 圆括号: 差 {parens}")
    else:
        print(f"  ✓ 圆括号: 平衡")
    
    # 检查常见错误
    if 'analyzeStock' in script:
        print(f"  ✓ 包含 analyzeStock 定义/调用")
    
    if 'onclick="switchMlMode(\'review\')"' in script or "onclick=\"switchMlMode('review')\"" in script:
        print(f"  ✓ switchMlMode 调用正确")
    elif 'switchMlMode' in script:
        print(f"  ⚠️ switchMlMode 出现但格式可能有误")
    
    print()

print("=" * 80)
print("总结: 如果所有脚本都平衡，JavaScript 应该可以正常加载")
print("=" * 80)
