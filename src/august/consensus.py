"""Consensus Engine — deterministic multi-source fact agreement.

Compares summaries from multiple sources using normalized text comparison
(no LLM dependency). Finds agreed facts and disputed facts. Gemini is
optional polish only — never in the critical path.
"""

from __future__ import annotations

import re
from collections import Counter

from query_understanding import QueryIntent
from utils.logger import get_logger, log_event

logger = get_logger("Consensus")

# ---------------------------------------------------------------------------
# Stop words for normalization
# ---------------------------------------------------------------------------
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "am", "do", "does", "did", "will", "would", "could", "should",
    "can", "may", "might", "shall", "must", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "from", "into", "through",
    "and", "or", "but", "not", "if", "than", "then", "so", "as",
    "it", "its", "this", "that", "these", "those", "my", "your",
    "his", "her", "our", "their", "me", "him", "us", "them",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "also", "very", "just", "been", "has", "have", "had", "having",
})

# Simple stem suffixes (no NLTK dependency).
_STEM_SUFFIXES: list[tuple[str, str]] = [
    ("ying", "y"), ("ting", "t"), ("ing", ""),
    ("ness", ""), ("ment", ""), ("tion", "t"),
    ("ally", ""), ("ful", ""), ("less", ""),
    ("ies", "y"), ("ves", "f"),
    ("edly", ""), ("ily", "y"),
    ("ed", ""), ("er", ""), ("es", ""),
    ("ly", ""), ("s", ""),
]

# Entity alias map for normalization.
_ENTITY_ALIASES: dict[str, str] = {
    "chatgpt": "openai_chatgpt",
    "openai": "openai_chatgpt",
    "gpt-4": "openai_chatgpt",
    "gpt4": "openai_chatgpt",
    "claude": "anthropic_claude",
    "anthropic": "anthropic_claude",
    "claude ai": "anthropic_claude",
    "bard": "google_bard",
    "google bard": "google_bard",
    "gemini": "google_gemini",
    "google gemini": "google_gemini",
    "west bengal": "west_bengal",
    "wb": "west_bengal",
    "chief minister": "chief_minister",
    "prime minister": "prime_minister",
    "president": "president",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_consensus(
    extractions: list[dict],
    intent: QueryIntent,
) -> dict[str, object]:
    """Build a deterministic consensus from multiple source extractions.

    Each item in *extractions* should have keys: "answer", "source_url", "title".

    Returns a dict with:
      - "answer": str — the consensus answer
      - "agreed_facts": list[str] — facts present in 2+ sources
      - "disputed_facts": list[str] — facts in only 1 source
      - "source_count": int
    """
    if not extractions:
        return {"answer": "", "agreed_facts": [], "disputed_facts": [], "source_count": 0}

    if len(extractions) == 1:
        answer = str(extractions[0].get("answer", ""))
        return {"answer": answer, "agreed_facts": [answer], "disputed_facts": [], "source_count": 1}

    # Step 1 — Extract key facts from each source.
    source_facts: list[dict] = []
    for ext in extractions:
        facts = _extract_key_facts(str(ext.get("answer", "")), intent)
        source_facts.append({
            "facts": facts,
            "source": ext.get("source_url", ""),
            "title": ext.get("title", ""),
        })

    # Step 2 — Find agreed facts (present in 2+ sources).
    agreed_facts = _find_agreed_facts(source_facts)

    # Step 3 — Find disputed facts (only 1 source).
    disputed_facts = _find_disputed_facts(source_facts)

    # Step 4 — Build answer from agreed facts.
    if agreed_facts:
        answer = ". ".join(agreed_facts)
    else:
        # Fallback: use highest-confidence source.
        best = max(extractions, key=lambda x: float(x.get("confidence", 0)))
        answer = str(best.get("answer", ""))

    # Step 5 — Add disagreement note if needed.
    if disputed_facts:
        note = "Note: some sources differ on " + ", ".join(disputed_facts[:2])
        answer = f"{answer} {note}"

    log_event(
        logger,
        "consensus_generated",
        source="consensus",
        success=True,
        source_count=len(extractions),
        agreed_fact_count=len(agreed_facts),
        disputed_fact_count=len(disputed_facts),
    )

    return {
        "answer": answer,
        "agreed_facts": agreed_facts,
        "disputed_facts": disputed_facts,
        "source_count": len(extractions),
    }


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------
def _extract_key_facts(summary: str, intent: QueryIntent) -> list[str]:
    """Extract factual claims from a summary using local heuristics."""
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    facts: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or len(sentence) < 20:
            continue
        sentence_lower = sentence.lower()

        # Keep sentences containing required entities.
        if any(e.lower() in sentence_lower for e in (intent.entities or [])):
            facts.append(sentence)
            continue

        # Keep factual assertion sentences.
        assertion_markers = ("is ", "are ", "was ", "were ", "has ", "located", "capital", "population", "currency")
        if any(marker in sentence_lower for marker in assertion_markers):
            facts.append(sentence)

    return facts


# ---------------------------------------------------------------------------
# Agreement detection using normalized comparison
# ---------------------------------------------------------------------------
def _normalize_for_comparison(text: str) -> list[str]:
    """Normalize text: lowercase, remove stopwords, stem."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    normalized: list[str] = []
    for word in words:
        if word in _STOP_WORDS or len(word) <= 2:
            continue
        # Entity alias normalization.
        word = _ENTITY_ALIASES.get(word, word)
        # Stem.
        stemmed = word
        for suffix, replacement in _STEM_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                stemmed = word[:-len(suffix)] + replacement
                break
        normalized.append(stemmed)
    return normalized


def _normalized_agreement_score(facts_a: list[str], facts_b: list[str]) -> float:
    """Compare two fact sets after normalization. Returns 0.0-1.0."""
    tokens_a: list[str] = []
    for fact in facts_a:
        tokens_a.extend(_normalize_for_comparison(fact))
    tokens_b: list[str] = []
    for fact in facts_b:
        tokens_b.extend(_normalize_for_comparison(fact))

    if not tokens_a or not tokens_b:
        return 0.0

    counter_a = Counter(tokens_a)
    counter_b = Counter(tokens_b)

    intersection = sum((counter_a & counter_b).values())
    magnitude = (sum(counter_a.values()) * sum(counter_b.values())) ** 0.5
    return intersection / magnitude if magnitude > 0 else 0.0


def _find_agreed_facts(source_facts: list[dict]) -> list[str]:
    """Find facts present in 2+ sources using normalized comparison."""
    if len(source_facts) < 2:
        return []

    agreed: list[str] = []
    seen: set[int] = set()

    for i in range(len(source_facts)):
        for j in range(i + 1, len(source_facts)):
            score = _normalized_agreement_score(
                source_facts[i]["facts"],
                source_facts[j]["facts"],
            )
            if score > 0.45:
                # Sources agree — take the longer/better fact from each.
                combined = source_facts[i]["facts"] + source_facts[j]["facts"]
                best_fact = max(combined, key=len)
                best_hash = hash(best_fact)
                if best_hash not in seen:
                    agreed.append(best_fact)
                    seen.add(best_hash)

    return agreed


def _find_disputed_facts(source_facts: list[dict]) -> list[str]:
    """Find facts that appear in only 1 source."""
    if len(source_facts) < 2:
        return []

    all_facts: list[tuple[str, int]] = []
    for idx, sf in enumerate(source_facts):
        for fact in sf["facts"]:
            all_facts.append((fact, idx))

    disputed: list[str] = []
    for fact, source_idx in all_facts:
        # Check if any other source has a similar fact.
        other_facts = []
        for other_idx, sf in enumerate(source_facts):
            if other_idx != source_idx:
                other_facts.extend(sf["facts"])

        score = _normalized_agreement_score([fact], other_facts)
        if score < 0.3:
            disputed.append(fact)

    # Deduplicate and limit.
    seen: set[str] = set()
    unique_disputed: list[str] = []
    for fact in disputed:
        normalized = fact.lower().strip()
        if normalized not in seen:
            seen.add(normalized)
            unique_disputed.append(fact)
    return unique_disputed[:5]
