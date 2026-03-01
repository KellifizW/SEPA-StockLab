#!/usr/bin/env python3
"""
Quick check for the CSS width fix
"""
with open('templates/ml_analyze.html', 'r', encoding='utf-8') as f:
    content = f.read()

checks = {
    '✓ container.style.width = containerWidth + "px"': 'container.style.width = containerWidth + \'px\'' in content,
    '✓ container.style.display = "block"': 'container.style.display = \'block\'' in content,
    '✓ container.style.height = "450px"': 'container.style.height = \'450px\'' in content,
    '✓ CSS #watchPanel has display:block': '#watchPanel { display:block !important' in content,
    '✓ CSS #intradayChartContainer has display:block': '#intradayChartContainer { background' in content and 'display:block' in content,
}

print("=" * 65)
print("CSS WIDTH FIX VERIFICATION")
print("=" * 65)

all_ok = True
for check, result in checks.items():
    status = "✅" if result else "❌"
    all_ok = all_ok and result
    print(f"{check:<55} {status}")

print("=" * 65)

if all_ok:
    print("✅ ALL FIXES APPLIED - Ready to test!\n")
    print("QUICK TEST:")
    print("1. Refresh page (Ctrl+R)")
    print("2. Go to: http://127.0.0.1:5000/ml/analyze?ticker=AEM")
    print("3. Open DevTools (F12 → Console)")
    print("4. Click '📡 盯盤模式' button")
    print("5. Look for this in Console:")
    print('   ✅ Container width set to: 1091px (or similar)')
    print("\n6. Chart should NOW BE VISIBLE with K-lines!")
else:
    print("❌ Some fixes missing")

print("=" * 65)
