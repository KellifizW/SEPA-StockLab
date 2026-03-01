import sys
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
import trader_config as C
from modules.combined_scanner import run_combined_scan
import traceback

try:
    # Run a quick combined scan with just a few tickers
    sepa_result, qm_result = run_combined_scan(verbose=True, stage1_source='finviz')
    print("Combined scan completed successfully!")
    print(f"SEPA passed count: {len(sepa_result.get('passed', []))}")
    print(f"QM passed count: {len(qm_result.get('passed', []))}")
except Exception as e:
    print(f"Error during combined scan: {type(e).__name__}: {e}")
    traceback.print_exc()
