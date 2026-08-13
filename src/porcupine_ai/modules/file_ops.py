import os
from config import config
from utils.logger import get_logger
from core.tts import speak

logger = get_logger("FileOps")

def open_folder(folder_name):
    logger.info(f"Opening folder: {folder_name}")
    dir_paths = config.dir_paths
    
    # Check aliases in config first (e.g. 'downloads', 'desktop')
    if folder_name in dir_paths:
        path = dir_paths[folder_name]
    else:
        # Check standard user directories natively
        user_profile = os.environ.get('USERPROFILE')
        possible_path = os.path.join(user_profile, folder_name.title())
        if os.path.exists(possible_path):
            path = possible_path
        else:
            speak(f"I couldn't locate the folder {folder_name}.")
            logger.warning(f"Folder not found: {folder_name}")
            return False

    try:
        os.startfile(path)
        speak(f"Opening folder {folder_name}")
        return True
    except Exception as e:
        logger.error(f"Error opening folder {path}: {e}")
        speak(f"There was an error opening the folder {folder_name}.")
        return False
