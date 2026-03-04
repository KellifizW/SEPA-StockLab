with open('templates/base.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the last </script> tag
for i in range(len(lines)-1, -1, -1):
    if '</script>' in lines[i]:
        print(f'Line {i+1}: {lines[i].rstrip()}')
        # Show context
        print(f'\nContext (lines {max(1, i-2)} to {min(len(lines), i+5)}):')
        for j in range(max(0, i-2), min(len(lines), i+5)):
            print(f'{j+1}: {lines[j].rstrip()}')
        break

# Also find where <script> without src starts
print('\n\n<script> without src:')
for i in range(len(lines)):
    if '<script>' in lines[i]:
        print(f'Line {i+1}: {lines[i].rstrip()}')
