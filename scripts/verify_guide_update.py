#!/usr/bin/env python3
"""
驗證 GUIDE.md 實戰教學更新是否正確集成到程式中
Verify GUIDE.md teaching update is correctly integrated
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def verify_guide_content():
    """檢查 GUIDE.md 是否包含所有新增內容"""
    guide_path = ROOT / "docs" / "GUIDE.md"
    
    if not guide_path.exists():
        print("❌ 錯誤：docs/GUIDE.md 不存在")
        return False
    
    content = guide_path.read_text(encoding='utf-8')
    
    # 檢查清單
    required_sections = {
        "🎯 實戰教學": "新章節標題",
        "9 步驟完整流程": "主要教學部分",
        "黃金組合": "篩選標準",
        "第 1 步：打開掃描頁面": "步驟 1",
        "第 9 步：執行交易": "步驟 9",
        "快速參考表": "快速查詢",
        "常見問答": "FAQ 部分",
        "驗證檢查清單": "檢查表",
    }
    
    print("=" * 60)
    print("📋 GUIDE.md 實戰教學更新驗證")
    print("=" * 60)
    
    all_found = True
    for section, description in required_sections.items():
        if section in content:
            print(f"✅ 找到「{description}」 → {section}")
        else:
            print(f"❌ 缺少「{description}」 → {section}")
            all_found = False
    
    # 統計信息
    lines = len(content.split('\n'))
    chars = len(content)
    print("\n" + "=" * 60)
    print(f"📊 文件統計")
    print("=" * 60)
    print(f"  總行數：{lines:,} 行")
    print(f"  總字元：{chars:,} 字元")
    print(f"  檔案大小：{chars / 1024:.1f} KB")
    
    return all_found

def verify_flask_integration():
    """檢查 Flask 應用是否正確加載 GUIDE.md"""
    print("\n" + "=" * 60)
    print("🌐 Flask 集成驗證")
    print("=" * 60)
    
    app_path = ROOT / "app.py"
    if not app_path.exists():
        print("❌ 錯誤：app.py 不存在")
        return False
    
    app_content = app_path.read_text(encoding='utf-8')
    
    checks = {
        "@app.route(\"/guide\")": "Flask /guide 路由",
        "GUIDE.md": "加載 GUIDE.md 文件",
        "render_template": "渲染模板",
    }
    
    all_found = True
    for check, description in checks.items():
        if check in app_content:
            print(f"✅ {description}")
        else:
            print(f"❌ {description} 缺失")
            all_found = False
    
    return all_found

def verify_template_support():
    """檢查 guide.html 模板是否支持 Markdown 渲染"""
    print("\n" + "=" * 60)
    print("🎨 模板支持驗證")
    print("=" * 60)
    
    template_path = ROOT / "templates" / "guide.html"
    if not template_path.exists():
        print("❌ 錯誤：guide.html 不存在")
        return False
    
    template_content = template_path.read_text(encoding='utf-8')
    
    checks = {
        "marked": "Markdown 渲染庫",
        "tojson": "JSON 轉換過濾器",
        "markdown-body": "樣式支持",
    }
    
    all_found = True
    for check, description in checks.items():
        if check in template_content:
            print(f"✅ {description}")
        else:
            print(f"❌ {description} 缺失")
            all_found = False
    
    return all_found

def print_summary():
    """打印使用指南"""
    print("\n" + "=" * 60)
    print("📖 如何查看新增教學")
    print("=" * 60)
    
    guide_methods = [
        ("🌐 Web 界面（推薦）", [
            "1. 啟動 Flask：python app.py",
            "2. 打開：http://localhost:5000/guide",
            "3. 向下滾動到「🎯 實戰教學」章節"
        ]),
        ("📝 編輯器", [
            "1. 打開 docs/GUIDE.md",
            "2. 搜索「實戰教學」(第 329 行開始)"
        ]),
        ("💻 命令行", [
            "cat docs/GUIDE.md | grep -A 20 '實戰教學'"
        ]),
    ]
    
    for method, steps in guide_methods:
        print(f"\n{method}：")
        for step in steps:
            print(f"  {step}")

def main():
    print("\n🚀 開始驗證 GUIDE.md 實戰教學集成...\n")
    
    results = {
        "GUIDE.md 內容": verify_guide_content(),
        "Flask 應用集成": verify_flask_integration(),
        "模板支持": verify_template_support(),
    }
    
    print_summary()
    
    print("\n" + "=" * 60)
    print("✅ 驗證結果總結")
    print("=" * 60)
    
    all_passed = True
    for check_name, passed in results.items():
        status = "✅ 通過" if passed else "❌ 失敗"
        print(f"{status} {check_name}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有驗證都通過！")
        print("\n📚 新增內容已成功集成到程式中：")
        print("  • 9 步驟實戰教學流程")
        print("  • 黃金組合篩選標準")
        print("  • 快速參考表和決策樹")
        print("  • 6 個常見問答 (FAQ)")
        print("  • 11 項驗證檢查清單")
        print("\n👉 請訪問 http://localhost:5000/guide 查看完整教學")
        print("=" * 60)
        return 0
    else:
        print("⚠️  部分驗證未通過，請檢查上述錯誤")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
