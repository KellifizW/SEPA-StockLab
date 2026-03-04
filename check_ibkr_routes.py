#!/usr/bin/env python
import os
os.environ['FLASK_DEBUG'] = '0'

# Minimal Flask app to test route registration
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app

print("Checking if IBKR routes are registered in Flask app...")
print("\nRegistered routes:")
for rule in app.app.url_map.iter_rules():
    if 'ibkr' in rule.rule:
        print(f"  {rule.rule} → {rule.endpoint} [{','.join(rule.methods)}]")

print("\nAll IBKR routes found:", len([r for r in app.app.url_map.iter_rules() if 'ibkr' in r.rule]))
