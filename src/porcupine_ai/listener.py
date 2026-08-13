from __future__ import annotations

import speech_recognition as sr
from fuzzywuzzy import fuzz

from config import config
from utils.logger import get_logger

logger = get_logger("Listener")


class Listener:
    def __init__(self) -> None:
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        speech_config = config.speech

        self.language = speech_config["language"]
        self.wake_phrase = config.wake_phrase.lower()
        self.wake_fuzzy_threshold = int(speech_config["wake_fuzzy_threshold"])
        self.wake_phrase_limit = int(speech_config["wake_phrase_limit_seconds"])
        self.command_timeout = int(speech_config["command_timeout_seconds"])
        self.command_phrase_limit = int(speech_config["command_phrase_limit_seconds"])

        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(
                source,
                duration=float(speech_config["ambient_adjust_seconds"]),
            )

        logger.info("Listener initialized with wake phrase '%s'", self.wake_phrase)

    def listen_for_wake_word(self) -> bool:
        logger.info("Waiting for wake word")
        while True:
            try:
                with self.microphone as source:
                    audio = self.recognizer.listen(source, phrase_time_limit=self.wake_phrase_limit)

                text = self.recognizer.recognize_google(audio, language=self.language).lower()
                score = fuzz.partial_ratio(self.wake_phrase, text)
                logger.debug("Wake phrase candidate '%s' scored %s", text, score)
                if score >= self.wake_fuzzy_threshold:
                    logger.info("Wake word detected from phrase '%s'", text)
                    return True
            except sr.UnknownValueError:
                continue
            except sr.RequestError as exc:
                logger.error("Speech recognition service error during wake phase: %s", exc)
                return False
            except Exception as exc:
                logger.exception("Unexpected wake word listener failure: %s", exc)
                return False

    def listen_for_command(self) -> str | None:
        logger.info("Listening for user command")
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(
                    source,
                    timeout=self.command_timeout,
                    phrase_time_limit=self.command_phrase_limit,
                )

            text = self.recognizer.recognize_google(audio, language=self.language).strip().lower()
            logger.info("Recognized command: %s", text)
            return text
        except sr.WaitTimeoutError:
            logger.warning("Command listening timed out")
            return None
        except sr.UnknownValueError:
            logger.warning("Speech was not understood")
            return None
        except sr.RequestError as exc:
            logger.error("Speech recognition service error during command phase: %s", exc)
            return None
        except Exception as exc:
            logger.exception("Unexpected command listener failure: %s", exc)
            return None
