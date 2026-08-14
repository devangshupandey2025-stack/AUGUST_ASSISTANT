from __future__ import annotations

import re

from august.query_understanding import QueryIntent

_WEATHER_KEYWORDS: frozenset[str] = frozenset({
    "weather", "forecast", "temperature", "temp", "rain", "humidity",
    "wind", "sunny", "cloudy", "storm", "precipitation", "heat", "cold",
    "sunrise", "sunset",
})

_SUN_KEYWORDS: frozenset[str] = frozenset({"sunrise", "sunset"})

_WEATHER_QUESTION_PREFIXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:what(?:'s| is)\s+)?(?:the\s+)?(?:weather|forecast|temperature|humidity|wind|rain)\s+(?:like\s+)?(?:in\s+|for\s+|of\s+)?", re.IGNORECASE),
    re.compile(r"^(?:will\s+it\s+)(?:rain|snow|storm|be\s+sunny|be\s+cloudy)", re.IGNORECASE),
    re.compile(r"^(?:temperature|humidity|wind|rain)\s+in\b", re.IGNORECASE),
    re.compile(r"^(?:sunrise|sunset)\s+(?:in\s+|for\s+|of\s+)?", re.IGNORECASE),
    re.compile(r"^(?:is\s+it\s+)(?:raining|sunny|cloudy|hot|cold)", re.IGNORECASE),
)


def is_weather_query(cleaned: str) -> bool:
    if not cleaned:
        return False
    if any(pattern.match(cleaned) for pattern in _WEATHER_QUESTION_PREFIXES):
        return True
    tokens = cleaned.split()
    if len(tokens) <= 3:
        weather_token_count = sum(1 for t in tokens if t in _WEATHER_KEYWORDS)
        if weather_token_count >= 1 and not any(
            t in _SUN_KEYWORDS for t in tokens
        ):
            return True
        if any(t in _SUN_KEYWORDS for t in tokens):
            return True
    return any(marker in cleaned for marker in _WEATHER_KEYWORDS)


_CITY_NAME_PATTERN = re.compile(
    r"\b(?:in|for|at|near|of)\s+([A-Za-z][A-Za-z\s'-]{1,60}?)(?:\s*\?|\.|,|\s*$|\s+(?:today|tomorrow|now|right now|currently|this week))",
    re.IGNORECASE,
)

_CITY_FALLBACK_PATTERN = re.compile(
    r"^(?:weather|forecast|temperature|humidity|wind|rain|sunrise|sunset)\s+(?:in\s+|for\s+|of\s+|at\s+)?([A-Za-z][A-Za-z\s'-]{1,60}?)(?:\s*\?|\.|,|\s*$|\s+(?:today|tomorrow))",
    re.IGNORECASE,
)


def extract_location(intent: QueryIntent) -> str | None:
    metadata = intent.metadata or {}
    locations = metadata.get("locations")
    if locations and isinstance(locations, (list, tuple)) and len(locations) > 0:
        return str(locations[0])
    if intent.entities:
        for e in intent.entities:
            e_lower = e.lower()
            if e_lower not in _WEATHER_KEYWORDS and len(e) > 2:
                return e
    raw = intent.raw_query
    match = _CITY_NAME_PATTERN.search(raw)
    if match:
        city = match.group(1).strip()
        if city and not any(
            kw in city.lower() for kw in _WEATHER_KEYWORDS
        ):
            return city.title()
    match = _CITY_FALLBACK_PATTERN.search(raw)
    if match:
        city = match.group(1).strip()
        if city:
            return city.title()
    return None
