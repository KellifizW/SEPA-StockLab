#!/usr/bin/env python3
"""
Simple Flask startup for testing ml_analyze page
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("=" * 70)
print("SEPA-StockLab ML Analyze Test Server")
print("=" * 70)
print("\n✓ Starting Flask app...")
print("\n📌 URLs to test:")
print("   1. http://127.0.0.1:5000/")
print("   2. http://127.0.0.1:5000/ml/test-minimal  (should show white page)")
print("   3. http://127.0.0.1:5000/ml/analyze       (original page)")
print("   4. http://127.0.0.1:5000/ml/analyze?ticker=AAPL")
print("\n💡 Tips for debugging:")
print("   - Press F12 to open DevTools Console")
print("   - Check Network tab for failed resources")
print("   - Look for red error messages in Console")
print("\n⏹️  Press Ctrl+C to stop server")
print("=" * 70 + "\n")

# Import and run Flask
from app import app

try:
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
except KeyboardInterrupt:
    print("\n\nServer stopped.")
except Exception as e:
    print(f"\n\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
