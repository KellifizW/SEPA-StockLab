#!/usr/bin/env python
"""Check positions data format."""

import json
from pathlib import Path

p_file = Path('data/positions.json')
if p_file.exists():
    data = json.loads(p_file.read_text(encoding='utf-8'))
    print('📋 positions.json structure:')
    print(f'  positions count: {len(data.get("positions", {}))}')
    if data.get('positions'):
        first_ticker = list(data['positions'].keys())[0]
        first_pos = data['positions'][first_ticker]
        print(f'  first ticker: {first_ticker}')
        print(f'  first position keys: {list(first_pos.keys())}')
        print(f'  first position data: {first_pos}')
    print(f'  closed positions count: {len(data.get("closed", []))}')
    print(f'  account_high: {data.get("account_high")}')
else:
    print('positions.json not found')
