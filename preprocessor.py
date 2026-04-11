from __future__ import annotations

import re

from utils.logger import get_logger

logger = get_logger("Preprocessor")


class Preprocessor:
    def __init__(self) -> None:
        self.filler_phrases = (
            "please",
            "can you",
            "could you",
            "tell me",
            "i want to",
            "would you",
        )

    def clean(self, text: str | None) -> str:
        if not text:
            return ""

        cleaned = text.lower().strip()
        cleaned = re.sub(r"[^\w\s']", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
