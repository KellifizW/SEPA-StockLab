with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()
    
# Search for the route
search_str = '@app.route("/api/ibkr/status"'
if search_str in content:
    print('✓ Found IBKR status route in file')
    # Find line number
    lines = content.split('\n')
    for i, line in enumerate(lines, 1):
        if search_str in line:
            print(f'  Line {i}: {line[:80]}')
else:
    print('✗ IBKR status route NOT found in file')
    
# Also check for function
if 'def api_ibkr_status():' in content:
    print('✓ Found def api_ibkr_status() in file')
else:
    print('✗ def api_ibkr_status() NOT found in file')

# Count IBKR mentions
ibkr_count = content.count('/api/ibkr/')
print(f'\nTotal /api/ibkr/ mentions in file: {ibkr_count}')
