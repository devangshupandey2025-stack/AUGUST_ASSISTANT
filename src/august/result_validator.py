"""Result Validator — validates search results, fetched articles, and answer
relevance before summarization.

Provides three validation layers:
  1. Entity validation — reject search results that don't mention required entities
  2. Article validation — reject fetched articles that don't discuss the topic
  3. Answer verification — reject articles that don't answer the user's question
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from august.query_understanding import QueryIntent
from august.utils.logger import get_logger, log_event

logger = get_logger("ResultValidator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_ENTITY_COVERAGE = 0.20
MIN_HEADING_MATCH = 0.30
MIN_ANSWER_COVERAGE = 0.20


def _metadata_string_list(intent: QueryIntent, key: str) -> list[str]:
    raw_value = intent.metadata.get(key, [])
    if not isinstance(raw_value, list):
        return []
    return [item for item in raw_value if isinstance(item, str)]


# ---------------------------------------------------------------------------
# 1. Search Result Entity Validation
# ---------------------------------------------------------------------------
def validate_search_result(
    result: object,
    intent: QueryIntent,
    required_entities: list[str],
) -> dict[str, object]:
    """Validate a search result against required entities BEFORE fetching.

    Returns ``{"valid": bool, "reason": str, "entity_overlap": float}``.
    """
    title = str(getattr(result, "title", "") or "")
    snippet = str(getattr(result, "snippet", "") or "")
    href = str(getattr(result, "href", "") or "")
    combined_text = f"{title} {snippet}"
    url_path = urlparse(href).path.lower()

    # --- Office-holder mandatory check ---
    # For office-holder queries, require BOTH office AND location in the result.
    offices = _metadata_string_list(intent, "offices")
    locations = _metadata_string_list(intent, "locations")
    if offices and locations:
        combined_lower = combined_text.lower()
        has_office = any(o.lower() in combined_lower for o in offices)
        has_location = any(loc.lower() in combined_lower for loc in locations)
        if not (has_office and has_location):
            missing: list[str] = []
            if not has_office:
                missing.extend(offices)
            if not has_location:
                missing.extend(locations)
            log_event(
                logger,
                "entity_validation_failed",
                source="result_validator",
                success=False,
                url=href,
                title=title,
                reason="missing_office_or_location",
                missing=missing,
            )
            return {"valid": False, "reason": "missing_office_or_location", "entity_overlap": 0.0}

    if not required_entities:
        return {"valid": True, "reason": "no_entities_required", "entity_overlap": 1.0}

    matched = 0
    for entity in required_entities:
        entity_lower = entity.lower()
        in_title = entity_lower in title.lower()
        in_snippet = entity_lower in snippet.lower()
        in_url = entity_lower in url_path

        if in_title or in_snippet or in_url:
            matched += 1

    overlap = matched / len(required_entities) if required_entities else 1.0

    if overlap < MIN_ENTITY_COVERAGE:
        log_event(
            logger,
            "entity_validation_failed",
            source="result_validator",
            success=False,
            url=href,
            title=title,
            required=required_entities,
            overlap=round(overlap, 3),
        )
        return {"valid": False, "reason": "insufficient_entity_overlap", "entity_overlap": overlap}

    log_event(
        logger,
        "entity_validation_passed",
        source="result_validator",
        success=True,
        url=href,
        overlap=round(overlap, 3),
    )
    return {"valid": True, "reason": "entity_overlap_ok", "entity_overlap": overlap}


# ---------------------------------------------------------------------------
# 2. Article Content Validation
# ---------------------------------------------------------------------------
def validate_article_content(
    article_text: str,
    intent: QueryIntent,
    title: str = "",
) -> dict[str, object]:
    """Validate fetched article text using weighted title/heading/body scoring.

    Returns ``{"valid": bool, "reason": str, "coverage": float}``.
    """
    if not article_text or len(article_text) < 100:
        return {"valid": False, "reason": "article_too_short", "coverage": 0.0}

    required_entities = list(intent.entities or [])
    # Also include metadata entities (offices, locations) for office-holder queries.
    offices = _metadata_string_list(intent, "offices")
    locations = _metadata_string_list(intent, "locations")
    for item in offices + locations:
        if item not in required_entities:
            required_entities.append(item)
    if not required_entities:
        return {"valid": True, "reason": "no_entities_required", "coverage": 1.0}

    headings = _extract_headings(article_text)
    text_lower = article_text.lower()
    title_lower = (title or "").lower()

    total_score = 0.0
    for entity in required_entities:
        entity_lower = entity.lower()

        # Weighted scoring: title (0.50) + heading (0.30) + body (0.20)
        title_hit = 1.0 if entity_lower in title_lower else 0.0
        heading_hit = 1.0 if any(entity_lower in h.lower() for h in headings) else 0.0
        body_count = text_lower.count(entity_lower)
        body_hit = min(body_count / 5.0, 1.0)

        entity_score = title_hit * 0.50 + heading_hit * 0.30 + body_hit * 0.20
        total_score += entity_score

        if entity_score < MIN_ENTITY_COVERAGE:
            log_event(
                logger,
                "article_validation_failed",
                source="result_validator",
                success=False,
                entity=entity,
                title_hit=title_hit,
                heading_hit=heading_hit,
                body_hit=round(body_hit, 3),
                entity_score=round(entity_score, 3),
            )
            return {"valid": False, "reason": f"entity '{entity}' insufficient coverage", "coverage": entity_score}

    avg_coverage = total_score / len(required_entities) if required_entities else 1.0

    log_event(
        logger,
        "article_validation_passed",
        source="result_validator",
        success=True,
        coverage=round(avg_coverage, 3),
    )
    return {"valid": True, "reason": "article_content_ok", "coverage": avg_coverage}


# ---------------------------------------------------------------------------
# 3. Answer Verification
# ---------------------------------------------------------------------------
def verify_answer_relevance(
    article_text: str,
    query: str,
    intent: QueryIntent,
) -> dict[str, object]:
    """Verify that the extracted article actually answers the user's question.

    Returns ``{"valid": bool, "reason": str, "coverage": float}``.
    """
    if not article_text:
        return {"valid": False, "reason": "empty_article", "coverage": 0.0}

    text_lower = article_text.lower()
    required_entities = [e.lower() for e in (intent.entities or [])]
    # Also include metadata entities for office-holder queries.
    offices = _metadata_string_list(intent, "offices")
    locations = _metadata_string_list(intent, "locations")
    for item in offices + locations:
        item_lower = item.lower()
        if item_lower not in required_entities:
            required_entities.append(item_lower)

    # Check 1 — Required entities present in text.
    if required_entities:
        present = sum(1 for e in required_entities if e in text_lower)
        entity_coverage = present / len(required_entities)
        if entity_coverage < 0.5:
            missing = [e for e in required_entities if e not in text_lower]
            log_event(
                logger,
                "answer_validation_failed",
                source="result_validator",
                success=False,
                reason="missing_entities",
                missing=missing,
                coverage=round(entity_coverage, 3),
            )
            return {"valid": False, "reason": f"missing_entities:{missing}", "coverage": entity_coverage}

    # Check 2 — Query-type-specific verification.
    if intent.type == "dynamic_fact":
        if not _text_has_factual_answer(article_text, intent):
            log_event(logger, "answer_validation_failed", source="result_validator", success=False, reason="no_factual_answer")
            return {"valid": False, "reason": "no_factual_answer", "coverage": 0.0}

    if intent.type == "definition":
        if not _text_has_definition(article_text):
            log_event(logger, "answer_validation_failed", source="result_validator", success=False, reason="no_definition_found")
            return {"valid": False, "reason": "no_definition_found", "coverage": 0.0}

    if intent.type == "comparison":
        entities_lower = set(e.lower() for e in (intent.entities or []))
        if entities_lower:
            found = sum(1 for e in entities_lower if e in text_lower)
            if found < len(entities_lower):
                log_event(logger, "answer_validation_failed", source="result_validator", success=False, reason="missing_comparison_entities")
                return {"valid": False, "reason": "missing_comparison_entities", "coverage": found / len(entities_lower)}

    # Check 3 — Answer-bearing sentences exist.
    answer_sentences = _find_answer_sentences(article_text, intent)
    if not answer_sentences:
        log_event(logger, "answer_validation_failed", source="result_validator", success=False, reason="no_answer_sentences")
        return {"valid": False, "reason": "no_answer_sentences", "coverage": 0.0}

    log_event(logger, "answer_validation_passed", source="result_validator", success=True, coverage=1.0)
    return {"valid": True, "reason": "verified", "coverage": 1.0}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _extract_headings(text: str) -> list[str]:
    """Extract heading-like lines from article text."""
    headings: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Short lines that look like headings (title case, no period)
        if len(stripped) < 80 and not stripped.endswith(".") and stripped[0:1].isupper():
            headings.append(stripped)
    return headings[:20]


def _text_has_factual_answer(text: str, intent: QueryIntent) -> bool:
    """Check if text contains a factual answer for dynamic_fact queries."""
    text_lower = text.lower()

    # For office holder queries: look for "is [Name]" pattern near office title.
    offices = _metadata_string_list(intent, "offices")
    if offices:
        for office in offices:
            pattern = rf"{office.lower()}\s+(?:of\s+\w+\s+)?(?:is|was)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
            if re.search(pattern, text, re.IGNORECASE):
                return True
            # Check if entity name appears near office title word.
            entities_lower = [e.lower() for e in (intent.entities or [])]
            for entity in entities_lower:
                if entity in text_lower and office.lower() in text_lower:
                    return True

    # General: check for date/number markers (dynamic facts).
    dynamic_markers = ("in 2024", "in 2025", "in 2026", "as of", "currently", "latest", "today")
    return any(marker in text_lower for marker in dynamic_markers)


def _text_has_definition(text: str) -> bool:
    """Check if text contains a definition-style explanation."""
    definition_markers = ("is a ", "is an ", "refers to", "defined as", "means that", "is the process", "is a type")
    return any(marker in text.lower() for marker in definition_markers)


def _find_answer_sentences(text: str, intent: QueryIntent) -> list[str]:
    """Find sentences that actually answer the query."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    required_terms = [e.lower() for e in (intent.entities or [])]
    topic_terms = (intent.topic or "").lower().split()
    all_terms = required_terms + topic_terms

    if not all_terms:
        return sentences[:3]

    answer_bearing: list[str] = []
    for sentence in sentences:
        sentence_lower = sentence.lower()
        term_matches = sum(1 for term in all_terms if term in sentence_lower)
        if term_matches >= 2:
            answer_bearing.append(sentence)

    return answer_bearing
