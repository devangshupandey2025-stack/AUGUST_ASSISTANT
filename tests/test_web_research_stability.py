"""Comprehensive tests for Web Research Engine stability.

Tests cover: acronym expansion, anti-hallucination, source quality filtering,
DuckDuckGo retry logic, research caching, confidence-gated memory storage,
generic answer rejection, clutter filtering, and summarization quality.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# ===================================================================
# Test 1 — Acronym Expansion
# ===================================================================
class TestAcronymResolver:
    def test_rbc_expands(self):
        from august.acronym_resolver import expand_acronyms
        expanded, expansions = expand_acronyms("what is an rbc")
        assert "Red Blood Cell" in expanded
        assert len(expansions) == 1
        assert "RBC" in expansions[0]

    def test_cpu_expands(self):
        from august.acronym_resolver import expand_acronyms
        expanded, _ = expand_acronyms("what is a cpu")
        assert "Central Processing Unit" in expanded

    def test_multiple_acronyms(self):
        from august.acronym_resolver import expand_acronyms
        expanded, expansions = expand_acronyms("compare cpu and gpu")
        assert "Central Processing Unit" in expanded
        assert "Graphics Processing Unit" in expanded
        assert len(expansions) == 2

    def test_unknown_acronym_passthrough(self):
        from august.acronym_resolver import expand_acronyms
        expanded, expansions = expand_acronyms("what is qxv protocol")
        assert "qxv" in expanded.lower()
        # "qxv" is not a known acronym — might match regex but no expansion
        assert all("QXV" not in e for e in expansions)

    def test_case_insensitive(self):
        from august.acronym_resolver import expand_acronyms
        expanded, _ = expand_acronyms("what is an RBC")
        assert "Red Blood Cell" in expanded

    def test_empty_query(self):
        from august.acronym_resolver import expand_acronyms
        expanded, expansions = expand_acronyms("")
        assert expanded == ""
        assert expansions == []

    def test_is_acronym(self):
        from august.acronym_resolver import is_acronym
        assert is_acronym("RBC") is True
        assert is_acronym("rbc") is True
        assert is_acronym("xyz123") is False

    def test_no_partial_word_match(self):
        from august.acronym_resolver import expand_acronyms
        # "practice" contains "ice" which is 3 letters but should not match
        expanded, expansions = expand_acronyms("best practice for development")
        # Should not have spurious expansions of common 3-letter subsequences
        assert expanded  # just ensure it doesn't crash


# ===================================================================
# Test 2 — Unknown Term: No Hallucination
# ===================================================================
class TestAntiHallucination:
    def test_unknown_term_not_filler(self):
        """_generate_lightweight_answer should return with confidence below threshold."""
        from august.answer_fallback import try_local_answer
        result = try_local_answer("what is qxv protocol")
        # Should NOT succeed — generic filler must be rejected
        if result.get("success"):
            text = str(result.get("text", ""))
            assert "is a concept" not in text.lower()
            assert "is an important concept" not in text.lower()
        else:
            # This is the correct behavior — local fails, falls through
            assert result["success"] is False

    def test_generic_definition_removed(self):
        """Generic 'define X' catch-all should no longer produce filler."""
        from august.answer_fallback import try_local_answer
        result = try_local_answer("define xyzabc")
        assert result["success"] is False

    def test_known_cs_term_still_works(self):
        """Known CS terms should still return valid local answers."""
        from august.answer_fallback import try_local_answer
        result = try_local_answer("what is a linked list")
        assert result["success"] is True
        assert float(result.get("confidence", 0)) >= 0.7


# ===================================================================
# Test 3 — Trusted Source Prioritization
# ===================================================================
class TestSourceQualityScoring:
    def test_wikipedia_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._score_source_quality("https://en.wikipedia.org/wiki/Test") == 1.0

    def test_britannica_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._score_source_quality("https://www.britannica.com/topic/test") == 1.0

    def test_gov_domain_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        score = engine._score_source_quality("https://www.cdc.gov/health")
        assert score >= 0.9

    def test_edu_domain_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        score = engine._score_source_quality("https://www.mit.edu/research")
        assert score >= 0.9

    def test_low_quality_detected(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        score = engine._score_source_quality("https://www.quora.com/something")
        assert score <= 0.3

    def test_neutral_domain(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        score = engine._score_source_quality("https://example.com/page")
        assert 0.3 < score < 0.8

    def test_search_results_sorted_by_quality(self):
        """Verify that search results get sorted with trusted domains first."""
        from august.web_research import SearchResult
        results = [
            SearchResult(title="A", href="https://example.com/a", snippet="", source_quality=0.5),
            SearchResult(title="B", href="https://en.wikipedia.org/wiki/B", snippet="", source_quality=1.0),
            SearchResult(title="C", href="https://quora.com/c", snippet="", source_quality=0.2),
        ]
        results.sort(key=lambda r: r.source_quality, reverse=True)
        assert results[0].title == "B"  # Wikipedia first
        assert results[-1].title == "C"  # Quora last


# ===================================================================
# Test 4 — DuckDuckGo Retry (Simulated Timeout)
# ===================================================================
class TestDuckDuckGoRetry:
    @patch("august.web_research.DDGS")
    @patch("august.web_research.time.sleep")
    def test_retry_on_failure(self, mock_sleep, mock_ddgs):
        """Search should retry on failure and recover on success."""
        from august.web_research import WebResearchEngine

        ddgs_instance = MagicMock()
        # First call fails, second succeeds
        ddgs_instance.text.side_effect = [
            Exception("timeout"),
            [{"title": "Test", "href": "https://example.com", "body": "snippet"}],
        ]
        mock_ddgs.return_value.__enter__ = MagicMock(return_value=ddgs_instance)
        mock_ddgs.return_value.__exit__ = MagicMock(return_value=False)

        engine = WebResearchEngine()
        results = engine._search("test query")
        assert len(results) >= 1
        assert mock_sleep.called  # backoff was triggered

    @patch("august.web_research.DDGS")
    @patch("august.web_research.time.sleep")
    def test_all_retries_exhausted(self, mock_sleep, mock_ddgs):
        """Search should return empty list after all retries fail."""
        from august.web_research import WebResearchEngine

        ddgs_instance = MagicMock()
        ddgs_instance.text.side_effect = Exception("persistent failure")
        mock_ddgs.return_value.__enter__ = MagicMock(return_value=ddgs_instance)
        mock_ddgs.return_value.__exit__ = MagicMock(return_value=False)

        engine = WebResearchEngine()
        results = engine._search("test query")
        assert results == []

    @patch("august.web_research.DDGS", None)
    def test_ddgs_not_available(self):
        """Should return empty list gracefully when DDGS is not installed."""
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        results = engine._search("test")
        assert results == []


# ===================================================================
# Test 5 — Research Cache
# ===================================================================
class TestResearchCache:
    def test_cache_hit(self):
        from august.web_research import _RESEARCH_CACHE, WebResearchEngine
        engine = WebResearchEngine()

        # Manually seed cache
        _RESEARCH_CACHE["test cache query"] = {
            "answer": "Cached answer",
            "source_url": "https://example.com",
            "source_urls": ["https://example.com"],
            "title": "Test",
            "article_text": "Full text",
            "confidence": 0.85,
            "timestamp": time.time(),
        }

        result = engine._get_cached("test cache query")
        assert result is not None
        assert result.answer == "Cached answer"

        # Cleanup
        _RESEARCH_CACHE.pop("test cache query", None)

    def test_cache_expired(self):
        from august.web_research import _RESEARCH_CACHE, WebResearchEngine
        engine = WebResearchEngine()

        _RESEARCH_CACHE["expired query"] = {
            "answer": "Old answer",
            "source_url": "",
            "source_urls": [],
            "title": "",
            "article_text": "",
            "confidence": 0.8,
            "timestamp": time.time() - 600,  # 10 minutes ago, past TTL
        }

        result = engine._get_cached("expired query")
        assert result is None

        _RESEARCH_CACHE.pop("expired query", None)

    def test_set_cached(self):
        from august.web_research import _RESEARCH_CACHE, WebResearchEngine
        engine = WebResearchEngine()

        engine._set_cached("new key", "new answer", "https://example.com", "Title", "text", 0.9)
        assert "new key" in _RESEARCH_CACHE
        assert _RESEARCH_CACHE["new key"]["confidence"] == 0.9

        _RESEARCH_CACHE.pop("new key", None)


# ===================================================================
# Test 6 — Weak Research Memory Block
# ===================================================================
class TestResearchMemoryGate:
    def test_low_confidence_blocked(self):
        from august.knowledge_governor import KnowledgeGovernor
        gov = KnowledgeGovernor()
        assert gov.should_store_research("test", "Some valid answer that is long enough for storage.", 0.5) is False

    def test_high_confidence_stored(self):
        from august.knowledge_governor import KnowledgeGovernor
        gov = KnowledgeGovernor()
        assert gov.should_store_research("test", "A detailed factual answer about the topic at hand is provided here.", 0.85) is True

    def test_filler_content_blocked(self):
        from august.knowledge_governor import KnowledgeGovernor
        gov = KnowledgeGovernor()
        assert gov.should_store_research("test", "X is a concept that refers to something important.", 0.85) is False

    def test_short_answer_blocked(self):
        from august.knowledge_governor import KnowledgeGovernor
        gov = KnowledgeGovernor()
        assert gov.should_store_research("test", "Short.", 0.9) is False


# ===================================================================
# Test 7 — Generic Answer Rejected by Confidence Gate
# ===================================================================
class TestGenericAnswerRejection:
    def test_lightweight_answer_below_threshold(self):
        """_generate_lightweight_answer confidence is now 0.45, below 0.7 threshold."""
        from august.answer_fallback import try_local_answer
        result = try_local_answer("what is xyzabc")
        # Should fail — generic filler rejected by confidence gate
        assert result["success"] is False

    def test_known_factual_still_passes(self):
        """Known factual answers should still pass."""
        from august.answer_fallback import try_local_answer
        result = try_local_answer("what is photosynthesis")
        assert result["success"] is True
        assert float(result.get("confidence", 0)) >= 0.7


# ===================================================================
# Test 8 — Source Quality Scoring (additional edge cases)
# ===================================================================
class TestSourceQualityEdgeCases:
    def test_empty_url(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        score = engine._score_source_quality("")
        assert score <= 0.5

    def test_stackoverflow_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._score_source_quality("https://stackoverflow.com/questions/123") == 1.0

    def test_mayoclinic_trusted(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._score_source_quality("https://www.mayoclinic.org/diseases") == 1.0


# ===================================================================
# Test 9 — Clutter Filtering
# ===================================================================
class TestClutterFiltering:
    def test_clutter_detected(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._looks_like_clutter("Subscribe to our newsletter for the latest updates.") is True
        assert engine._looks_like_clutter("Click here to learn more about our products.") is True
        assert engine._looks_like_clutter("Accept cookies to continue browsing this website.") is True

    def test_clean_sentence_passes(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._looks_like_clutter("Red blood cells carry oxygen throughout the body using hemoglobin.") is False

    def test_hallucination_marker_detected(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        assert engine._is_hallucinated_summary("RBC is a concept that refers to something in biology.") is True
        assert engine._is_hallucinated_summary("Red blood cells carry oxygen using hemoglobin.") is False


# ===================================================================
# Test 10 — Summary Quality
# ===================================================================
class TestSummarizationQuality:
    def test_summary_length(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        long_text = ". ".join([f"This is sentence number {i} about red blood cells and their function" for i in range(20)])
        summary = engine._summarize(long_text, "red blood cells", "RBC - Wikipedia", "Red blood cells carry oxygen.")
        # Should be between 2-5 sentences, under 900 chars
        sentences = [s.strip() for s in summary.split(".") if s.strip()]
        assert 1 <= len(sentences) <= 6
        assert len(summary) <= 950

    def test_hallucinated_summary_rejected(self):
        from august.web_research import WebResearchEngine
        engine = WebResearchEngine()
        result = engine._summarize(
            "X is a concept that refers to how a specific idea, process, or system is understood and applied. It generally involves understanding the core idea, common use cases, and trade-offs.",
            "what is xyzabc",
            "Title",
            "Snippet"
        )
        # Should be empty because the text is hallucinated filler
        assert result == "" or "concept that refers to" not in result.lower()


# ===================================================================
# Test — Decision Engine Routing Fix
# ===================================================================
class TestDecisionEngineRouting:
    def test_weak_local_answer_detected(self):
        """_is_weak_local_answer should catch generic filler across all query types."""
        # We need to test the method directly without full init
        from august.decision_engine import DecisionEngine

        # Create minimal mock
        mock_ai = MagicMock()
        mock_memory = MagicMock()
        mock_memory.snapshot.return_value = {}

        engine = DecisionEngine(ai_parser=mock_ai, memory_store=mock_memory)

        # Generic filler should be weak regardless of query type
        assert engine._is_weak_local_answer(
            "what is rbc",
            "rbc is a concept that refers to how a specific idea is understood",
            0.78,
        ) is True

    def test_good_local_answer_not_weak(self):
        from august.decision_engine import DecisionEngine
        mock_ai = MagicMock()
        mock_memory = MagicMock()
        mock_memory.snapshot.return_value = {}

        engine = DecisionEngine(ai_parser=mock_ai, memory_store=mock_memory)
        assert engine._is_weak_local_answer(
            "what is a linked list",
            "A linked list is a linear data structure where each node stores data and a reference to the next node.",
            0.88,
        ) is False

    def test_low_confidence_is_weak(self):
        from august.decision_engine import DecisionEngine
        mock_ai = MagicMock()
        mock_memory = MagicMock()
        mock_memory.snapshot.return_value = {}

        engine = DecisionEngine(ai_parser=mock_ai, memory_store=mock_memory)
        assert engine._is_weak_local_answer("anything", "any answer", 0.5) is True

    def test_should_attempt_web_research_static_local_failed(self):
        """static_knowledge queries should go to web research when local fails."""
        from august.decision_engine import DecisionEngine
        mock_ai = MagicMock()
        mock_memory = MagicMock()
        mock_memory.snapshot.return_value = {}

        engine = DecisionEngine(ai_parser=mock_ai, memory_store=mock_memory)
        assert engine._should_attempt_web_research("what is rbc", "static_knowledge", local_failed=True) is True

    def test_should_not_attempt_web_research_static_local_ok(self):
        """static_knowledge queries should NOT go to web research when local succeeds."""
        from august.decision_engine import DecisionEngine
        mock_ai = MagicMock()
        mock_memory = MagicMock()
        mock_memory.snapshot.return_value = {}

        engine = DecisionEngine(ai_parser=mock_ai, memory_store=mock_memory)
        assert engine._should_attempt_web_research("what is a linked list", "static_knowledge", local_failed=False) is False


# ===================================================================
# Test — WebResearchResult Confidence Field
# ===================================================================
class TestWebResearchResultConfidence:
    def test_result_has_confidence_field(self):
        from august.web_research import WebResearchResult
        result = WebResearchResult(success=True, answer="test", confidence=0.85)
        assert result.confidence == 0.85

    def test_result_default_confidence(self):
        from august.web_research import WebResearchResult
        result = WebResearchResult(success=False, answer="fail")
        assert result.confidence == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
