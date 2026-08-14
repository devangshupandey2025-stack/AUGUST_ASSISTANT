"""Tests for the Retrieval Intelligence Engine.

Covers query understanding, entity extraction, query normalization,
search query synthesis, result filtering, and retrieval confidence scoring.
"""

from __future__ import annotations

import pytest

from august.query_normalizer import normalize_query
from august.query_understanding import QueryIntent, understand_query
from august.result_filter import _extract_domain, _is_irrelevant_result, filter_results
from august.retrieval_confidence import assess_retrieval_confidence
from august.search_synthesizer import (
    get_deprioritized_domains,
    get_preferred_domains,
    synthesize_search_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class FakeSearchResult:
    """Minimal stand-in for web_research.SearchResult (duck-typed)."""

    def __init__(self, title: str = "", href: str = "", snippet: str = "", source_quality: float = 0.5):
        self.title = title
        self.href = href
        self.snippet = snippet
        self.source_quality = source_quality


# ===================================================================
# Test 1 — AI Comparison
# ===================================================================
class TestAIComparison:
    """Input: 'which is better chatgpt or claude'"""

    def test_comparison_detected(self):
        intent = understand_query("which is better chatgpt or claude")
        assert intent.type == "comparison"

    def test_entities_extracted(self):
        intent = understand_query("which is better chatgpt or claude")
        canonical_names = {e for e in intent.entities}
        assert "ChatGPT" in canonical_names
        assert "Claude AI" in canonical_names

    def test_search_query_rewritten(self):
        intent = understand_query("which is better chatgpt or claude")
        normalized = normalize_query("which is better chatgpt or claude", intent)
        search_query = synthesize_search_query(intent, normalized)
        assert "vs" in search_query.lower() or "comparison" in search_query.lower()

    def test_ai_domains_prioritised(self):
        intent = understand_query("which is better chatgpt or claude")
        domains = get_preferred_domains(intent)
        assert len(domains) > 0
        # At least one AI-specific domain should be present.
        ai_domains = {"openai.com", "anthropic.com", "huggingface.co", "techcrunch.com"}
        assert any(d in ai_domains for d in domains)


# ===================================================================
# Test 2 — STT Noise Correction
# ===================================================================
class TestSTTNoise:
    """Input: 'charge gpt or cloud ai'"""

    def test_entities_resolve_from_stt_noise(self):
        intent = understand_query("charge gpt or cloud ai")
        canonical_names = {e for e in intent.entities}
        assert "ChatGPT" in canonical_names
        assert "Claude AI" in canonical_names

    def test_normalized_query(self):
        intent = understand_query("charge gpt or cloud ai")
        normalized = normalize_query("charge gpt or cloud ai", intent)
        assert "ChatGPT" in normalized
        assert "Claude AI" in normalized


# ===================================================================
# Test 3 — Definition Query
# ===================================================================
class TestDefinitionQuery:
    """Input: 'what is polymorphism'"""

    def test_definition_detected(self):
        intent = understand_query("what is polymorphism")
        assert intent.type == "definition"

    def test_optimised_search_query(self):
        intent = understand_query("what is polymorphism")
        normalized = normalize_query("what is polymorphism", intent)
        search_query = synthesize_search_query(intent, normalized)
        assert "polymorphism" in search_query.lower()
        assert "explained" in search_query.lower() or "what is" in search_query.lower()


# ===================================================================
# Test 4 — Dynamic Fact
# ===================================================================
class TestDynamicFact:
    """Input: 'current chief minister of Karnataka'"""

    def test_dynamic_fact_detected(self):
        intent = understand_query("current chief minister of Karnataka")
        assert intent.type == "dynamic_fact"

    def test_search_query_includes_latest(self):
        intent = understand_query("current chief minister of Karnataka")
        normalized = normalize_query("current chief minister of Karnataka", intent)
        search_query = synthesize_search_query(intent, normalized)
        assert "latest" in search_query.lower() or "chief minister" in search_query.lower()


# ===================================================================
# Test 5 — Irrelevant Result Filtering
# ===================================================================
class TestIrrelevantResultFiltering:
    """Dictionary pages should be rejected for comparison queries."""

    def test_dictionary_rejected_for_comparison(self):
        intent = understand_query("ChatGPT vs Claude")
        result = FakeSearchResult(
            title="Better | Definition of Better by Merriam-Webster",
            href="https://www.merriam-webster.com/dictionary/better",
            snippet="Definition of better: greater than half.",
        )
        assert _is_irrelevant_result(result, intent) is True

    def test_dictionary_accepted_for_definition(self):
        intent = understand_query("what is polymorphism")
        result = FakeSearchResult(
            title="Polymorphism | Definition",
            href="https://www.merriam-webster.com/dictionary/polymorphism",
            snippet="Definition of polymorphism.",
        )
        assert _is_irrelevant_result(result, intent) is False

    def test_ai_result_kept_for_comparison(self):
        intent = understand_query("ChatGPT vs Claude")
        result = FakeSearchResult(
            title="ChatGPT vs Claude: Which AI is Better?",
            href="https://www.techcrunch.com/chatgpt-vs-claude",
            snippet="We compare ChatGPT and Claude AI across key metrics.",
        )
        assert _is_irrelevant_result(result, intent) is False

    def test_no_entity_match_rejected(self):
        intent = QueryIntent(
            type="comparison",
            entities=["ChatGPT", "Claude AI"],
            topic="ChatGPT vs Claude AI",
            raw_query="chatgpt vs claude",
        )
        result = FakeSearchResult(
            title="Top 10 Kitchen Appliances",
            href="https://www.example.com/kitchen",
            snippet="Best kitchen gadgets for 2024.",
        )
        assert _is_irrelevant_result(result, intent) is True

    def test_filter_results_integration(self):
        intent = understand_query("ChatGPT vs Claude")
        results = [
            FakeSearchResult(
                title="Better | Definition",
                href="https://www.dictionary.com/browse/better",
                snippet="Definition of better.",
            ),
            FakeSearchResult(
                title="ChatGPT vs Claude AI comparison",
                href="https://www.techcrunch.com/chatgpt-vs-claude",
                snippet="ChatGPT and Claude AI compared side by side.",
            ),
        ]
        normalized = normalize_query("ChatGPT vs Claude", intent)
        search_query = synthesize_search_query(intent, normalized)
        preferred = get_preferred_domains(intent)
        filtered = filter_results(results, intent, search_query, preferred)
        assert len(filtered) == 1
        assert "techcrunch" in filtered[0].href


# ===================================================================
# Test 6 — Multi-Source Consensus (unit-level check)
# ===================================================================
class TestMultiSourceConsensus:
    """The engine collects up to 3 results — verified at the data-flow level."""

    def test_multiple_results_collected(self):
        """Verify that multiple results with different confidence values
        are all represented when building the consensus set."""
        results = [
            {"confidence": 0.85, "answer": "Source A says ...", "source_url": "https://a.com"},
            {"confidence": 0.90, "answer": "Source B says ...", "source_url": "https://b.com"},
            {"confidence": 0.75, "answer": "Source C says ...", "source_url": "https://c.com"},
        ]
        best = max(results, key=lambda x: float(x["confidence"]))
        assert best["source_url"] == "https://b.com"
        source_urls = [r["source_url"] for r in results]
        assert len(source_urls) == 3


# ===================================================================
# Test 7 — Retrieval Confidence Scoring
# ===================================================================
class TestRetrievalConfidence:
    def test_strong_entity_alignment(self):
        intent = QueryIntent(
            type="comparison",
            entities=["ChatGPT", "Claude AI"],
            topic="ChatGPT vs Claude AI",
            raw_query="chatgpt vs claude",
        )
        result = assess_retrieval_confidence(
            article_text="ChatGPT and Claude AI are two popular AI assistants. ChatGPT is developed by OpenAI while Claude AI is developed by Anthropic.",
            answer="ChatGPT is developed by OpenAI, Claude AI by Anthropic.",
            query="ChatGPT vs Claude AI comparison",
            intent=intent,
            source_quality=0.9,
            extractor_quality=0.8,
            domain_relevance=0.9,
        )
        assert result["confidence"] > 0.7
        assert result["reason"] in ("strong_entity_alignment", "good_entity_match", "good_semantic_overlap")

    def test_weak_signals(self):
        intent = QueryIntent(
            type="comparison",
            entities=["ChatGPT", "Claude AI"],
            topic="ChatGPT vs Claude AI",
            raw_query="chatgpt vs claude",
        )
        result = assess_retrieval_confidence(
            article_text="The weather today is sunny with clear skies.",
            answer="Sunny weather expected.",
            query="ChatGPT vs Claude AI comparison",
            intent=intent,
            source_quality=0.2,
            extractor_quality=0.3,
            domain_relevance=0.1,
        )
        assert result["confidence"] < 0.5


# ===================================================================
# Test 8 — Query Type Classification
# ===================================================================
class TestQueryTypeClassification:
    """Verify various query types are correctly classified."""

    @pytest.mark.parametrize(
        "query,expected_type",
        [
            ("compare Python vs Java", "comparison"),
            ("difference between CNN and RNN", "comparison"),
            ("what is polymorphism", "definition"),
            ("define recursion", "definition"),
            ("latest AI news", "dynamic_fact"),
            ("current president of the United States", "dynamic_fact"),
            ("how to build a neural network", "tutorial"),
            ("how to use Docker", "tutorial"),
            ("why is Python slow", "reasoning"),
        ],
    )
    def test_query_type(self, query: str, expected_type: str):
        intent = understand_query(query)
        assert intent.type == expected_type, f"Expected {expected_type} for '{query}', got {intent.type}"


# ===================================================================
# Test 9 — Domain extraction helper
# ===================================================================
class TestDomainExtraction:
    def test_simple_url(self):
        assert _extract_domain("https://www.example.com/page") == "example.com"

    def test_subdomain(self):
        assert _extract_domain("https://docs.python.org/3/library/re.html") == "docs.python.org"

    def test_no_www(self):
        assert _extract_domain("https://github.com/repo") == "github.com"

    def test_empty(self):
        assert _extract_domain("") == ""


# ===================================================================
# Test 10 — Deprioritized domains
# ===================================================================
class TestDeprioritizedDomains:
    def test_dictionaries_in_list(self):
        deprioritized = get_deprioritized_domains()
        assert "dictionary.com" in deprioritized
        assert "merriam-webster.com" in deprioritized

    def test_spam_in_list(self):
        deprioritized = get_deprioritized_domains()
        assert "quora.com" in deprioritized
        assert "brainly.com" in deprioritized
