#!/usr/bin/env python
"""Start Flask app and log output to file."""

import sys
import logging
from pathlib import Path

# Set up logging
log_file = Path("app_startup.log")
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)
logger.info("Starting Flask app...")

try:
    import app  
    logger.info("App module imported successfully")
    
    # Start the Flask app
    logger.info("Starting Flask server on http://localhost:5000")
    app.app.run(debug=False, host='0.0.0.0', port=5000)
    
except Exception as e:
    logger.exception(f"Failed to start app: {e}")
    sys.exit(1)
