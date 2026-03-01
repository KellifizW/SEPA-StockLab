#!/usr/bin/env python3
"""测试页面是否能正确显示_testing only"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

# 使用 test_request_context 来测试模板渲染
with app.test_request_context('/ml/analyze'):
    from flask import render_template, request
    
    # 尝试渲染 ml_analyze 模板
    try:
        html = render_template('ml_analyze.html', prefill=False)
        
        print("✓ 模板渲染成功")
        print(f"✓ 输出大小: {len(html)} 字节")
        
        # 保存到文件
        test_file = ROOT / 'test_render.html'
        test_file.write_text(html, encoding='utf-8')
        print(f"✓ 已保存到: {test_file}")
        
        # 检查关键内容
        checks = {
            'emptyState 存在': '<div id="emptyState"' in html,
            'resultArea 存在': '<div id="resultArea"' in html,
            'h4 标题存在': '<h4' in html and 'ML 個股分析' in html,
            'input 框存在': 'id="tickerInput"' in html,
            'script 块存在': '<script' in html,
            'style 块存在': '<style' in html,
        }
        
        print("\n【内容检查】")
        for name, result in checks.items():
            status = "✓" if result else "✗"
            print(f"  {status} {name}")
            
    except Exception as e:
        print(f"✗ 渲染失败: {e}")
        import traceback
        traceback.print_exc()
