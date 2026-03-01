#!/usr/bin/env python3
"""检查 JavaScript 语法错误"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app
import json

client = app.test_client()
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

# 提取所有 <script> 块
import re
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)

print(f"找到 {len(scripts)} 个 <script> 块\n")

for i, script in enumerate(scripts):
    print("=" * 80)
    print(f"Script 块 #{i+1} ({len(script)} 字节)")
    print("=" * 80)
    
    # 检查基本语法问题
    lines = script.split('\n')
    bracket_balance = 0
    paren_balance = 0
    
    for line_no, line in enumerate(lines):
        bracket_balance += line.count('{') - line.count('}')
        paren_balance += line.count('(') - line.count(')')
        
        # 检查明显的语法错误
        if 'analyzeStock' in line:
            print(f"  第 {line_no+1} 行 (analyzeStock): {line.strip()[:100]}")
    
    print(f"\n括号平衡: {{ = {bracket_balance}, ( = {paren_balance}")
    
    if bracket_balance != 0:
        print(f"  ⚠️ 花括号不平衡！缺少 {-bracket_balance if bracket_balance < 0 else bracket_balance} 个")
    if paren_balance != 0:
        print(f"  ⚠️ 圆括号不平衡！缺少 {-paren_balance if paren_balance < 0 else paren_balance} 个")
    
    # 检查引号平衡
    single_quotes = script.count("'") - script.count("\\'")
    double_quotes = script.count('"') - script.count('\\"')
    
    if single_quotes % 2 != 0:
        print(f"  ⚠️ 单引号不平衡")
    if double_quotes % 2 != 0:
        print(f"  ⚠️ 双引号不平衡")
    
    print()
