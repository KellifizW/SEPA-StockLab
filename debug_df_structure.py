#!/usr/bin/env python
"""Debug script to check yfinance DataFrame structure."""

import pandas as pd
import yfinance as yf

# Fetch PLTR data
ticker = "PLTR"
print(f"Fetching {ticker} data...")
df = yf.download(ticker, period="2y", progress=False)

print(f"\nDataFrame info:")
print(f"  Shape: {df.shape}")
print(f"  Columns type: {type(df.columns)}")
print(f"  Columns: {df.columns.tolist()}")
print(f"\nFrist few rows:")
print(df.head())

print(f"\nAccessing 'Close' column:")
close_col = df["Close"]
print(f"  Type: {type(close_col)}")
print(f"  Shape: {close_col.shape}")
print(f"  Head:\n{close_col.head()}")

print(f"\nAccessing 'High' column:")
high_col = df["High"]
print(f"  Type: {type(high_col)}")
print(f"  Shape: {high_col.shape}")
print(f"  Head:\n{high_col.head()}")
