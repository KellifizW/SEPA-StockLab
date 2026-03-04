with open('dashboard.html', 'r', encoding='utf-8-sig') as f:
    lines = f.readlines()

print(f"Total lines in file: {len(lines)}")

# Find ALL script tags
script_starts = []
script_ends = []

for i, line in enumerate(lines):
    if '<script>' in line:
        script_starts.append(i + 1)  # 1-indexed
    if '</script>' in line:
        script_ends.append(i + 1)  # 1-indexed

print(f"Script start lines: {script_starts}")
print(f"Script end lines: {script_ends}")

if script_starts and script_ends:
    # Get the first script block
    script_start = script_starts[0]
    script_end = script_ends[0]
    
    print(f"\nAnalyzing script from line {script_start} to {script_end}")
    
    # Get script lines (convert back to 0-indexed)
    script_lines = lines[script_start:script_end]
    
    # Count braces in the script
    script_content = ''.join(script_lines)
    open_braces = script_content.count('{')
    close_braces = script_content.count('}')
    
    print(f"\nOpen braces in script: {open_braces}")
    print(f"Close braces in script: {close_braces}")
    print(f"Match: {open_braces == close_braces}")
    
    # Find extra closing brace
    if close_braces > open_braces:
        print(f"\nERROR: Extra {close_braces - open_braces} closing braces found")
        # Find the line with extra brace
        brace_count = 0
        for i, line in enumerate(script_lines):
            for char in line:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count < 0:
                        line_num = script_start + i
                        print(f"Extra brace found at line {line_num}: {line.strip()}")
                        brace_count = 0  # reset
    
    print(f"\nAll closing braces in script:")
    for i, line in enumerate(script_lines):
        if '}' in line:
            line_num = script_start + i
            print(f"  Line {line_num}: {line.strip()[:100]}")
