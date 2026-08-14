import json
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

def get_logger(name):
    """
    Creates and returns a logger with the specified name.
    Writes to both console and a rotating log file.
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times if logger already exists
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # File Handler (max 5MB per file, keeps 2 backups)
        log_file = os.path.join(LOG_DIR, "jarvis.log")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5*1024*1024, backupCount=2
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


def log_event(logger, event: str, **fields):
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=True, default=str))
