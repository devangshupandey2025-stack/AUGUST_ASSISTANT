"""Search Result Filter — evaluates result relevance using entity overlap,
keyword matching, and domain awareness before article extraction.

Runs after search results are returned but before any HTTP fetching occurs,
to reject irrelevant results early (e.g. dictionary pages for AI comparison
queries).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from query_understanding import QueryIntent
from search_synthesizer import get_deprioritized_domains
from utils.logger import get_logger, log_event

logger = get_logger("ResultFilter")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_RELEVANCE_SCORE = 0.15

DICTIONARY_DOMAINS: tuple[str, ...] = (
    "dictionary.com",
    "merriam-webster.com",
    "cambridge.org",
    "thefreedictionary.com",
    "wiktionary.org",
    "dictionary.cambridge.org",
    "oxfordlearnersdictionaries.com",
    "collinsdictionary.com",
    "macmillandictionary.com",
)

# Query types where dictionary results are still acceptable.
DICTIONARY_ACCEPTABLE_TYPES: set[str] = {"definition"}

_STOP_WORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "am", "do", "does", "did", "will", "would", "could", "should",
    "can", "may", "might", "shall", "must", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "from", "into", "through",
    "and", "or", "but", "not", "if", "than", "then", "so", "as",
    "it", "its", "this", "that", "these", "those", "my", "your",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "vs", "versus", "comparison", "compare", "between", "difference",
    "better", "best", "worse", "more", "less", "most",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def filter_results(
    results: list,
    intent: QueryIntent,
    search_query: str,
    preferred_domains: list[str],
) -> list:
    """Filter and re-rank search results by semantic relevance.

    Returns a new list with irrelevant results removed and remaining results
    sorted by relevance score (descending).  Uses duck-typing for result
    objects (expects ``.title``, ``.href``, ``.snippet`` attributes).
    """
    deprioritized = get_deprioritized_domains()
    kept: list[tuple[float, int, object]] = []
    rejected_count = 0

    for index, result in enumerate(results):
        title = getattr(result, "title", "") or ""
        href = getattr(result, "href", "") or ""

        # Hard rejection.
        if _is_irrelevant_result(result, intent):
            log_event(
                logger,
                "irrelevant_result_rejected",
                source="result_filter",
                success=True,
                url=href,
                title=title,
                reason="hard_rejection",
                query_type=intent.type,
            )
            rejected_count += 1
            continue

        # Compute relevance.
        score = _relevance_score(result, intent, search_query, preferred_domains, deprioritized)
        if score < MIN_RELEVANCE_SCORE:
            log_event(
                logger,
                "irrelevant_result_rejected",
                source="result_filter",
                success=True,
                url=href,
                title=title,
                reason="low_relevance",
                score=round(score, 3),
            )
            rejected_count += 1
            continue

        kept.append((score, index, result))

    # Sort by score descending, then by original order.
    kept.sort(key=lambda item: (-item[0], item[1]))
    filtered = [item[2] for item in kept]

    log_event(
        logger,
        "domain_rank_applied",
        source="result_filter",
        success=True,
        total=len(results),
        kept=len(filtered),
        rejected=rejected_count,
        query_type=intent.type,
    )
    return filtered


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------
def _relevance_score(
    result: object,
    intent: QueryIntent,
    search_query: str,
    preferred_domains: list[str],
    deprioritized: tuple[str, ...],
) -> float:
    """Compute a 0.0–1.0 relevance score for a single search result."""
    entity_score = _entity_overlap(result, intent.entities)
    keyword_score = _keyword_overlap(result, search_query)
    domain_score = _domain_relevance(result, preferred_domains, deprioritized)

    return (
        entity_score * 0.35
        + keyword_score * 0.35
        + domain_score * 0.30
    )


def _entity_overlap(result: object, entities: list[str]) -> float:
    """Check how many expected entities appear in the result's title + snippet."""
    if not entities:
        return 0.5  # Neutral when no entities are expected.

    text = _result_text(result).lower()
    matched = sum(1 for entity in entities if entity.lower() in text)
    return matched / len(entities)


def _keyword_overlap(result: object, search_query: str) -> float:
    """Compute term overlap between the search query and the result text."""
    query_terms = _extract_keywords(search_query)
    if not query_terms:
        return 0.5

    text = _result_text(result).lower()
    result_terms = set(re.findall(r"[a-z0-9]+", text))
    matched = len(query_terms & result_terms)
    return min(matched / len(query_terms), 1.0)


def _domain_relevance(
    result: object,
    preferred_domains: list[str],
    deprioritized: tuple[str, ...],
) -> float:
    """Score the result's domain against preferred/deprioritized lists."""
    domain = _extract_domain(getattr(result, "href", "") or "")
    if not domain:
        return 0.3

    for pref in preferred_domains:
        if domain == pref or domain.endswith("." + pref):
            return 1.0

    for dep in deprioritized:
        if domain == dep or domain.endswith("." + dep):
            return 0.1

    return 0.5


# ---------------------------------------------------------------------------
# Hard rejection
# ---------------------------------------------------------------------------
def _is_irrelevant_result(result: object, intent: QueryIntent) -> bool:
    """Apply hard rejection rules — returns True to discard the result."""
    domain = _extract_domain(getattr(result, "href", "") or "")

    # Reject dictionary sites for non-definition queries.
    if intent.type not in DICTIONARY_ACCEPTABLE_TYPES:
        if any(domain == d or domain.endswith("." + d) for d in DICTIONARY_DOMAINS):
            return True

    # For comparison queries with known entities, reject if NONE appear in text.
    if intent.type == "comparison" and intent.entities:
        text = _result_text(result).lower()
        if not any(entity.lower() in text for entity in intent.entities):
            return True

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _result_text(result: object) -> str:
    """Concatenate title and snippet of a result for text analysis."""
    title = getattr(result, "title", "") or ""
    snippet = getattr(result, "snippet", "") or ""
    return f"{title} {snippet}"


def _extract_domain(url: str) -> str:
    """Parse a URL and return the domain without 'www.' prefix."""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "").strip()
        return host
    except Exception:
        return ""


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    short_terms = {"ai", "ml", "ui", "ux", "os", "qa", "db", "js", "ts"}
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return {w for w in words if (len(w) > 2 or w in short_terms) and w not in _STOP_WORDS}
