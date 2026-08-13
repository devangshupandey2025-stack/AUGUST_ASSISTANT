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
    """Compute an enhanced confidence score using the weighted formula.

    confidence =
        0.30 * entity_match_score
      + 0.25 * semantic_similarity_score
      + 0.20 * source_quality_score
      + 0.15 * extraction_quality_score
      + 0.10 * answer_coverage_score

    Returns a dict with ``"confidence"`` (float 0.0–1.0) and ``"reason"``
    (human-readable string explaining the dominant signal).
    """
    # 1. Entity match (0.30)
    entity_score = _entity_match_score(article_text, intent.entities) * 0.30
    if not intent.entities:
        entity_score = 0.22  # Neutral when no entities expected.

    # 2. Semantic similarity (0.25)
    semantic_score = _semantic_similarity(answer, query) * 0.25

    # 3. Source quality (0.20)
    source_quality_score = 0.20 * max(0.0, min(1.0, source_quality))

    # 4. Extraction quality (0.15)
    extraction_score = 0.15 * max(0.0, min(1.0, extractor_quality))

    # 5. Answer coverage (0.10)
    coverage_score = 0.10 * _answer_coverage(answer, intent)

    total = entity_score + semantic_score + source_quality_score + extraction_score + coverage_score
    total = max(0.0, min(1.0, total))

    # Determine reason.
    reason = _determine_reason(entity_score, semantic_score, source_quality_score)

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
        source_quality=round(source_quality_score, 3),
        extraction_quality=round(extraction_score, 3),
        coverage_score=round(coverage_score, 3),
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


def _semantic_similarity(answer: str, query: str) -> float:
    """Compute semantic similarity between answer and query using keyword overlap."""
    query_keywords = _extract_keywords(query)
    answer_keywords = _extract_keywords(answer)

    if not query_keywords:
        return 0.5  # Neutral when query has no keywords.

    overlap = len(query_keywords & answer_keywords)
    ratio = overlap / len(query_keywords)

    # Boost if answer is substantially longer (contains more context).
    answer_len_bonus = min(len(answer_keywords) / max(len(query_keywords) * 2, 1), 0.3)

    return min(ratio + answer_len_bonus, 1.0)


def _answer_coverage(answer: str, intent: QueryIntent) -> float:
    """Check if the answer actually addresses the query type."""
    if not answer:
        return 0.0

    answer_lower = answer.lower()
    coverage = 0.5  # Base coverage.

    # For dynamic_fact: check for factual markers.
    if intent.type == "dynamic_fact":
        factual_markers = ("is", "are", "was", "were", "has", "in ", "at ", "current", "latest")
        if any(marker in answer_lower for marker in factual_markers):
            coverage += 0.3
        # Check for entity names.
        for entity in (intent.entities or []):
            if entity.lower() in answer_lower:
                coverage += 0.1

    # For definition: check for explanatory language.
    if intent.type == "definition":
        definition_markers = ("is a", "is an", "refers to", "defined as", "means")
        if any(marker in answer_lower for marker in definition_markers):
            coverage += 0.4

    # For comparison: check for both entities mentioned.
    if intent.type == "comparison" and len(intent.entities) >= 2:
        mentioned = sum(1 for e in intent.entities if e.lower() in answer_lower)
        coverage += 0.3 * (mentioned / len(intent.entities))

    # General: answer length suggests coverage.
    if len(answer) > 100:
        coverage += 0.1

    return min(coverage, 1.0)


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
