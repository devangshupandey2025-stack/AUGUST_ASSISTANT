"""Shared helpers for identifying short conversational follow-up phrases."""

from __future__ import annotations

import re

FOLLOW_UP_PATTERNS = (
    r"^explain more(?:\s+(?:please|again|that|it))?$",
    r"^tell me more(?:\s+(?:please|again|about that))?$",
    r"^give example(?:\s+(?:please|again))?$",
    r"^give me (?:an? )?example(?:\s+(?:please|again))?$",
    r"^more(?:\s+(?:please|about that))?$",
    r"^yes(?:\s+(?:please|do))?$",
    r"^no(?:\s+(?:thanks|please))?$",
    r"^search it(?:\s+(?:please|for me))?$",
    r"^explain(?:\s+(?:please|that|it|again))?$",
    r"^tell me(?:\s+(?:please|that|again))?$",
)


def normalize_followup_text(text: str) -> str:
    """Normalize user text for follow-up matching."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_follow_up_query(text: str) -> bool:
    """Return True when the input is a short follow-up phrase, not a standalone query."""
    normalized = normalize_followup_text(text)
    if not normalized:
        return False
    return any(re.match(pattern, normalized) for pattern in FOLLOW_UP_PATTERNS)
