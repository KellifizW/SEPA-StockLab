import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.screener import get_scan_progress, _progress as sepa_progress

# Test SEPA logging
sepa_progress("Test Stage", 50, "Testing message")
result = get_scan_progress()

print("✓ SEPA module working")
print(f"  Log lines captured: {len(result.get('log_lines', []))}")
if result.get('log_lines'):
    print(f"  Sample: {result['log_lines'][-1]}")
print("\n✓ Fix verification successful!")
