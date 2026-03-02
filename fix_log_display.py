#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fix the intraday log display in qm_analyze.html"""

with open('templates/qm_analyze.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and replace the problematic section line by line
new_lines = []
skip_next = False
i = 0

while i < len(lines):
    line = lines[i]
    
    if skip_next:
        i += 1
        continue
        
    # Look for the "// Log entry" comment
    if '// Log entry' in line:
        # Replace this section with new code
        new_lines.append('    // Log each signal individually (not summary counts)\n')
        new_lines.append('    const tsStr = new Date(data.refresh_ts * 1000).toLocaleTimeString(\'zh-HK\', { hour:\'2-digit\', minute:\'2-digit\' });\n')
        new_lines.append('    if (data.active_signals && data.active_signals.length) {\n')
        new_lines.append('      data.active_signals.forEach(sig => {\n')
        new_lines.append('        const cleanSig = sig.replace(/^\\s*/, \'\').replace(/^[🟢🔴⚠ℹ]\\s*/, \'\');\n')
        new_lines.append('        appendQmWatchLog(`${tsStr} ${cleanSig}`);\n')
        new_lines.append('      });\n')
        new_lines.append('    } else {\n')
        new_lines.append('      appendQmWatchLog(`${tsStr} 無新訊號 (價格 $${(data.current_price||0).toFixed(2)})`);\n')
        new_lines.append('    }\n')
        
        # Skip the next 2-3 lines (const cnt and appendQmWatchLog with 卄)
        i += 1
        while i < len(lines) and ('const cnt' in lines[i] or ('appendQmWatchLog' in lines[i] and '卄' in lines[i])):
            i += 1
        i -= 1  # Back up one since loop will increment
    else:
        new_lines.append(line)
    i += 1

with open('templates/qm_analyze.html', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('[OK] File updated successfully')



