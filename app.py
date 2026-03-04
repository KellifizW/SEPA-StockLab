"""
app.py  —  Minervini SEPA Web Interface (slim hub)
════════════════════════════════════════════════════
Launch:   python app.py       or   python start_web.py
Browser:  http://localhost:5000

All route handlers live in the ``routes/`` package.
This file only:
  1. Creates the Flask app
  2. Configures logging
  3. Pre-imports heavy modules (IBKR) in the main thread
  4. Registers all blueprints
  5. Provides the ``if __name__ == '__main__'`` entry point
"""

import sys
import os
import logging
import threading
import signal
import time
import webbrowser
import io

# Force UTF-8 stdout/stderr on Windows (avoids cp950 encode errors for ✓ ✗ → etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from pathlib import Path
from flask import Flask

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trader_config as C

# ── Logging ──────────────────────────────────────────────────────────────────
_LOG_DIR = ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.DEBUG)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[_stream_handler],
)
# Suppress high-volume DEBUG noise from third-party libraries
for _name in (
    "werkzeug", "urllib3", "urllib3.connectionpool",
    "yfinance", "yfinance.base", "yfinance.utils",
    "peewee", "multitasking", "charset_normalizer",
    "numba", "numba.core",
):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "minervini-sepa-2026"

# ── Pre-import ibkr_client in main thread ────────────────────────────────────
# ib_insync → eventkit tries to get event loop at import time, so we must
# import it once in the main thread during startup.
if C.IBKR_ENABLED:
    try:
        from modules import ibkr_client  # noqa: F401
        logger.info("✓ IBKR module pre-loaded in main thread")
    except Exception as exc:
        logger.warning("Failed to pre-load IBKR module: %s", exc)

# ── Register all route blueprints ────────────────────────────────────────────
from routes import register_blueprints  # noqa: E402
register_blueprints(app)

# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import routes.helpers as H

    # Ensure data directories exist
    (ROOT / C.DATA_DIR / "price_cache").mkdir(parents=True, exist_ok=True)
    (ROOT / C.REPORTS_DIR).mkdir(parents=True, exist_ok=True)

    print("\n  ┌──────────────────────────────────────────┐")
    print("  │  Minervini SEPA  —  Web Interface         │")
    print("  │  http://localhost:5000                    │")
    print("  │  Press Ctrl+C to stop                    │")
    print("  └──────────────────────────────────────────┘\n")
    sys.stdout.flush()

    # Global state for clean shutdown
    _shutdown_event = threading.Event()
    _flask_thread = None

    def _cleanup_and_exit(code=0):
        """Cleanup resources and exit immediately."""
        print("\n\n  ⏹  關閉伺服器... Shutting down server...", flush=True)

        # Stop Telegram polling if running
        try:
            if H._tg_thread and H._tg_thread.is_alive():
                from modules.telegram_bot import stop_polling
                stop_polling()
                H._tg_thread.join(timeout=1)
        except Exception:
            pass

        # Signal all jobs to cancel
        try:
            with H._jobs_lock:
                for jid in list(H._cancel_events.keys()):
                    H._cancel_events[jid].set()
        except Exception:
            pass

        _shutdown_event.set()
        time.sleep(0.1)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)

    def _signal_handler(signum, frame):
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n  📡 收到信號 Received {signal_name}", flush=True)
        _cleanup_and_exit(0)

    # Disable buffering for immediate output
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

    # Register signal handlers BEFORE starting the server
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if hasattr(signal, "set_wakeup_fd"):
            signal.set_wakeup_fd(sys.stderr.fileno())
    except (AttributeError, ValueError):
        pass

    # Auto-open browser
    def _open_browser():
        try:
            time.sleep(2)
            webbrowser.open("http://localhost:5000")
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    # Run Flask in a daemon thread
    def _run_flask():
        try:
            app.run(
                debug=False,
                host="127.0.0.1",
                port=5000,
                threaded=True,
                use_reloader=False,
                use_debugger=False,
            )
        except Exception as exc:
            print(f"❌ Flask server error: {exc}", flush=True)

    _flask_thread = threading.Thread(target=_run_flask, daemon=True)
    _flask_thread.start()

    print(" * Serving Flask app 'app'")
    print(" * Debug mode: off")

    # Start Telegram Bot Polling (if enabled)
    if C.TG_ENABLED:
        try:
            from modules.telegram_bot import start_polling
            H._tg_thread = threading.Thread(target=start_polling, daemon=True)
            H._tg_thread.start()
            H._tg_enabled = True
            print(" * Telegram Bot Polling: ON")
        except Exception as exc:
            print(f" ⚠️  Telegram Bot 啟動失敗: {exc}")
            H._tg_enabled = False

    # Main thread: keep alive to receive signals
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _cleanup_and_exit(0)
    except Exception as exc:
        print(f"\n\n  ❌ 錯誤 Error: {exc}\n", flush=True)
        _cleanup_and_exit(1)
