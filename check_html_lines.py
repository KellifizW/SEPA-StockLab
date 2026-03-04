with open('dashboard.html', 'r', encoding='utf-8-sig') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
print("\nLines 1675-1695:")
for i in range(min(1674, len(lines)), min(1695, len(lines))):
    print(f"{i+1}: {lines[i]}", end='')

# Also find all occurrences of toggleCurrency
print("\n\nAll lines with toggl Currency:")
for i, line in enumerate(lines, 1):
    if 'toggleCurrency' in line:
        print(f"{i}: {line.strip()[:120]}")
