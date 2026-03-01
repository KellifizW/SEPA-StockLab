"""
詳細檢查 ML 分析頁面的實際 HTML 輸出。
在 Flask 測試客戶端中渲染頁面並分析為什麼顯示为空白。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import app

print("=" * 80)
print("ML 分析頁面 - 詳細 HTML 輸出診斷")
print("=" * 80)

# 使用 Flask 測試客戶端渲染頁面
client = app.test_client()
resp = client.get('/ml/analyze')

if resp.status_code != 200:
    print(f"\n✗ 頁面返回狀態碼: {resp.status_code}")
    print(f"響應: {resp.get_data(as_text=True)[:500]}")
    sys.exit(1)

html = resp.get_data(as_text=True)
print(f"\n✓ 頁面返回 200 OK (大小: {len(html)} 字符)")

# 分析 HTML 結構
print("\n" + "=" * 80)
print("HTML 結構分析")
print("=" * 80)

checks = {
    '<!DOCTYPE html>': '文檔類型',
    '<html': 'HTML 標籤',
    '<head': 'HEAD 標籤',
    '<body': 'BODY 標籤',
    '<div id="app"': 'Vue 應用程序根元素',
    'class="container"': '容器類',
    'class="row"': '行佈局',
    '<form': '表單',
    'id="tickerInput"': '股票代號輸入框',
    'id="resultArea"': '結果區域',
    'id="reviewPanel"': '複盤面板',
    'id="watchPanel"': '盯盤面板',
    'id="intradayChartContainer"': '日內圖表容器',
}

missing = []
found = []

for pattern, description in checks.items():
    if pattern.lower() in html.lower():
        found.append(f"  ✓ {description}")
    else:
        missing.append(f"  ✗ {description} - {pattern}")

print("\n找到的元素:")
for item in found:
    print(item)

if missing:
    print("\n缺失的元素:")
    for item in missing:
        print(item)
else:
    print("\n⭐ 所有關鍵 HTML 元素都存在")

# 檢查 CSS
print("\n" + "=" * 80)
print("CSS 檢查")
print("=" * 80)

css_checks = {
    '<style': '內聯樣式標籤',
    'class="btn"': '按鈕樣式',
    'class="form-control"': '表單控制樣式',
    'class="card"': '卡片樣式',
    'display: none': '隱藏元素樣式',
    'display: flex': 'Flexbox 樣式',
}

for pattern, description in css_checks.items():
    if pattern in html:
        print(f"  ✓ {description}")
    else:
        print(f"  ✗ {description}")

# 檢查 JavaScript
print("\n" + "=" * 80)
print("JavaScript 檢查")
print("=" * 80)

js_checks = {
    'function analyzeStock()': '分析函數',
    'function switchMlMode()': '模式切換函數',
    'function loadIntradayChart()': '加載日內圖表函數',
    'async function': '異步函數',
    'fetch(': 'Fetch API',
    'document.addEventListener': '事件監聽器',
    'getElementById': 'DOM 操作',
}

for pattern, description in js_checks.items():
    if pattern in html:
        print(f"  ✓ {description}")
    else:
        print(f"  ✗ {description}")

# 檢查 Bootstrap 和外部資源
print("\n" + "=" * 80)
print("外部依賴檢查")
print("=" * 80)

libs = {
    'bootstrap': 'Bootstrap 框架',
    'lightweightcharts': 'LightweightCharts 圖表庫',
    'cdn.jsdelivr.net': 'CDN 資源',
}

for lib, description in libs.items():
    if lib.lower() in html.lower():
        print(f"  ✓ {description}")
    else:
        print(f"  ⚠ {description} - 可能缺失")

# 提取 body 內容的前 2000 個字符
print("\n" + "=" * 80)
print("BODY 內容預覽 (前 2000 字符)")
print("=" * 80)

body_start = html.lower().find('<body')
if body_start > 0:
    body_content = html[body_start:body_start+2000]
    # 移除標籤以便閱讀
    import re
    text = re.sub(r'<[^>]+>', '', body_content)
    print(text[:1000])
else:
    print("✗ 未找到 BODY 標籤")

# 檢查初始化代碼
print("\n" + "=" * 80)
print("頁面初始化檢查")
print("=" * 80)

if 'DOMContentLoaded' in html:
    print("  ✓ DOMContentLoaded 事件監聽器存在")
else:
    print("  ✗ DOMContentLoaded 事件監聽器缺失")

if 'analyzeStock()' in html or 'analyzeStock' in html:
    print("  ✓ analyzeStock 函數可用")
else:
    print("  ✗ analyzeStock 函數不可用")

# 保存 HTML 到文件以便檢查
output_file = ROOT / 'ml_analyze_output.html'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\n✓ 完整 HTML 已保存到: {output_file}")
print("  您可以用瀏覽器打開此文件查看實際呈現")

# 檢查空白和可見性
print("\n" + "=" * 80)
print("可見性檢查")
print("=" * 80)

# 查找 resultArea 的狀態
if 'id="resultArea"' in html:
    result_pos = html.find('id="resultArea"')
    # 查找前面 200 個字符
    before_result = html[max(0, result_pos-200):result_pos]
    if 'd-none' in before_result:
        print("  ✓ #resultArea 初始時隱藏 (d-none) - 正確行為")
    else:
        print("  ⚠ #resultArea 初始時可見")

if 'id="emptyState"' in html:
    empty_pos = html.find('id="emptyState"')
    before_empty = html[max(0, empty_pos-200):empty_pos]
    if 'd-none' not in before_empty:
        print("  ✓ #emptyState 初始時顯示 - 正確")
    else:
        print("  ✗ #emptyState 初始時隱藏 (錯誤)")

print("\n" + "=" * 80)
print("診斷總結")
print("=" * 80)
print("""
如果您看到頁面是「空白」:

1. 檢查瀏覽器 DevTools (F12)
   - 視圖 > 開發者工具
   - Console 標籤: 查看紅線錯誤
   - Elements 標籤: 查看 HTML 結構
   - Network 標籤: 查看資源載入狀態

2. 常見原因:
   ✗ JavaScript 錯誤導致頁面初始化失敗
   ✗ CSS 隱藏了所有內容
   ✗ 外部資源 (Bootstrap, CDN) 載入失敗
   ✗ 模板變數未正確渲染

3. 如果看到的是真的「空白頁面」:
   - 檢查瀏覽器是否阻止了腳本執行
   - 檢查 CSP (Content Security Policy) 設置
   - 嘗試其他瀏覽器 (Chrome, Firefox, Edge)
   - 清除瀏覽器快取 (Ctrl+Shift+Del)

HTML 輸出檔案已保存，您可以在瀏覽器中打開檢查。
""")
