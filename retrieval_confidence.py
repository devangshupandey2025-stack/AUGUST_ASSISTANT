"""Retrieval Confidence Engine — enhanced confidence scoring incorporating
query understanding, entity alignment, and domain relevance signals.

Supplements the existing ``_assess_research_confidence`` in ``web_research.py``
with richer signal sources when a ``QueryIntent`` is available.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from query_understanding import QueryIntent
from utils.logger import get_logger, log_event

logger = get_logger("RetrievalConfidence")

# Short terms that should be kept despite length < 3.
_SHORT_TERMS: set[str] = {"ai", "ml", "ui", "ux", "os", "qa", "db", "js", "ts"}

_STOP_WORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "am", "do", "does", "did", "will", "would", "could", "should",
    "can", "may", "might", "shall", "must", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "from", "into", "through",
    "and", "or", "but", "not", "if", "than", "then", "so", "as",
    "it", "its", "this", "that", "these", "those", "my", "your",
    "what", "which", "who", "whom", "where", "when", "why", "how",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def assess_retrieval_confidence(
    article_text: str,
    answer: str,
    query: str,
    intent: QueryIntent,
    source_quality: float,
    extractor_quality: float,
    domain_relevance: float = 0.5,
) -> dict[str, object]:
    """Compute an enhanced confidence score using query-understanding signals.

    Returns a dict with ``"confidence"`` (float 0.0–1.0) and ``"reason"``
    (human-readable string explaining the dominant signal).
    """
    # 1. Entity match (max 0.20)
    entity_score = _entity_match_score(article_text, intent.entities) * 0.20
    if not intent.entities:
        entity_score = 0.15  # Neutral when no entities are expected.

    # 2. Domain quality (max 0.20)
    domain_quality_score = 0.20 * max(0.0, min(1.0, source_quality))

    # 3. Semantic overlap (max 0.25)
    query_keywords = _extract_keywords(query)
    answer_keywords = _extract_keywords(answer)
    if query_keywords:
        overlap_ratio = len(query_keywords & answer_keywords) / len(query_keywords)
        semantic_score = 0.25 * min(overlap_ratio * 1.5, 1.0)
    else:
        semantic_score = 0.12  # Baseline when query has no keywords.

    # 4. Extraction quality (max 0.15)
    extraction_score = 0.15 * max(0.0, min(1.0, extractor_quality))

    # 5. Domain relevance (max 0.20)
    domain_rel_score = 0.20 * max(0.0, min(1.0, domain_relevance))

    total = entity_score + domain_quality_score + semantic_score + extraction_score + domain_rel_score
    total = max(0.0, min(1.0, total))

    # Determine reason.
    reason = _determine_reason(entity_score, semantic_score, domain_quality_score)

    log_event(
        logger,
        "retrieval_confidence_assigned",
        source="retrieval_confidence",
        success=True,
        query=query,
        confidence=round(total, 3),
        reason=reason,
        entity_score=round(entity_score, 3),
        semantic_score=round(semantic_score, 3),
        domain_quality=round(domain_quality_score, 3),
        extraction_quality=round(extraction_score, 3),
        domain_relevance=round(domain_rel_score, 3),
    )

    return {"confidence": total, "reason": reason}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _entity_match_score(article_text: str, entities: list[str]) -> float:
    """Check case-insensitive presence of entities in article text."""
    if not entities:
        return 0.75  # Neutral score when no entities are expected.

    text_lower = (article_text or "").lower()
    matched = sum(1 for entity in entities if entity.lower() in text_lower)
    return matched / len(entities)


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return {w for w in words if (len(w) > 2 or w in _SHORT_TERMS) and w not in _STOP_WORDS}


def _determine_reason(entity_score: float, semantic_score: float, domain_score: float) -> str:
    """Pick a human-readable reason based on the dominant signal."""
    if entity_score >= 0.15 and semantic_score >= 0.15:
        return "strong_entity_alignment"
    if entity_score >= 0.15:
        return "good_entity_match"
    if semantic_score >= 0.15:
        return "good_semantic_overlap"
    if domain_score >= 0.15:
        return "trusted_domain"
    return "weak_signals"
