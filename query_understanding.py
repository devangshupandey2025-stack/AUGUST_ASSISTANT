"""Query Understanding Engine — semantic query classification and entity extraction.

Classifies user queries into structured intents (comparison, definition, etc.)
and extracts named entities with fuzzy alias resolution to handle speech-to-text
noise.  This module runs BEFORE web search to ensure the right search strategy
is selected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fuzzywuzzy import fuzz

from utils.logger import get_logger, log_event

logger = get_logger("QueryUnderstanding")

# ---------------------------------------------------------------------------
# Query types
# ---------------------------------------------------------------------------
QUERY_TYPES = {
    "comparison",
    "definition",
    "dynamic_fact",
    "tutorial",
    "research",
    "reasoning",
    "conversation",
}

# ---------------------------------------------------------------------------
# Entity alias map — STT-corrupted names → canonical forms
# ---------------------------------------------------------------------------
ENTITY_ALIASES: dict[str, str] = {
    # AI tools
    "charge gpt": "ChatGPT",
    "chat gpt": "ChatGPT",
    "chatgpt": "ChatGPT",
    "gpt": "ChatGPT",
    "gpt 4": "GPT-4",
    "gpt4": "GPT-4",
    "gpt 4o": "GPT-4o",
    "gpt4o": "GPT-4o",
    "cloud ai": "Claude AI",
    "cloud": "Claude AI",
    "claude": "Claude AI",
    "claude ai": "Claude AI",
    "claud": "Claude AI",
    "bard": "Google Bard",
    "gemini": "Google Gemini",
    "gemini ai": "Google Gemini",
    "co pilot": "Copilot",
    "copilot": "Copilot",
    "github copilot": "GitHub Copilot",
    "mid journey": "Midjourney",
    "midjourney": "Midjourney",
    "dall e": "DALL-E",
    "dolly": "DALL-E",
    "stable diffusion": "Stable Diffusion",
    "llama": "LLaMA",
    "meta ai": "Meta AI",
    "perplexity": "Perplexity AI",
    "hugging face": "Hugging Face",
    "huggingface": "Hugging Face",
    "open ai": "OpenAI",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    # Programming languages
    "python": "Python",
    "java": "Java",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "c++": "C++",
    "c plus plus": "C++",
    "c sharp": "C#",
    "c#": "C#",
    "rust": "Rust",
    "golang": "Go",
    "go lang": "Go",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "ruby": "Ruby",
    # Technologies
    "react": "React",
    "react js": "React",
    "angular": "Angular",
    "vue": "Vue.js",
    "vue js": "Vue.js",
    "node js": "Node.js",
    "nodejs": "Node.js",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "py torch": "PyTorch",
    # Companies
    "google": "Google",
    "microsoft": "Microsoft",
    "apple": "Apple",
    "amazon": "Amazon",
    "meta": "Meta",
    "facebook": "Meta",
    "tesla": "Tesla",
    "nvidia": "NVIDIA",
    # Technical terms
    "cnn": "CNN (Convolutional Neural Network)",
    "rnn": "RNN (Recurrent Neural Network)",
    "lstm": "LSTM",
    "transformer": "Transformer",
    "bert": "BERT",
    "neural network": "Neural Network",
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "nlp": "NLP (Natural Language Processing)",
    "computer vision": "Computer Vision",
}

# Pre-build lowercase lookup for O(1) matching.
_ALIAS_LOWER: dict[str, str] = {k.lower(): v for k, v in ENTITY_ALIASES.items()}

# Known entity names grouped by category — used for fuzzy fallback matching.
KNOWN_ENTITIES: dict[str, set[str]] = {
    "ai_tools": {
        "ChatGPT", "GPT-4", "GPT-4o", "Claude AI", "Google Bard",
        "Google Gemini", "Copilot", "GitHub Copilot", "Midjourney",
        "DALL-E", "Stable Diffusion", "LLaMA", "Meta AI",
        "Perplexity AI", "Hugging Face",
    },
    "languages": {
        "Python", "Java", "JavaScript", "TypeScript", "C++", "C#",
        "Rust", "Go", "Kotlin", "Swift", "Ruby",
    },
    "companies": {
        "OpenAI", "Anthropic", "Google", "Microsoft", "Apple",
        "Amazon", "Meta", "Tesla", "NVIDIA",
    },
    "technologies": {
        "React", "Angular", "Vue.js", "Node.js", "Docker",
        "Kubernetes", "TensorFlow", "PyTorch",
    },
    "technical_terms": {
        "CNN (Convolutional Neural Network)",
        "RNN (Recurrent Neural Network)",
        "LSTM", "Transformer", "BERT", "Neural Network",
        "Machine Learning", "Deep Learning",
        "NLP (Natural Language Processing)", "Computer Vision",
    },
}

# Flat set of all canonical entity names for reverse-lookup.
_ALL_ENTITIES: set[str] = set()
for _category_entities in KNOWN_ENTITIES.values():
    _ALL_ENTITIES.update(_category_entities)

# Minimum fuzzy match score to accept a token as an entity.
FUZZY_THRESHOLD = 78


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class QueryIntent:
    """Structured representation of a user query's intent."""

    type: str  # One of QUERY_TYPES
    entities: list[str] = field(default_factory=list)
    topic: str = ""
    raw_query: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Comparison patterns (order matters — more specific first)
# ---------------------------------------------------------------------------
_COMPARISON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:compare|comparison)\b", re.IGNORECASE),
    re.compile(r"\bdifference(?:s)?\s+between\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+(?:is|one\s+is)\s+(?:better|best|faster|worse)\b", re.IGNORECASE),
    re.compile(r"\b(\w[\w\s]*?)\s+(?:vs\.?|versus)\s+(\w[\w\s]*)\b", re.IGNORECASE),
    re.compile(r"\b(\w[\w\s]*?)\s+or\s+(\w[\w\s]*?)\s*(?:\?|$)", re.IGNORECASE),
)

_DEFINITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:what\s+is|what's|what\s+are)\b", re.IGNORECASE),
    re.compile(r"^(?:define|explain|meaning\s+of)\b", re.IGNORECASE),
)

_DYNAMIC_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:current|latest|today'?s?|recent|breaking)\b", re.IGNORECASE),
    re.compile(r"\b(?:chief\s+minister|prime\s+minister|president|governor)\b", re.IGNORECASE),
    re.compile(r"\b(?:stock\s+price|share\s+price|election|ranking|score|result)\b", re.IGNORECASE),
    re.compile(r"\b(?:who\s+won|who\s+is\s+the)\b", re.IGNORECASE),
    re.compile(r"\bnews\b", re.IGNORECASE),
)

_TUTORIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^how\s+(?:to|do\s+(?:i|you|we))\b", re.IGNORECASE),
    re.compile(r"\b(?:steps?\s+to|guide\s+(?:for|to)|tutorial)\b", re.IGNORECASE),
)

_RESEARCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:detailed|in[\-\s]?depth|thorough|comprehensive)\b", re.IGNORECASE),
    re.compile(r"\b(?:analysis\s+of|overview\s+of|survey\s+of)\b", re.IGNORECASE),
)

_REASONING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^why\b", re.IGNORECASE),
    re.compile(r"^how\s+does\b", re.IGNORECASE),
    re.compile(r"\b(?:cause|reason\s+behind|internals|architecture)\b", re.IGNORECASE),
)

_CONVERSATION_PATTERNS: tuple[str, ...] = (
    "hi", "hello", "hey", "how are you", "how r u", "what's up",
    "whats up", "thanks", "thank you", "good morning",
    "good afternoon", "good evening", "nice",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def understand_query(query: str) -> QueryIntent:
    """Analyse *query* and return a structured ``QueryIntent``.

    This is the main entry point called by the web research pipeline before
    search begins.
    """
    cleaned = _normalize(query)
    if not cleaned:
        return QueryIntent(type="conversation", raw_query=query, confidence=0.0)

    query_type = _classify_type(cleaned)
    entities = _extract_entities(cleaned)
    topic = _extract_topic(cleaned, query_type)
    confidence = _estimate_confidence(query_type, entities, cleaned)

    intent = QueryIntent(
        type=query_type,
        entities=entities,
        topic=topic,
        raw_query=query,
        confidence=confidence,
    )

    log_event(
        logger,
        "query_understood",
        source="query_understanding",
        success=True,
        query=query,
        query_type=intent.type,
        entities=intent.entities,
        topic=intent.topic,
        confidence=round(intent.confidence, 3),
    )
    return intent


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def _classify_type(cleaned: str) -> str:
    """Classify the query into one of the known QUERY_TYPES."""
    # Check conversational first (fast path).
    if cleaned in _CONVERSATION_PATTERNS or any(cleaned.startswith(p) for p in _CONVERSATION_PATTERNS):
        return "conversation"

    # Comparison (check before definition — "which is better X or Y" is comparison).
    if any(pattern.search(cleaned) for pattern in _COMPARISON_PATTERNS):
        return "comparison"

    # Dynamic fact.
    if any(pattern.search(cleaned) for pattern in _DYNAMIC_FACT_PATTERNS):
        return "dynamic_fact"

    # Tutorial.
    if any(pattern.search(cleaned) for pattern in _TUTORIAL_PATTERNS):
        return "tutorial"

    # Research.
    if any(pattern.search(cleaned) for pattern in _RESEARCH_PATTERNS):
        return "research"

    # Reasoning.
    if any(pattern.search(cleaned) for pattern in _REASONING_PATTERNS):
        return "reasoning"

    # Definition (broadest — keep last among knowledge types).
    if any(pattern.search(cleaned) for pattern in _DEFINITION_PATTERNS):
        return "definition"

    # Fallback: treat any question-like query as research.
    if cleaned.endswith("?") or cleaned.startswith(("what ", "who ", "where ", "when ")):
        return "research"

    return "conversation"


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------
def _extract_entities(cleaned: str) -> list[str]:
    """Extract and resolve named entities from the query text."""
    entities: list[str] = []
    seen_canonical: set[str] = set()

    # ---- Pass 1: multi-word alias matching (longest first) ----
    remaining = cleaned
    sorted_aliases = sorted(_ALIAS_LOWER.keys(), key=len, reverse=True)
    for alias in sorted_aliases:
        if alias in remaining:
            canonical = _ALIAS_LOWER[alias]
            if canonical not in seen_canonical:
                entities.append(canonical)
                seen_canonical.add(canonical)
            remaining = remaining.replace(alias, " ")

    # ---- Pass 2: single-token fuzzy matching on remaining tokens ----
    tokens = [t for t in re.split(r"\s+", remaining.strip()) if len(t) >= 2]
    for token in tokens:
        # Skip common filler words.
        if token in _STOP_WORDS:
            continue
        resolved = _resolve_entity(token)
        if resolved and resolved not in seen_canonical:
            entities.append(resolved)
            seen_canonical.add(resolved)

    if entities:
        log_event(
            logger,
            "entity_extracted",
            source="query_understanding",
            success=True,
            entities=entities,
        )

    return entities


def _resolve_entity(token: str) -> str | None:
    """Try to resolve a single token to a known entity via fuzzy matching."""
    lowered = token.lower()

    # Exact alias hit.
    if lowered in _ALIAS_LOWER:
        return _ALIAS_LOWER[lowered]

    # Fuzzy match against alias keys.
    best_score = 0
    best_match: str | None = None
    for alias_key, canonical in _ALIAS_LOWER.items():
        score = fuzz.ratio(lowered, alias_key)
        if score > best_score and score >= FUZZY_THRESHOLD:
            best_score = score
            best_match = canonical

    if best_match:
        log_event(
            logger,
            "entity_fuzzy_resolved",
            source="query_understanding",
            success=True,
            token=token,
            resolved=best_match,
            score=best_score,
        )
        return best_match

    return None


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------
def _extract_topic(cleaned: str, query_type: str) -> str:
    """Extract the core topic from the query for search template filling."""
    topic = cleaned

    # Strip question prefixes.
    for prefix_pattern in (
        r"^(?:what\s+is|what's|what\s+are)\s+",
        r"^(?:define|explain)\s+",
        r"^(?:how\s+to)\s+",
        r"^(?:who\s+is|who\s+are)\s+",
        r"^(?:why\s+(?:is|are|does|do))\s+",
        r"^(?:how\s+does|how\s+do)\s+",
        r"^(?:which\s+is\s+(?:better|best|faster))\s+",
        r"^(?:tell\s+me\s+about)\s+",
        r"^(?:current|latest)\s+",
    ):
        topic = re.sub(prefix_pattern, "", topic, flags=re.IGNORECASE).strip()

    # Strip trailing question marks and filler.
    topic = re.sub(r"[?!.]+$", "", topic).strip()
    topic = re.sub(r"\b(?:please|for me)\b", "", topic, flags=re.IGNORECASE).strip()
    topic = re.sub(r"\s+", " ", topic).strip()

    return topic


# ---------------------------------------------------------------------------
# Confidence estimation
# ---------------------------------------------------------------------------
def _estimate_confidence(query_type: str, entities: list[str], cleaned: str) -> float:
    """Estimate how confident we are in the classification."""
    if query_type == "conversation":
        return 0.95

    score = 0.5
    # Entity presence boosts confidence.
    if entities:
        score += 0.15 * min(len(entities), 3)
    # Longer queries tend to be more specific.
    word_count = len(cleaned.split())
    if word_count >= 4:
        score += 0.1
    # Comparison with two entities is very confident.
    if query_type == "comparison" and len(entities) >= 2:
        score += 0.15

    return min(score, 0.99)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_STOP_WORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "am", "do", "does", "did", "will", "would", "could", "should",
    "can", "may", "might", "shall", "must", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "from", "into", "through",
    "and", "or", "but", "not", "if", "than", "then", "so", "as",
    "it", "its", "this", "that", "these", "those", "my", "your",
    "his", "her", "our", "their", "me", "him", "us", "them",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "better", "best", "worse", "worst", "more", "most", "less",
    "between", "vs", "versus", "compare", "comparison", "difference",
    "current", "latest", "define", "explain", "tell", "give",
    "ai", "agent", "tool",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
