import pandas as pd

# Test what happens with `not df` for a DataFrame
df = pd.DataFrame({'a': [1, 2, 3]})

print("Testing 'not df' for DataFrame:")
try:
    if not df:
        print("  DataFrame is falsy")
    else:
        print("  DataFrame is truthy")
except ValueError as ve:
    print(f"  ERROR: {ve}")

print("\nTesting len(df):")
try:
    length = len(df)
    print(f"  len(df) = {length}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTesting 'if not df.empty':")
try:
    if not df.empty:
        print("  DataFrame is not empty")
except Exception as e:
    print(f"  ERROR: {e}")
