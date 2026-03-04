import re

with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract script content
script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
if script_match:
    script = script_match.group(1)
    
    print("Script extracted")
    print(f"Script size: {len(script)} characters")
    print(f"Script lines: {len(script.splitlines())}")
    
    # Look for toggleCurrency definition
    if 'async function toggleCurrency' in script:
        idx = script.find('async function toggleCurrency')
        print(f"\n✓ Found toggleCurrency at position {idx}")
        # Show 3 lines before and after
        lines = script.splitlines()
        for i, line in enumerate(lines):
            if 'async function toggleCurrency' in line:
                start = max(0, i-2)
                end = min(len(lines), i+10)
                print("Context around toggleCurrency:")
                for j in range(start, end):
                    marker = ">>>" if j == i else "   "
                    print(f"  {marker} {j}: {lines[j][:100]}")
                break
    else:
        print("\n✗ ERROR: toggleCurrency function not found in script!")
        
    # Look for editAccountSize definition
    if 'async function editAccountSize' in script:
        idx = script.find('async function editAccountSize')
        print(f"\n✓ Found editAccountSize at position {idx}")
    else:
        print("\n✗ ERROR: editAccountSize function not found in script!")

    # Check if there's a syntax error in the script by looking for incomplete statements
    print("\n" + "="*60)
    print("Checking for common syntax issues...")
    print("="*60)
    
    # Count open/close parens
    open_parens = script.count('(')
    close_parens = script.count(')')
    print(f"Open parens: {open_parens}")
    print(f"Close parens: {close_parens}")
    if open_parens != close_parens:
        print(f"✗ MISMATCH: {abs(open_parens - close_parens)} unbalanced parens")
    else:
        print(f"✓ Parens are balanced")
