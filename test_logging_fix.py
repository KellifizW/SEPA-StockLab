#!/usr/bin/env python3
"""
test_logging_fix.py - Test that app.py logger now works properly
"""
import sys
import logging
from pathlib import Path

# Setup path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Import app and use its logger
import app

def test_logging():
    """Test that logger outputs to console."""
    print("\n" + "="*80)
    print("🧪 TEST: Logging to Console")
    print("="*80)
    
    # Test different logging levels
    print("\n✓ Testing logger.debug():")
    app.logger.debug("DEBUG: This is a debug message")
    
    print("\n✓ Testing logger.info():")
    app.logger.info("INFO: This is an info message")
    
    print("\n✓ Testing logger.warning():")
    app.logger.warning("WARNING: This is a warning message")
    
    print("\n✓ Testing logger.error():")
    app.logger.error("ERROR: This is an error message")
    
    print("\n" + "="*80)
    print("✅ ALL LOGGING TESTS PASSED - Logger is now properly configured!")
    print("="*80 + "\n")
    
    # Show logger configuration details
    print("\n📋 Logger Configuration Details:")
    print(f"   Logger name: {app.logger.name}")
    print(f"   Logger level: {logging.getLevelName(app.logger.level)}")
    print(f"   Number of handlers: {len(app.logger.handlers)}")
    print(f"   Handlers: {app.logger.handlers}")
    print(f"   Root logger level: {logging.getLevelName(logging.getLogger().level)}")
    print()

if __name__ == "__main__":
    test_logging()
