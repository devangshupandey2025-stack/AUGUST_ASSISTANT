import speech_recognition as sr
from fuzzywuzzy import fuzz
from utils.logger import get_logger
from config import config

logger = get_logger("Listener")

class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        
        # dynamic energy threshold helps in noisy environments
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            
        self.wake_phrase = config.wake_phrase.lower()
        logger.info(f"Listener initialized. Wake phrase: '{self.wake_phrase}'")

    def listen_for_wake_word(self):
        """Continuously listens until the wake phrase is detected based on similarity."""
        logger.info("Listening for wake phrase...")
        while True:
            try:
                with self.microphone as source:
                    audio = self.recognizer.listen(source, phrase_time_limit=3)
                
                # Using Google's free online recognizer for quick testing without API keys
                text = self.recognizer.recognize_google(audio).lower()
                logger.debug(f"Heard: {text}")
                
                # Check similarity
                ratio = fuzz.partial_ratio(self.wake_phrase, text)
                if ratio > 80:  # 80% similarity threshold
                    logger.info(f"Wake word detected! (Similarity: {ratio}%)")
                    return True

            except sr.UnknownValueError:
                pass  # Did not understand audio
            except sr.RequestError as e:
                logger.error(f"Could not request results from service; {e}")
            except Exception as e:
                logger.error(f"Listener error: {e}")

    def listen_for_command(self):
        """Listens for the actual command after wake word is detected."""
        logger.info("Listening for command...")
        
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=8)
            text = self.recognizer.recognize_google(audio).lower()
            logger.info(f"Command transcribed: {text}")
            return text
        except sr.WaitTimeoutError:
            logger.warning("Listening timed out.")
            return None
        except sr.UnknownValueError:
            logger.warning("Could not understand command.")
            return None
        except Exception as e:
            logger.error(f"Command listener error: {e}")
            return None
