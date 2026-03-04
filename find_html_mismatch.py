with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find lines with closing angle brackets but let's look for suspicious patterns
mismatched_lines = []

for i, line in enumerate(lines, 1):
    # Count brackets in this line
    open_count = line.count('<')
    close_count = line.count('>')
    
    if close_count > open_count:
        mismatched_lines.append((i, close_count - open_count, line.rstrip()[:150]))

print(f"Lines with more closing brackets than opening brackets:")
print(f"Total: {len(mismatched_lines)}")

for line_num, diff, content in mismatched_lines[:20]:
    print(f"\nLine {line_num}: {diff} more '>' than '<'")
    print(f"  {content}")
