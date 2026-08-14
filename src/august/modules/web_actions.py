import webbrowser
from urllib.parse import quote_plus

from august.core.tts import speak
from august.utils.logger import get_logger

logger = get_logger("WebActions")

def search_web(entity):
    platform = entity.get('platform')
    query = entity.get('query')
    
    logger.info(f"Web search: {platform} - {query}")
    if query:
        speak(f"Searching {platform} for {query}")
    else:
        speak(f"Opening {platform}")
    
    try:
        if platform == 'youtube':
            if query:
                encoded_query = quote_plus(query)
                url = f"https://www.youtube.com/results?search_query={encoded_query}"
            else:
                url = "https://www.youtube.com"
        else:
            if query:
                encoded_query = quote_plus(query)
                url = f"https://www.google.com/search?q={encoded_query}"
            else:
                url = "https://www.google.com"
             
        webbrowser.open(url)
    except Exception as e:
        logger.error(f"Web browser error: {e}")
        speak("There was an issue opening the web browser.")
