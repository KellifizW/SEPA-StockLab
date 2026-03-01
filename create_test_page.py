#!/usr/bin/env python3
"""Create a minimalist test version without Bootstrap or external CSS"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Create a simple Flask app with a test page
from flask import Flask

test_app = Flask(__name__)

@test_app.route('/test-simple')
def test_simple():
    return """<!DOCTYPE html>
<html>
<head>
    <title>Test Page</title>
    <style>
        body {
            background: #0d1117;
            color: #c9d1d9;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }
        h1 {
            color: #3fb950;
            font-size: 24px;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            background: #161b22;
            padding: 20px;
            border: 1px solid #30363d;
            border-radius: 8px;
        }
        input {
            width: 100%;
            padding: 8px;
            margin: 10px 0;
            background: #0d1117;
            border: 1px solid #30363d;
            color: #c9d1d9;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            background: #238636;
            color: white;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        button:hover {
            background: #2ea043;
        }
        .status {
            margin-top: 20px;
            padding: 10px;
            background: #1a2030;
            border-left: 4px solid #58a6ff;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🧪 ML 個股分析 - Test Page</h1>
        <p>這是一個測試頁面來驗證基本顯示功能</p>
        
        <label for="ticker">輸入股票代號:</label>
        <input type="text" id="ticker" placeholder="e.g. AAPL" value="">
        
        <button onclick="testClick()">測試按鈕 Test Button</button>
        
        <div class="status">
            <h4>頁面狀態:</h4>
            <p>✓ HTML 加載成功</p>
            <p>✓ CSS 應用成功</p>
            <p>✓ JavaScript 執行正常</p>
            <p id="test-output">等待用戶操作...</p>
        </div>
    </div>
    
    <script>
        console.log('✓ JavaScript loaded');
        function testClick() {
            const val = document.getElementById('ticker').value;
            document.getElementById('test-output').textContent = '輸入值: ' + (val || '(空)') + ' | 按鈕點擊成功';
            console.log('Button clicked with value:', val);
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    client = test_app.test_client()
    resp = client.get('/test-simple')
    html = resp.data.decode('utf-8')
    
    # 保存到文件
    test_file = ROOT / 'test_simple.html'
    test_file.write_text(html, encoding='utf-8')
    
    print(f"✓ 簡化測試頁面已保存到: {test_file}")
    print(f"✓ 大小: {len(html)} 字節")
    print(f"\n可以用浏览器打开查看，验证基本样式和 JavaScript 是否正常工作")
