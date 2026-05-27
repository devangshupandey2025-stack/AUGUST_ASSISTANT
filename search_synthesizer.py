"""Search Query Synthesizer — generates optimized search queries from structured
intents and provides topic-aware domain preference lists.

Runs after query normalisation to transform a cleaned user query into a
search-engine-optimised string with the correct template for the detected
query type.
"""

from __future__ import annotations

import re
from datetime import datetime

from query_understanding import QueryIntent
from utils.logger import get_logger, log_event

logger = get_logger("SearchSynthesizer")

# ---------------------------------------------------------------------------
# Search query templates — {a}, {b}, {topic}, {year} are filled at runtime.
# ---------------------------------------------------------------------------
SEARCH_TEMPLATES: dict[str, str] = {
    "comparison": "{a} vs {b} comparison {year}",
    "definition": "what is {topic} explained",
    "dynamic_fact": "{topic} latest {year}",
    "tutorial": "how to {topic} step by step",
    "research": "{topic} detailed explanation",
    "reasoning": "why {topic} explained",
}

# ---------------------------------------------------------------------------
# Topic-aware preferred domain lists
# ---------------------------------------------------------------------------
TOPIC_DOMAINS: dict[str, list[str]] = {
    "ai": [
        "openai.com",
        "anthropic.com",
        "huggingface.co",
        "github.com",
        "techcrunch.com",
        "arxiv.org",
        "towardsdatascience.com",
        "analyticsindiamag.com",
        "theverge.com",
        "arstechnica.com",
    ],
    "programming": [
        "stackoverflow.com",
        "github.com",
        "docs.python.org",
        "developer.mozilla.org",
        "geeksforgeeks.org",
        "realpython.com",
        "dev.to",
        "medium.com",
    ],
    "news": [
        "reuters.com",
        "bbc.com",
        "bbc.co.uk",
        "apnews.com",
        "techcrunch.com",
        "theverge.com",
        "ndtv.com",
        "thehindu.com",
    ],
    "medical": [
        "mayoclinic.org",
        "who.int",
        "ncbi.nlm.nih.gov",
        "pubmed.ncbi.nlm.nih.gov",
        "cdc.gov",
        "webmd.com",
        "healthline.com",
    ],
    "science": [
        "nature.com",
        "sciencedirect.com",
        "arxiv.org",
        "ncbi.nlm.nih.gov",
    ],
    "education": [
        "khanacademy.org",
        "mit.edu",
        "stanford.edu",
        "harvard.edu",
        "coursera.org",
    ],
}

# ---------------------------------------------------------------------------
# Domains to deprioritise — dictionaries, spam blogs, SEO farms.
# ---------------------------------------------------------------------------
LOW_RELEVANCE_DOMAINS: tuple[str, ...] = (
    "dictionary.com",
    "merriam-webster.com",
    "cambridge.org",
    "thefreedictionary.com",
    "wiktionary.org",
    "dictionary.cambridge.org",
    "oxfordlearnersdictionaries.com",
    "quora.com",
    "answers.com",
    "brainly.com",
    "chegg.com",
    "coursehero.com",
    "wikihow.com",
    "ehow.com",
    "about.com",
)

# ---------------------------------------------------------------------------
# Entity markers for topic detection
# ---------------------------------------------------------------------------
_AI_ENTITY_MARKERS: set[str] = {
    "ChatGPT", "GPT-4", "GPT-4o", "Claude AI", "Google Bard",
    "Google Gemini", "Copilot", "GitHub Copilot", "Midjourney",
    "DALL-E", "Stable Diffusion", "LLaMA", "OpenAI", "Anthropic",
    "Hugging Face", "Perplexity AI", "Meta AI", "TensorFlow",
    "PyTorch", "Neural Network", "Machine Learning", "Deep Learning",
    "Transformer", "BERT", "LSTM",
    "CNN (Convolutional Neural Network)",
    "RNN (Recurrent Neural Network)",
    "NLP (Natural Language Processing)",
    "Computer Vision",
}

_PROGRAMMING_MARKERS: set[str] = {
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#",
    "Rust", "Go", "Kotlin", "Swift", "Ruby",
    "React", "Angular", "Vue.js", "Node.js", "Docker", "Kubernetes",
}

# Lowercase keyword sets for topic-text matching.
_AI_KEYWORDS: set[str] = {
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "nlp", "llm", "language model", "chatbot",
    "generative ai", "computer vision",
}

_PROGRAMMING_KEYWORDS: set[str] = {
    "programming", "coding", "software", "developer", "algorithm",
    "data structure", "framework", "library", "api", "backend",
    "frontend", "fullstack", "database", "sql",
}

_NEWS_KEYWORDS: set[str] = {
    "news", "latest", "today", "breaking", "current", "update",
    "election", "politics", "government",
}

_MEDICAL_KEYWORDS: set[str] = {
    "medical", "health", "disease", "symptom", "treatment",
    "medicine", "doctor", "hospital", "diagnosis",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def synthesize_search_query(intent: QueryIntent, normalized_query: str) -> str:
    """Build an optimised search query from the structured *intent*.

    Uses the appropriate template for the query type and fills in entity
    names, topic, and the current year.
    """
    if intent.type == "conversation":
        return normalized_query

    year = str(datetime.now().year)

    # Comparison: use both entities.
    if intent.type == "comparison" and len(intent.entities) >= 2:
        synthesized = SEARCH_TEMPLATES["comparison"].format(
            a=intent.entities[0],
            b=intent.entities[1],
            year=year,
        )
    else:
        template = SEARCH_TEMPLATES.get(intent.type)
        topic = intent.topic or normalized_query
        if template:
            synthesized = template.format(topic=topic, year=year)
        else:
            synthesized = normalized_query

    synthesized = re.sub(r"\s+", " ", synthesized).strip()

    log_event(
        logger,
        "search_query_generated",
        source="search_synthesizer",
        success=True,
        original=normalized_query,
        synthesized=synthesized,
        query_type=intent.type,
    )
    return synthesized


def get_preferred_domains(intent: QueryIntent) -> list[str]:
    """Return a list of preferred domains appropriate for the query topic."""
    categories = _detect_topic_category(intent)
    domains: list[str] = []
    seen: set[str] = set()
    for category in categories:
        for domain in TOPIC_DOMAINS.get(category, []):
            if domain not in seen:
                domains.append(domain)
                seen.add(domain)
    return domains


def get_deprioritized_domains() -> tuple[str, ...]:
    """Return the tuple of domains that should be ranked lower."""
    return LOW_RELEVANCE_DOMAINS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _detect_topic_category(intent: QueryIntent) -> list[str]:
    """Detect which topic categories apply based on entities and topic text."""
    categories: list[str] = []

    entity_set = set(intent.entities)

    # Check entity markers.
    if entity_set & _AI_ENTITY_MARKERS:
        categories.append("ai")
    if entity_set & _PROGRAMMING_MARKERS:
        categories.append("programming")

    # Check topic text for keyword matches.
    topic_lower = (intent.topic or "").lower()
    raw_lower = (intent.raw_query or "").lower()
    combined = f"{topic_lower} {raw_lower}"

    if not categories or "ai" not in categories:
        if any(kw in combined for kw in _AI_KEYWORDS):
            categories.append("ai")
    if "programming" not in categories:
        if any(kw in combined for kw in _PROGRAMMING_KEYWORDS):
            categories.append("programming")
    if any(kw in combined for kw in _NEWS_KEYWORDS):
        categories.append("news")
    if any(kw in combined for kw in _MEDICAL_KEYWORDS):
        categories.append("medical")

    # Dynamic facts often benefit from news sources.
    if intent.type == "dynamic_fact" and "news" not in categories:
        categories.append("news")

    return categories
