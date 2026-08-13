import pyttsx3
from utils.logger import get_logger

logger = get_logger("TTS")

class TTS:
    def __init__(self):
        try:
            self.engine = pyttsx3.init()
            # Set properties: speed and voice
            self.engine.setProperty('rate', 160)
            voices = self.engine.getProperty('voices')
            # Select first available voice, optionally change to Zira/David if present
            for voice in voices:
                if "Zira" in voice.name or "Female" in voice.name:
                    self.engine.setProperty('voice', voice.id)
                    break 
            logger.info("TTS Engine initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")

    def speak(self, text):
        logger.info(f"Speaking: '{text}'")
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            logger.error(f"TTS Speech error: {e}")

tts_engine = TTS()

def speak(text):
    tts_engine.speak(text)
