import sys
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
import trader_config as C
from modules.data_pipeline import batch_download_and_enrich

# Test with a small sample
tickers = ['AAPL', 'MSFT']
try:
    result = batch_download_and_enrich(tickers, period='2y')
    print(f'Return type: {type(result)}')
    print(f'Keys: {list(result.keys())}')
    if result:
        first_key = next(iter(result))
        first_val = result[first_key]
        print(f'First value type: {type(first_val)}')
        if hasattr(first_val, 'shape'):
            print(f'First value shape: {first_val.shape}')
        print('SUCCESS')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
