"""Search Query Synthesizer — generates optimized search queries from structured
intents and provides topic-aware domain preference lists.

Runs after query normalisation to transform a cleaned user query into a
search-engine-optimised string with the correct template for the detected
query type.
"""

from __future__ import annotations

import re
from datetime import datetime

from august.query_understanding import QueryIntent
from august.utils.logger import get_logger, log_event

logger = get_logger("SearchSynthesizer")

# ---------------------------------------------------------------------------
# Search query templates — specialized by query sub-type.
# ---------------------------------------------------------------------------
SEARCH_TEMPLATES: dict[str, str] = {
    "weather": "{location} weather today",
    "office_holder": "Current {office} of {region}",
    "stock": "{company} stock price today",
    "election": "{region} election results {year}",
    "comparison": "{a} vs {b} comparison {year}",
    "definition": "What is {topic}",
    "dynamic_fact": "{topic} {year}",
    "tutorial": "How to {topic}",
    "research": "{topic} detailed explanation",
    "reasoning": "Why {topic} explained",
    "news": "{topic} latest news {year}",
}

# ---------------------------------------------------------------------------
# Topic-aware preferred domain lists
# ---------------------------------------------------------------------------
TOPIC_DOMAINS: dict[str, list[str]] = {
    "weather": [
        "weather.gov",
        "mausam.gov.in",
        "imd.gov.in",
        "accuweather.com",
        "weather.com",
        "weatherbug.com",
        "bbc.com",
    ],
    "government": [
        "gov.in",
        "gov.uk",
        "usa.gov",
        "wikipedia.org",
        "britannica.com",
        "pib.gov.in",
        "ndtv.com",
        "thehindu.com",
        "india.gov.in",
    ],
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
    names, topic, and the current year. Handles specialized sub-types
    like weather, office_holder, stock, and election.
    """
    if intent.type == "conversation":
        return normalized_query

    year = str(datetime.now().year)
    metadata = getattr(intent, "metadata", {})

    # --- Weather ---
    if intent.type == "dynamic_fact" and metadata.get("topic_category") == "weather":
        locations = metadata.get("locations", [])
        location = locations[0] if locations else _extract_location_from_entities(intent.entities)
        if location:
            synthesized = SEARCH_TEMPLATES["weather"].format(location=location)
            log_event(logger, "search_query_generated", source="search_synthesizer", success=True,
                      original=normalized_query, synthesized=synthesized, query_type="weather")
            return _clean(synthesized)

    # --- Office holder ---
    if intent.type == "dynamic_fact" and metadata.get("offices"):
        offices = metadata.get("offices", [])
        office = offices[0] if offices else ""
        locations = metadata.get("locations", [])
        region = locations[0] if locations else _extract_location_from_entities(intent.entities)
        if office and not region:
            region = _extract_location_from_query(normalized_query)
        if office and region:
            synthesized = SEARCH_TEMPLATES["office_holder"].format(office=office, region=region)
            synthesized += " official"
            log_event(logger, "search_query_generated", source="search_synthesizer", success=True,
                      original=normalized_query, synthesized=synthesized, query_type="office_holder")
            return _clean(synthesized)

    # --- Comparison: use both entities ---
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

    synthesized = _clean(synthesized)

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

    # Weather detection.
    weather_markers = {"weather", "temperature", "rain", "humidity", "wind", "forecast"}
    if any(kw in combined for kw in weather_markers):
        categories.append("weather")

    # Government/office detection.
    office_markers = {"minister", "president", "governor", "parliament", "assembly", "election"}
    if any(kw in combined for kw in office_markers):
        categories.append("government")

    # Dynamic facts often benefit from news sources.
    if intent.type == "dynamic_fact" and "news" not in categories:
        categories.append("news")

    return categories


def _extract_location_from_entities(entities: list[str]) -> str:
    """Extract a location from the entity list."""
    location_entities = {
        "west bengal", "tamil nadu", "karnataka", "kerala",
        "andhra pradesh", "telangana", "maharashtra", "gujarat",
        "rajasthan", "uttar pradesh", "bihar", "jharkhand",
        "odisha", "madhya pradesh", "chhattisgarh", "haryana",
        "punjab", "himachal pradesh", "uttarakhand", "assam",
        "delhi", "india", "united states", "usa", "uk",
    }
    city_entities = {
        "kolkata", "mumbai", "delhi", "new delhi", "bengaluru",
        "bangalore", "chennai", "hyderabad", "pune", "jaipur",
    }
    for entity in entities:
        if entity.lower() in city_entities or entity.lower() in location_entities:
            return entity
    return ""


def _extract_location_from_query(query: str) -> str:
    """Extract a location directly from the raw query text."""
    all_locations = sorted(
        {
            "west bengal", "tamil nadu", "karnataka", "kerala",
            "andhra pradesh", "telangana", "maharashtra", "gujarat",
            "rajasthan", "uttar pradesh", "bihar", "jharkhand",
            "odisha", "madhya pradesh", "chhattisgarh", "haryana",
            "punjab", "himachal pradesh", "uttarakhand", "assam",
            "meghalaya", "manipur", "mizoram", "nagaland", "sikkim",
            "arunachal pradesh", "tripura", "goa", "delhi",
            "india", "united states", "usa", "uk", "united kingdom",
            "china", "japan", "russia", "france", "germany", "australia",
            "canada", "brazil", "south korea", "north korea",
            "kolkata", "mumbai", "new delhi", "bengaluru",
            "bangalore", "chennai", "hyderabad", "pune", "jaipur",
        },
        key=len,
        reverse=True,
    )
    query_lower = query.lower()
    for loc in all_locations:
        if loc in query_lower:
            return loc.title()
    return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
