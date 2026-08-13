from __future__ import annotations

import threading
import time

import pyttsx3

from config import config
from personality_engine import personality_engine
from utils.logger import get_logger

logger = get_logger("TTS")


class TTS:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", int(config.tts["rate"]))
        self._select_voice(str(config.tts["preferred_voice"]).lower())
        logger.info("TTS engine initialized")

    def _select_voice(self, preferred_voice: str) -> None:
        for voice in self.engine.getProperty("voices"):
            voice_name = getattr(voice, "name", "").lower()
            if preferred_voice and preferred_voice in voice_name:
                self.engine.setProperty("voice", voice.id)
                logger.info("Selected TTS voice '%s'", voice.name)
                return

    def speak(self, text: str) -> None:
        if not text:
            return
        rendered = personality_engine.render_for_tts(text)
        rendered = personality_engine.add_optional_hook(rendered)
        chunks = personality_engine.split_for_speech(rendered)
        if not chunks:
            return
        logger.info("Speaking response: %s", rendered)
        with self._lock:
            for idx, chunk in enumerate(chunks):
                self.engine.say(chunk)
                self.engine.runAndWait()
                if idx < len(chunks) - 1:
                    time.sleep(0.12)


tts_engine = TTS()


def speak(text: str) -> None:
    tts_engine.speak(text)
