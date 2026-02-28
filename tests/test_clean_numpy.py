#!/usr/bin/env python
"""Test _clean function with numpy values."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import _clean
from modules.stock_analyzer import analyze

result = analyze('MU', account_size=100000, print_report=False)

# 測試 _clean 函數
scored = result.get('scored_pillars', {})
print("=== Before _clean ===")
print(f"ticker: {scored.get('ticker')!r}")
print(f"vcp['is_valid_vcp']: {scored.get('vcp', {}).get('is_valid_vcp')!r} (type: {type(scored.get('vcp', {}).get('is_valid_vcp')).__name__})")
print(f"vcp['base_depth_pct']: {scored.get('vcp', {}).get('base_depth_pct')!r} (type: {type(scored.get('vcp', {}).get('base_depth_pct')).__name__})")

cleaned_scored = _clean(scored)
print("\n=== After _clean ===")
print(f"ticker: {cleaned_scored.get('ticker')!r}")
print(f"vcp type: {type(cleaned_scored.get('vcp'))}")
if isinstance(cleaned_scored.get('vcp'), dict):
    print(f"vcp['is_valid_vcp']: {cleaned_scored['vcp'].get('is_valid_vcp')!r}")
    print(f"vcp['base_depth_pct']: {cleaned_scored['vcp'].get('base_depth_pct')!r}")

# Now try jsonify
from flask import Flask, jsonify
app = Flask(__name__)
with app.app_context():
    response = jsonify({"scored_pillars": cleaned_scored})
    print("\n=== jsonify response ===")
    data = response.get_json()
    print(f"ticker: {data['scored_pillars'].get('ticker')!r}")
    print(f"vcp type: {type(data['scored_pillars'].get('vcp'))}")
    if isinstance(data['scored_pillars'].get('vcp'), dict):
        print(f"vcp['is_valid_vcp']: {data['scored_pillars']['vcp'].get('is_valid_vcp')!r}")
