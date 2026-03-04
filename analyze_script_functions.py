import re

with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract script block
script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
if script_match:
    script = script_match.group(1)
    
    # Extract function names
    functions = re.findall(r'(async\s+)?function\s+(\w+)\s*\(', script)
    
    print("Functions found in script:")
    for is_async, func_name in functions:
        async_text = "async " if is_async else ""
        print(f"  {async_text}{func_name}()")
    
    # Check for mismatched closing braces
    lines = script.split('\n')
    print(f"\nTotal script lines: {len(lines)}")
    
    # Look for lines that are just closing braces
    print("\nLines with only closing braces:")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == '}':
            print(f"  Line {i}")
    
    # Count parens and braces
    open_paren = script.count('(')
    close_paren = script.count(')')
    open_brace = script.count('{')
    close_brace = script.count('}')
    
    print(f"\nBrace/Paren count:")
    print(f"  Open parens: {open_paren}, Close parens: {close_paren}")
    print(f"  Open braces: {open_brace}, Close braces: {close_brace}")
    print(f"  Parens match: {open_paren == close_paren}")
    print(f"  Braces match: {open_brace == close_brace}")
PYEOF
