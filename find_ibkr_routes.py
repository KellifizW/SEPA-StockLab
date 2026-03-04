import sys

# Check indentation and structure around IBKR routes
with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Look for 'def api_ibkr_status'
print('Searching for IBKR route definitions and their indentation...\n')
found = False
for i, line in enumerate(lines):
    if 'def api_ibkr_status' in line:
        print(f'Line {i+1}: FOUND api_ibkr_status')
        found = True
        # Show 5 lines before for context
        for j in range(max(0, i-5), min(len(lines), i+3)):
            indent = len(lines[j]) - len(lines[j].lstrip())
            print(f'  {j+1:4d} (indent={indent:2d}): {lines[j].rstrip()[:100]}')
        break

if not found:
    print('api_ibkr_status NOT FOUND')

print('\n---\n')
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check if IBKR route definitions might be in any conditional blocks
print('Checking for routes in file...')
print(f'Total @app.route decorators: {content.count("@app.route")}')
print(f'Decorators with /api/ibkr: {content.count("@app.route(\"/api/ibkr")}')

# Also check the @app.route decorators at the end of the file (near the entry point)
print('\nLast 20 @app.route occurrences:')
import re
matches = list(re.finditer(r'@app\.route.*?\)', content))
if matches:
    for match in matches[-20:]:
        start = content.rfind('\n', max(0, match.start()-100), match.start()) + 1
        end = content.find('\n', match.end())
        line_num = content[:match.start()].count('\n') + 1
        print(f'  Line {line_num}: {content[match.start():match.end()]}')
