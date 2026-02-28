#!/usr/bin/env python3
"""
QM Chart Fix Verification Script
验证所有 QM 分析页面的图表修复是否正确部署
"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C

def check_template_chart():
    """检查模板中的 loadChart 函数"""
    template_path = ROOT / 'templates' / 'qm_analyze.html'
    
    if not template_path.exists():
        return False, "模板文件不存在"
    
    content = template_path.read_text(encoding='utf-8')
    
    checks = [
        ('_qmChart 全局变量', 'let _qmChart = null' in content),
        ('_destroyQmChart 函数', 'function _destroyQmChart()' in content),
        ('LightweightCharts 创建', '_qmChart = LWC.createChart(container' in content),
        ('成交量直方图', 'addHistogramSeries' in content and 'priceScaleId: \'vol\'' in content),
        ('SMA50/150/200 线', 'sma50' in content and 'sma150' in content and 'sma200' in content),
        ('Bollinger Bands', ('data.bbu' in content or "'bbu'" in content) and ('data.bbl' in content or "'bbl'" in content) and ('data.bbm' in content or "'bbm'" in content)),
        ('价格线创建', 'createPriceLine' in content),
        ('ResizeObserver', 'ResizeObserver' in content),
        ('数据属性传输', 'data-qm-close' in content and 'data-qm-day1-stop' in content),
        ('错误处理', 'Chart unavailable' in content or '圖表載入失敗' in content),
    ]
    
    all_pass = all(check[1] for check in checks)
    return all_pass, checks

def check_analyzer_backend():
    """检查后端 QM 分析器"""
    analyzer_path = ROOT / 'modules' / 'qm_analyzer.py'
    
    if not analyzer_path.exists():
        return False, "分析器文件不存在"
    
    content = analyzer_path.read_text(encoding='utf-8')
    
    checks = [
        ('mom_1m 字段', 'result["mom_1m"] = mom.get("1m")' in content or "mom_1m" in content),
        ('mom_3m 字段', 'result["mom_3m"] = mom.get("3m")' in content or "mom_3m" in content),
        ('mom_6m 字段', 'result["mom_6m"] = mom.get("6m")' in content or "mom_6m" in content),
        ('day1_stop 字段', 'day1_stop' in content),
        ('day3plus_stop 字段', 'day3plus_stop' in content),
        ('profit_target_px 字段', 'profit_target_px' in content),
    ]
    
    all_pass = all(check[1] for check in checks)
    return all_pass, checks

def check_flask_chart_endpoint():
    """检查 Flask 图表端点"""
    app_path = ROOT / 'app.py'
    
    if not app_path.exists():
        return False, "Flask app 文件不存在"
    
    content = app_path.read_text(encoding='utf-8')
    
    checks = [
        ('chart/enriched 端点', '/api/chart/enriched' in content),
        ('GET 方法', '@app.route' in content),
    ]
    
    all_pass = all(check[1] for check in checks)
    return all_pass, checks

def main():
    print("\n" + "="*70)
    print("QM 图表修复验证 🔍")
    print("="*70 + "\n")
    
    all_results = []
    
    # 1. 检查模板
    print("📋 检查模板 (templates/qm_analyze.html)...")
    template_ok, template_checks = check_template_chart()
    for check_name, result in template_checks:
        status = "✅" if result else "❌"
        print(f"  {status} {check_name}")
    all_results.append(("模板", template_ok))
    print()
    
    # 2. 检查后端分析器
    print("📋 检查后端分析器 (modules/qm_analyzer.py)...")
    analyzer_ok, analyzer_checks = check_analyzer_backend()
    for check_name, result in analyzer_checks:
        status = "✅" if result else "❌"
        print(f"  {status} {check_name}")
    all_results.append(("分析器", analyzer_ok))
    print()
    
    # 3. 检查 Flask 端点
    print("📋 检查 Flask 端点 (app.py)...")
    flask_ok, flask_checks = check_flask_chart_endpoint()
    for check_name, result in flask_checks:
        status = "✅" if result else "❌"
        print(f"  {status} {check_name}")
    all_results.append(("Flask", flask_ok))
    print()
    
    # 总结
    print("="*70)
    if all(ok for _, ok in all_results):
        print("✅ 所有修复都已正确部署！")
        print("\n🚀 立即测试:")
        print("   1. python -B app.py")
        print("   2. 打开浏览器: http://localhost:5000/qm/analyze?ticker=ASTI")
        print("   3. 验证图表是否显示")
        print("="*70 + "\n")
        return 0
    else:
        print("❌ 某些检查失败:")
        for component, ok in all_results:
            status = "✅" if ok else "❌"
            print(f"  {status} {component}")
        print("="*70 + "\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
