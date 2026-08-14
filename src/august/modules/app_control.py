from AppOpener import close as close_app_cmd
from AppOpener import open as open_app_cmd

from august.config import config
from august.core.tts import speak
from august.utils.logger import get_logger

logger = get_logger("AppControl")

def open_app(app_name):
    logger.info(f"Attempting to open app: {app_name}")
    app_paths = config.app_paths
    if app_name in app_paths:
        import os
        os.startfile(app_paths[app_name])
        speak(f"Opening {app_name}")
        return True
    
    try:
        open_app_cmd(app_name, match_closest=True)
        speak(f"Opening {app_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to open app {app_name}: {e}")
        speak(f"I couldn't open the application {app_name}.")
        return False

def close_app(app_name):
    logger.info(f"Attempting to close app: {app_name}")
    try:
        close_app_cmd(app_name, match_closest=True)
        speak(f"Closing {app_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to close app {app_name}: {e}")
        speak(f"I couldn't close the application {app_name}.")
        return False
