#!/usr/bin/env python3
"""提取 script 块 #4 来检查问题"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

client = app.test_client()
resp = client.get('/ml/analyze?ticker=AEM')
html = resp.data.decode('utf-8')

# 提取脚本块
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)

if len(scripts) >= 4:
    script4 = scripts[3]
    print("=" * 80)
    print("Script 块 #4 内容")
    print("=" * 80)
    print(script4)
    print()
    print("=" * 80)
    print("逐行分析")
    print("=" * 80)
    
    lines = script4.split('\n')
    quote_count_single = 0
    quote_count_double = 0
    
    for i, line in enumerate(lines):
        if "'" in line or '"' in line:
            single_in_line = line.count("'")
            double_in_line = line.count('"')
            quote_count_single += single_in_line
            quote_count_double += double_in_line
            
            if single_in_line % 2 != 0 or double_in_line % 2 != 0:
                print(f"第 {i+1} 行 (单: {single_in_line}, 双: {double_in_line}): {line[:100]}")
