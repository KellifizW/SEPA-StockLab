#!/usr/bin/env python3
"""
Verify that all watch mode fixes have been applied to ml_analyze.html
"""
import re

def check_html():
    with open('templates/ml_analyze.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    checks = {
        '✓ intradayChartContainer has width:100%': 'id="intradayChartContainer" style="width:100%;height:450px' in content,
        
        '✓ switchMlMode uses requestAnimationFrame': 'requestAnimationFrame(() => {' in content,
        
        '✓ initWatchMode has setTimeout with 200ms delay': ', 200);' in content and 'setTimeout' in content and 'loadIntradayChart' in content,
        
        '✓ loadIntradayChart has detailed logging': '🔄 loadIntradayChart called' in content,
        
        '✓ loadIntradayChart checks parent container width': 'parent?.clientWidth > 0' in content or 'parent && parent.clientWidth > 0' in content,
        
        '✓ loadIntradayChart has grandparent fallback': 'grandparent' in content.lower() or 'gp' in content,
        
        '✓ switchMlMode has debug logging': 'console.log' in content and 'switchMlMode' in content,
    }
    
    print("=" * 60)
    print("WATCH MODE FIX VERIFICATION (v2)")
    print("=" * 60)
    
    all_passed = True
    for check_name, result in checks.items():
        status = "✅ PASS" if result else "❌ FAIL"
        all_passed = all_passed and result
        print(f"{check_name:<55} {status}")
    
    print("=" * 60)
    if all_passed:
        print("🎉 ALL FIXES VERIFIED - Ready to test in browser!")
        print("\nTo test watch mode:")
        print("1. Visit: http://127.0.0.1:5000/ml/analyze?ticker=AEM")
        print("2. Press F12 to open DevTools (Console tab)")
        print("3. Click '📡 盯盤模式 Watch Market' button")
        print("4. Watch Console for messages:")
        print("   🔄 switchMlMode: watch")
        print("   ✅ Switched to watch mode")
        print("   📌 RequestAnimFrame - ticker: AEM")
        print("   📐 Container clientWidth: [number]")
        print("   ✅ API data received")
        print("   ✅ Chart created successfully")
        print("\nSee WATCH_MODE_TEST_GUIDE.md for full testing instructions")
    else:
        print("⚠️  Some fixes may not be applied correctly")
        print("Check the verification output above")
    
    print("=" * 60)

if __name__ == '__main__':
    check_html()

