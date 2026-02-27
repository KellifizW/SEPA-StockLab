#!/usr/bin/env python
"""
SEPA-StockLab Web Interface Launcher
Start the Flask web server for SEPA stock screening tool.
"""

import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    print("\n" + "="*60)
    print(" SEPA-StockLab Web Interface")
    print("="*60 + "\n")
    
    try:
        print("📦 Importing Flask app...")
        import app
        print("   ✅ App loaded\n")
        
        print("🌐 Starting server...")
        print("   http://localhost:5000\n")
        print("   Press Ctrl+C to stop\n")
        
        # Open browser after a short delay
        def open_browser():
            time.sleep(1.5)
            try:
                webbrowser.open('http://localhost:5000')
                print("📱 Browser opened at http://localhost:5000")
            except:
                pass
        
        import threading
        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()
        
        # Start Flask
        app.app.run(
            host='127.0.0.1',
            port=5000,
            debug=False,
            use_reloader=False
        )
        
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
