"""
More extensive test of batch_download_and_enrich
"""
import sys
from pathlib import Path
import traceback
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))

from modules.data_pipeline import batch_download_and_enrich

print("Testing batch_download_and_enrich...")
try:
    # Small batch
    tickers = ['AAPL', 'MSFT', 'GOOGL']
    result = batch_download_and_enrich(tickers, period='2y')
    
    print(f"Result type: {type(result)}")
    print(f"Result keys: {list(result.keys())}")
    
    for tkr, df in result.items():
        print(f"\n  {tkr}:")
        print(f"    Type: {type(df)}")
        print(f"    Shape: {df.shape if hasattr(df, 'shape') else 'N/A'}")
        
        
        # Try to trigger the ambiguity error
        print(f"    Testing boolean context...")
        try:
            if df:  # This should fail if df is a DataFrame
                pass
        except ValueError as ve:
            print(f"    ERROR (expected): {ve}")
        
        print(f"    .empty check: {df.empty if hasattr(df, 'empty') else 'N/A'}")
    
    print("\nSUCCESS - No ambiguity errors")
        
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
