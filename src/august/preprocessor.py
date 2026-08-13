from __future__ import annotations

import re
from fuzzywuzzy import fuzz

from config import config

from utils.logger import get_logger

logger = get_logger("Preprocessor")


class Preprocessor:
    WEAK_REPLIES = {"yes", "no", "it", "search it"}

    def __init__(self) -> None:
        self.filler_phrases = (
            "please",
            "can you",
            "could you",
            "tell me",
            "i want to",
            "would you",
        )
        self.wake_phrase = self._normalize_wake_phrase(config.wake_phrase)
        self.wake_threshold = int(config.speech.get("wake_fuzzy_threshold", 80))

    def clean(self, text: str | None) -> str:
        if not text:
            return ""

        cleaned = text.lower().strip()
        cleaned = re.sub(r"[^\w\s']", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = self._normalize_short_replies(cleaned)
        cleaned = self._strip_wake_phrase_prefix(cleaned)
        cleaned = self._strip_fillers(cleaned)
        cleaned = self._normalize_patterns(cleaned)
        logger.debug("Preprocessed text '%s' -> '%s'", text, cleaned)
        return cleaned

    def _strip_fillers(self, text: str) -> str:
        cleaned = text
        changed = True
        while changed:
            changed = False
            for phrase in self.filler_phrases:
                updated = re.sub(rf"^\s*{re.escape(phrase)}\s+", "", cleaned).strip()
                if updated != cleaned:
                    cleaned = updated
                    changed = True
        return cleaned

    def _normalize_patterns(self, text: str) -> str:
        cleaned = re.sub(r"^(open)\s+\1\s+", r"\1 ", text)
        cleaned = re.sub(r"^(close)\s+\1\s+", r"\1 ", cleaned)
        cleaned = re.sub(r"\bindia vs\b", "india versus", cleaned)
        cleaned = re.sub(r"^youtube\s+for\s+", "open youtube for ", cleaned)
        cleaned = re.sub(r"^google\s+for\s+", "google for ", cleaned)
        cleaned = re.sub(r"^what s\b", "what's", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def is_valid_wake_phrase(self, detected_phrase: str) -> bool:
        normalized = self._normalize_wake_phrase(detected_phrase)
        if not normalized or not self.wake_phrase:
            return False
        if normalized != self.wake_phrase:
            return False
        return fuzz.ratio(self.wake_phrase, normalized) >= self.wake_threshold

    def _normalize_wake_phrase(self, phrase: str | None) -> str:
        text = (phrase or "").strip().lower()
        text = re.sub(r"[^\w\s']", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_short_replies(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
        if cleaned in self.WEAK_REPLIES:
            return cleaned
        return text

    def _strip_wake_phrase_prefix(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not cleaned or not self.wake_phrase:
            return cleaned
        if cleaned == self.wake_phrase:
            return ""
        prefix = f"{self.wake_phrase} "
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip()
        wake_index = cleaned.find(self.wake_phrase)
        if wake_index > 0:
            return ""
        return cleaned
