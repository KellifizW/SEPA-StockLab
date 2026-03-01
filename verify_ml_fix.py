#!/usr/bin/env python3
"""
最终验证：ML 分析页面是否修复成功
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("""
╔════════════════════════════════════════════════════════════════════╗
║              ✅ ML 分析页面修复验证                                ║
╚════════════════════════════════════════════════════════════════════╝
""")

from app import app

client = app.test_client()

# 测试所有相关路由
routes = [
    ('/', '首页'),
    ('/ml/analyze', 'ML 分析页面'),
    ('/ml/diagnostic', '诊断工具'),
    ('/api/ml/analyze', 'ML 分析 API'),
]

print("【检查 Flask 服务器状态】\n")
all_ok = True
for route, name in routes:
    try:
        resp = client.get(route) if 'api' not in route else client.post(route, json={'ticker': 'TEST'})
        status = '✅' if resp.status_code in [200, 400] else '❌'
        print(f"{status} {name:20} {route:30} → {resp.status_code}")
        if resp.status_code >= 400:
            all_ok = False
    except Exception as e:
        print(f"❌ {name:20} {route:30} → 错误: {str(e)[:50]}")
        all_ok = False

# 检查 ml_analyze.html 内容
print("\n【检查 ML 分析页面内容】\n")
resp = client.get('/ml/analyze')
html = resp.data.decode('utf-8')

checks = [
    ('HTML 大小 > 50KB', len(html) > 50000),
    ('包含导航栏', '<nav' in html),
    ('包含搜索框', 'id="tickerInput"' in html),
    ('包含分析按钮', 'onclick="analyzeStock()"' in html),
    ('包含空状态', 'id="emptyState"' in html),
    ('包含改进的 Fallback CSS', '.container-fluid {' in html and 'display: block !important' in html),
    ('包含表单元素样式', 'input, textarea, select, button' in html),
    ('包含 Bootstrap JS', 'bootstrap.bundle.min.js' in html),
    ('包含 LightweightCharts', 'lightweight-charts' in html),
    ('包含诊断日志', '[ML Analyze] Page loaded:' in html),
]

for check_name, result in checks:
    status = '✅' if result else '❌'
    print(f"{status} {check_name}")

# 总结
print("\n" + "═" * 68)
print("【修复总结】\n")

if all_ok and all(result for _, result in checks):
    print("🎉 所有检查都通过了！")
    print("\n现在应该能看到：")
    print("  ✓ 导航栏")
    print("  ✓ 搜索框")
    print("  ✓ 「分析」按钮")
    print("  ✓ 提示文字")
    print("\n下一步：")
    print("  1. 重启 Flask 服务器: python run_app.py")
    print("  2. 打开页面: http://127.0.0.1:5000/ml/analyze")
    print("  3. 尝试输入股票代号（如 AAPL）并分析")
else:
    print("⚠️ 有些检查没有通过")
    print("\n建议：")
    print("  1. 检查文件是否正确保存")
    print("  2. 重启 Flask 服务器")
    print("  3. 清除浏览器缓存")

print("\n" + "═" * 68)
print("修复详情见: ML_ANALYZE_FIX_GUIDE.md\n")
