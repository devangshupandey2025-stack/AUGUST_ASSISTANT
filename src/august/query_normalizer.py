"""Query Normalizer — cleans STT noise and rewrites queries with corrected entities.

Runs after query understanding and before search query synthesis.  Handles
filler-word removal, repeated-token deduplication, and entity-name
canonicalisation so the downstream search receives a clean, entity-correct
query string.
"""

from __future__ import annotations

import re

from august.acronym_resolver import expand_acronyms
from august.query_understanding import ENTITY_ALIASES, QueryIntent
from august.utils.logger import get_logger, log_event

logger = get_logger("QueryNormalizer")

# Pre-build lowercase alias lookup sorted by key length (longest first)
# so multi-word aliases are matched before single-word ones.
_ALIAS_LOWER_SORTED: list[tuple[str, str]] = sorted(
    ((k.lower(), v) for k, v in ENTITY_ALIASES.items()),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

# Filler phrases commonly injected by STT engines.
_STT_FILLERS: tuple[str, ...] = (
    "okay so",
    "you know",
    "i mean",
    "basically",
    "actually",
    "like",
    "well",
    "so",
    "um",
    "uh",
    "hmm",
    "ah",
)

# Trailing courtesy words to strip.
_TRAILING_FILLER: tuple[str, ...] = (
    "please",
    "for me",
    "right now",
    "quickly",
    "fast",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def normalize_query(query: str, intent: QueryIntent) -> str:
    """Clean STT noise, expand acronyms, and rewrite entities in *query*.

    If the intent is a comparison with two or more entities, the output is
    collapsed to ``"EntityA vs EntityB"`` format.
    """
    if not query or not query.strip():
        return query or ""

    # Step 1 — basic STT noise removal.
    cleaned = _clean_stt_noise(query)

    # Step 2 — acronym expansion (reuse existing module).
    expanded, _ = expand_acronyms(cleaned)
    if expanded and expanded != cleaned:
        cleaned = expanded

    # Step 3 — replace fuzzy entity mentions with canonical names.
    cleaned = _replace_entities(cleaned, intent.entities, intent)

    # Step 4 — for comparison queries, build the canonical "A vs B" form.
    if intent.type == "comparison" and len(intent.entities) >= 2:
        cleaned = _build_comparison_query(intent.entities)

    # Final whitespace normalisation.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    log_event(
        logger,
        "query_normalized",
        source="query_normalizer",
        success=True,
        original=query,
        normalized=cleaned,
        query_type=intent.type,
        entities=intent.entities,
    )
    return cleaned


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _clean_stt_noise(query: str) -> str:
    """Remove common speech-to-text artefacts from *query*."""
    cleaned = re.sub(r"\s+", " ", (query or "").strip().lower())

    # Strip leading filler phrases (iteratively — they can stack).
    changed = True
    while changed:
        changed = False
        for filler in _STT_FILLERS:
            pattern = rf"^\s*{re.escape(filler)}\s+"
            updated = re.sub(pattern, "", cleaned).strip()
            if updated != cleaned:
                cleaned = updated
                changed = True

    # Strip trailing courtesy words.
    for filler in _TRAILING_FILLER:
        pattern = rf"\s+{re.escape(filler)}\s*$"
        cleaned = re.sub(pattern, "", cleaned).strip()

    # De-duplicate consecutive identical words ("the the" → "the").
    words = cleaned.split()
    if words:
        deduped: list[str] = [words[0]]
        for i in range(1, len(words)):
            if words[i] != words[i - 1]:
                deduped.append(words[i])
        cleaned = " ".join(deduped)

    return cleaned.strip()


def _replace_entities(query: str, entities: list[str], intent: QueryIntent) -> str:
    """Replace fuzzy/corrupted entity mentions in *query* with canonical forms."""
    if not entities:
        return query

    result = query.lower()
    entity_set = set(entities)

    # Walk through aliases (longest first) and replace matches.
    for alias_lower, canonical in _ALIAS_LOWER_SORTED:
        if canonical not in entity_set:
            continue
        if alias_lower in result:
            result = result.replace(alias_lower, canonical)

    return result


def _build_comparison_query(entities: list[str]) -> str:
    """Return a canonical ``"A vs B"`` comparison string."""
    return f"{entities[0]} vs {entities[1]}"
