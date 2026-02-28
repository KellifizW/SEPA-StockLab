#!/usr/bin/env python3
"""
验证 QM 分析页面的语法错误是否已修复
"""

import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

def check_duplicate_variables():
    """检查模板中是否有重复的变量声明"""
    template_path = ROOT / 'templates' / 'qm_analyze.html'
    
    print(f"  查找路径: {template_path}")
    print(f"  路径存在: {template_path.exists()}")
    
    if not template_path.exists():
        print("❌ 模板文件不存在")
        return False
    
    content = template_path.read_text(encoding='utf-8')
    
    # 查找所有的变量声明
    qm_chart_matches = re.findall(r'let\s+_qmChart\s*=', content)
    qm_data_matches = re.findall(r'let\s+_qmChartData\s*=', content)
    
    print("检查重复声明...")
    print(f"  _qmChart 声明数: {len(qm_chart_matches)}")
    print(f"  _qmChartData 声明数: {len(qm_data_matches)}")
    
    if len(qm_chart_matches) == 1 and len(qm_data_matches) == 1:
        print("\n✅ 没有重复的变量声明")
        return True
    else:
        print("\n❌ 发现重复的变量声明！")
        return False

def main():
    print("="*60)
    print("QM 分析页面语法检查 🔍")
    print("="*60 + "\n")
    
    if check_duplicate_variables():
        print("\n✅ 问题已修复！")
        print("\n现在可以安全地在浏览器中使用 QM 分析页面")
        print("   http://localhost:5000/qm/analyze?ticker=ASTI")
        return 0
    else:
        print("\n❌ 仍有语法错误需要修复")
        return 1

if __name__ == '__main__':
    sys.exit(main())
