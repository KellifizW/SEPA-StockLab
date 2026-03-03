#!/usr/bin/env python
"""
SEPA-StockLab Web Interface Launcher
Start the Flask web server for SEPA stock screening tool.
"""

import sys
import os
import time
import webbrowser
import signal
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    print("\n" + "="*60)
    print(" SEPA-StockLab Web Interface")
    print("="*60 + "\n")
    
    # Global shutdown events
    _shutdown_event = threading.Event()
    
    def _cleanup_and_exit(code=0):
        """Cleanup and exit immediately."""
        print("\n\n  ⏹  關閉伺服器... Shutting down server...")
        _shutdown_event.set()
        time.sleep(0.1)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    
    def _signal_handler(signum, frame):
        """Handle Ctrl+C (SIGINT) and SIGTERM."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n  📡 收到信號 Received {signal_name}")
        _cleanup_and_exit(0)
    
    # Register signal handlers BEFORE importing/starting app
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    
    try:
        print("📦 Importing Flask app...")
        import app
        print("   ✅ App loaded\n")
        
        print("🌐 Starting server...")
        print("   http://localhost:5000\n")
        print("   Press Ctrl+C to stop\n")
        
        # Open browser after a short delay
        def _open_browser():
            time.sleep(1.5)
            try:
                webbrowser.open('http://localhost:5000')
                print("📱 Browser opened at http://localhost:5000")
            except:
                pass
        
        browser_thread = threading.Thread(target=_open_browser, daemon=True)
        browser_thread.start()
        
        # Run Flask in daemon thread
        def _run_flask():
            try:
                app.app.run(
                    host='127.0.0.1',
                    port=5000,
                    debug=False,
                    use_reloader=False,
                    threaded=True,
                    use_debugger=False
                )
            except Exception as e:
                print(f"❌ Flask error: {e}")
        
        flask_thread = threading.Thread(target=_run_flask, daemon=True)
        flask_thread.start()
        
        # Main thread loop - keeps main thread alive to receive signals
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        _cleanup_and_exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        _cleanup_and_exit(1)

if __name__ == '__main__':
    main()
