#!/usr/bin/env python3
"""Start Flask with verbose logging to stdout and file."""

import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set up logging BEFORE importing app
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "flask_debug.log", encoding="utf-8")
    ]
)

# Now import and run
from app import app

print("\n" + "="*80)
print("Starting Flask with DEBUG logging")
print("="*80 + "\n")

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
