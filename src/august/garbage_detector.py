"""Heuristics for rejecting obvious garbage or noise before execution."""

from __future__ import annotations

import re
from typing import Any


def detect_garbage_input(text: str, has_known_intent: bool = False) -> dict[str, Any]:
    """Return a structured verdict for inputs that should be rejected early."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return {"is_garbage": True, "reason": "empty_input"}

    tokens = [token for token in normalized.split(" ") if token]
    unique_tokens = set(tokens)

    if _has_repeated_words(tokens):
        return {"is_garbage": True, "reason": "repeated_words"}

    if _has_nonsensical_pattern(normalized):
        return {"is_garbage": True, "reason": "nonsensical_pattern"}

    if not has_known_intent and len(tokens) <= 2:
        return {"is_garbage": True, "reason": "too_short_without_intent"}

    if len(tokens) >= 4:
        diversity = len(unique_tokens) / float(len(tokens))
        if diversity <= 0.4:
            return {"is_garbage": True, "reason": "low_token_diversity", "diversity": round(diversity, 2)}

    return {"is_garbage": False, "reason": ""}


def _has_repeated_words(tokens: list[str]) -> bool:
    if len(tokens) < 3:
        return False
    streak = 1
    for index in range(1, len(tokens)):
        if tokens[index] == tokens[index - 1]:
            streak += 1
            if streak > 2:
                return True
        else:
            streak = 1
    return False


def _has_nonsensical_pattern(normalized: str) -> bool:
    if re.fullmatch(r"[^a-z0-9\s]+", normalized):
        return True
    if re.search(r"(.)\1{5,}", normalized):
        return True
    compact = normalized.replace(" ", "")
    if len(compact) >= 6 and not re.search(r"[aeiou]", compact) and not compact.isdigit():
        return True
    return False
