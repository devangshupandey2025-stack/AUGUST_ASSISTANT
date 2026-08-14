from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from august.providers.base_provider import BaseProvider
from august.providers.provider_result import ProviderResult
from august.providers.provider_router import ProviderRouter
from august.providers.wikipedia_provider import WikipediaProvider
from august.query_understanding import QueryIntent


def _make_intent(
    query_type: str = "definition",
    entities: list[str] | None = None,
    topic: str = "",
    raw_query: str = "",
    metadata: dict | None = None,
) -> QueryIntent:
    return QueryIntent(
        type=query_type,
        entities=entities or [],
        topic=topic or raw_query,
        raw_query=raw_query or topic or "test query",
        confidence=0.5,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# ProviderResult
# ---------------------------------------------------------------------------
class ProviderResultTests(unittest.TestCase):
    def test_default_values(self) -> None:
        r = ProviderResult(success=True)
        self.assertTrue(r.success)
        self.assertEqual(r.provider, "")
        self.assertEqual(r.confidence, 0.0)
        self.assertEqual(r.source, "")
        self.assertEqual(r.title, "")
        self.assertEqual(r.summary, "")
        self.assertEqual(r.raw_text, "")
        self.assertEqual(r.url, "")
        self.assertEqual(r.metadata, {})
        self.assertEqual(r.structured_data, {})

    def test_all_fields(self) -> None:
        r = ProviderResult(
            success=True,
            provider="Wikipedia",
            confidence=0.94,
            source="Wikipedia",
            title="Polymorphism",
            summary="Polymorphism is the provision of a single interface...",
            raw_text="Polymorphism is the provision of a single interface...",
            url="https://en.wikipedia.org/wiki/Polymorphism",
            metadata={"page_id": 12345},
            structured_data={"key": "value"},
        )
        self.assertTrue(r.success)
        self.assertEqual(r.provider, "Wikipedia")
        self.assertEqual(r.confidence, 0.94)
        self.assertEqual(r.source, "Wikipedia")
        self.assertEqual(r.title, "Polymorphism")
        self.assertEqual(r.summary, "Polymorphism is the provision of a single interface...")
        self.assertEqual(r.url, "https://en.wikipedia.org/wiki/Polymorphism")
        self.assertEqual(r.metadata, {"page_id": 12345})
        self.assertEqual(r.structured_data, {"key": "value"})


# ---------------------------------------------------------------------------
# BaseProvider ABC
# ---------------------------------------------------------------------------
class BaseProviderTests(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self) -> None:
        with self.assertRaises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_abstract_methods(self) -> None:
        with self.assertRaises(TypeError):

            class BadProvider(BaseProvider):
                pass

            BadProvider()  # type: ignore[abstract]

    def test_can_subclass_correctly(self) -> None:
        class GoodProvider(BaseProvider):
            def can_handle(self, intent: QueryIntent) -> bool:
                return True

            def fetch(self, intent: QueryIntent) -> ProviderResult:
                return ProviderResult(success=True, provider="Test")

        p = GoodProvider()
        self.assertTrue(p.can_handle(_make_intent()))
        result = p.fetch(_make_intent())
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Test")


# ---------------------------------------------------------------------------
# WikipediaProvider — can_handle
# ---------------------------------------------------------------------------
class WikipediaProviderCanHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WikipediaProvider()

    def test_definition_always_handles(self) -> None:
        intent = _make_intent(query_type="definition", raw_query="what is polymorphism")
        self.assertTrue(self.provider.can_handle(intent))

    def test_definition_with_entity(self) -> None:
        intent = _make_intent(
            query_type="definition",
            entities=["Docker"],
            topic="Docker",
            raw_query="what is docker",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_weather_dynamic_fact_skips(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "weather", "time_relevance": "dynamic"},
            raw_query="what is the weather today",
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_office_holder_skips(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"offices": ["Chief Minister"], "topic_category": "government"},
            raw_query="who is the chief minister of west bengal",
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_comparison_skips(self) -> None:
        intent = _make_intent(query_type="comparison", entities=["ChatGPT", "Claude"])
        self.assertFalse(self.provider.can_handle(intent))

    def test_conversation_skips(self) -> None:
        intent = _make_intent(query_type="conversation", raw_query="hello")
        self.assertFalse(self.provider.can_handle(intent))

    def test_tutorial_skips(self) -> None:
        intent = _make_intent(query_type="tutorial", raw_query="how to learn python")
        self.assertFalse(self.provider.can_handle(intent))

    def test_reasoning_skips(self) -> None:
        intent = _make_intent(query_type="reasoning", raw_query="why is the sky blue")
        self.assertFalse(self.provider.can_handle(intent))

    def test_research_handles(self) -> None:
        intent = _make_intent(query_type="research", raw_query="machine learning explained")
        self.assertTrue(self.provider.can_handle(intent))

    def test_dynamic_fact_generic_handles(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="who is alan turing",
            topic="alan turing",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_news_dynamic_fact_skips(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "news", "time_relevance": "dynamic"},
            raw_query="latest ai news",
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_business_dynamic_fact_skips(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "business"},
            raw_query="stock market today",
        )
        self.assertFalse(self.provider.can_handle(intent))


# ---------------------------------------------------------------------------
# WikipediaProvider — confidence computation
# ---------------------------------------------------------------------------
class WikipediaProviderConfidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WikipediaProvider()

    def test_exact_match_long_extract(self) -> None:
        conf = self.provider._compute_confidence(
            topic="Polymorphism",
            page_title="Polymorphism",
            extract="Polymorphism is the provision of a single interface to entities of different types." * 20,
            is_redirect=False,
            is_disambiguation=False,
        )
        self.assertGreater(conf, 0.70)

    def test_exact_match_short_extract(self) -> None:
        conf = self.provider._compute_confidence(
            topic="Docker",
            page_title="Docker (software)",
            extract="Docker is a set of platform as a service products." * 5,
            is_redirect=False,
            is_disambiguation=False,
        )
        self.assertGreater(conf, 0.30)
        self.assertLess(conf, 0.80)

    def test_disambiguation_penalty(self) -> None:
        conf = self.provider._compute_confidence(
            topic="Python",
            page_title="Python (disambiguation)",
            extract="Python may refer to:" * 10,
            is_redirect=False,
            is_disambiguation=True,
        )
        self.assertGreaterEqual(conf, 0.0)
        self.assertLess(conf, 0.85)

    def test_redirect_penalty(self) -> None:
        conf = self.provider._compute_confidence(
            topic="AI",
            page_title="Artificial intelligence",
            extract="Artificial intelligence is intelligence demonstrated by machines." * 10,
            is_redirect=True,
            is_disambiguation=False,
        )
        self.assertLess(conf, 0.85)

    def test_no_extract(self) -> None:
        conf = self.provider._compute_confidence(
            topic="UnknownTopic",
            page_title="Some other page",
            extract="",
            is_redirect=False,
            is_disambiguation=False,
        )
        self.assertLess(conf, 0.30)

    def test_substring_title_match(self) -> None:
        conf = self.provider._compute_confidence(
            topic="Polymorphism computer science",
            page_title="Polymorphism (computer science)",
            extract="Polymorphism is a feature of object-oriented programming." * 10,
            is_redirect=False,
            is_disambiguation=False,
        )
        self.assertGreater(conf, 0.50)


# ---------------------------------------------------------------------------
# WikipediaProvider — _resolve_topic
# ---------------------------------------------------------------------------
class WikipediaProviderResolveTopicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WikipediaProvider()

    def test_uses_entities_first(self) -> None:
        intent = _make_intent(
            entities=["Docker", "Container"],
            topic="containerization",
            raw_query="what is docker container",
        )
        topic = self.provider._resolve_topic(intent)
        self.assertEqual(topic, "Docker Container")

    def test_falls_back_to_topic(self) -> None:
        intent = _make_intent(
            entities=[],
            topic="polymorphism",
            raw_query="what is polymorphism",
        )
        topic = self.provider._resolve_topic(intent)
        self.assertEqual(topic, "polymorphism")

    def test_falls_back_to_raw_query(self) -> None:
        intent = _make_intent(
            entities=[],
            topic="",
            raw_query="what is recursion",
        )
        topic = self.provider._resolve_topic(intent)
        self.assertEqual(topic, "what is recursion")


# ---------------------------------------------------------------------------
# WikipediaProvider — fetch with mocked API
# ---------------------------------------------------------------------------
class WikipediaProviderFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WikipediaProvider()

    @patch("august.providers.wikipedia_provider.requests.get")
    def test_fetch_definition_success(self, mock_get: MagicMock) -> None:
        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
        )

        # Mock search response
        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "query": {
                "search": [
                    {"title": "Polymorphism", "pageid": 12345}
                ]
            }
        }

        # Mock summary response
        summary_response = Mock()
        summary_response.status_code = 200
        summary_response.json.return_value = {
            "title": "Polymorphism",
            "extract": "Polymorphism is the provision of a single interface to entities of different types.",
            "content_urls": {
                "desktop": {
                    "page": "https://en.wikipedia.org/wiki/Polymorphism"
                }
            },
            "type": "standard",
        }

        mock_get.side_effect = [search_response, summary_response]

        result = self.provider.fetch(intent)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Wikipedia")
        self.assertEqual(result.title, "Polymorphism")
        self.assertEqual(result.source, "Wikipedia")
        self.assertIn("Polymorphism", result.summary)
        self.assertEqual(result.url, "https://en.wikipedia.org/wiki/Polymorphism")
        self.assertGreater(result.confidence, 0.5)

    @patch("august.providers.wikipedia_provider.requests.get")
    def test_fetch_page_not_found(self, mock_get: MagicMock) -> None:
        intent = _make_intent(
            query_type="definition",
            entities=["NonexistentTopicXYZ123"],
            raw_query="what is nonexistenttopicxyz123",
        )

        # Mock search response — empty results
        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {"query": {"search": []}}

        mock_get.return_value = search_response

        result = self.provider.fetch(intent)

        self.assertFalse(result.success)

    @patch("august.providers.wikipedia_provider.requests.get")
    def test_fetch_summary_404(self, mock_get: MagicMock) -> None:
        intent = _make_intent(
            query_type="definition",
            entities=["SomePage"],
            raw_query="what is somepage",
        )

        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "query": {"search": [{"title": "SomePage", "pageid": 999}]}
        }

        summary_response = Mock()
        summary_response.status_code = 404

        mock_get.side_effect = [search_response, summary_response]

        result = self.provider.fetch(intent)

        self.assertFalse(result.success)

    @patch("august.providers.wikipedia_provider.requests.get")
    def test_fetch_api_error(self, mock_get: MagicMock) -> None:
        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
        )

        mock_get.side_effect = requests.exceptions.ConnectionError("API unavailable")

        result = self.provider.fetch(intent)

        self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# ProviderRouter
# ---------------------------------------------------------------------------
class ProviderRouterTests(unittest.TestCase):
    def test_routes_definition_to_wikipedia(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
        )

        # Mock WikipediaProvider to return a good result
        mock_result = ProviderResult(
            success=True,
            provider="Wikipedia",
            confidence=0.94,
            source="Wikipedia",
            title="Polymorphism",
            summary="Polymorphism is...",
            raw_text="Polymorphism is...",
            url="https://en.wikipedia.org/wiki/Polymorphism",
        )

        with patch.object(router, "_providers", new_callable=list) as mock_providers:
            wp = Mock(spec=WikipediaProvider)
            wp.can_handle.return_value = True
            wp.fetch.return_value = mock_result
            mock_providers.append(wp)

            # Re-run route with our patched provider
            router._providers = [wp]
            result = router.route(intent)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Wikipedia")
        self.assertEqual(result.title, "Polymorphism")

    def test_skips_weather(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "weather"},
            raw_query="what is the weather today",
        )
        result = router.route(intent)
        self.assertIsNone(result)

    def test_skips_office_holder(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"offices": ["Chief Minister"]},
            raw_query="who is the chief minister of west bengal",
        )
        result = router.route(intent)
        self.assertIsNone(result)

    def test_skips_comparison(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="comparison",
            entities=["ChatGPT", "Claude"],
            raw_query="compare ChatGPT and Claude",
        )
        result = router.route(intent)
        self.assertIsNone(result)

    def test_skips_news(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="news",
            raw_query="latest news today",
        )
        result = router.route(intent)
        self.assertIsNone(result)

    def test_falls_back_when_provider_fails(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
        )

        failing_provider = Mock(spec=WikipediaProvider)
        failing_provider.can_handle.return_value = True
        failing_provider.fetch.return_value = ProviderResult(
            success=False, provider="Wikipedia", confidence=0.0
        )

        router._providers = [failing_provider]
        result = router.route(intent)
        self.assertIsNone(result)

    def test_falls_back_when_provider_raises(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
        )

        broken_provider = Mock(spec=WikipediaProvider)
        broken_provider.can_handle.return_value = True
        broken_provider.fetch.side_effect = RuntimeError("Unexpected error")

        router._providers = [broken_provider]
        result = router.route(intent)
        self.assertIsNone(result)

    def test_register_adds_provider(self) -> None:
        router = ProviderRouter()
        provider = Mock(spec=WikipediaProvider)
        provider.can_handle.return_value = True
        provider.fetch.return_value = ProviderResult(
            success=True, provider="Test", title="Test"
        )

        original_count = len(router._providers)
        router.register(provider)
        self.assertEqual(len(router._providers), original_count + 1)

    def test_returns_none_when_no_provider_handles(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(query_type="conversation", raw_query="hello")
        result = router.route(intent)
        self.assertIsNone(result)

    def test_research_type_routes_to_wikipedia(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="research",
            raw_query="machine learning explained",
            topic="machine learning",
        )

        mock_result = ProviderResult(
            success=True,
            provider="Wikipedia",
            confidence=0.85,
            source="Wikipedia",
            title="Machine learning",
            summary="Machine learning is a field of AI...",
            raw_text="Machine learning is a field of AI...",
            url="https://en.wikipedia.org/wiki/Machine_learning",
        )

        wp = Mock(spec=WikipediaProvider)
        wp.can_handle.return_value = True
        wp.fetch.return_value = mock_result
        router._providers = [wp]

        result = router.route(intent)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)

    def test_dynamic_fact_generic_routes_to_wikipedia(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="who is alan turing",
            topic="alan turing",
            metadata={"time_relevance": "static"},
        )

        mock_result = ProviderResult(
            success=True,
            provider="Wikipedia",
            confidence=0.90,
            source="Wikipedia",
            title="Alan Turing",
            summary="Alan Turing was an English mathematician...",
            raw_text="Alan Turing was an English mathematician...",
            url="https://en.wikipedia.org/wiki/Alan_Turing",
        )

        wp = Mock(spec=WikipediaProvider)
        wp.can_handle.return_value = True
        wp.fetch.return_value = mock_result
        router._providers = [wp]

        result = router.route(intent)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)


# ---------------------------------------------------------------------------
# Integration: Web Research with Provider Router
# ---------------------------------------------------------------------------
class WebResearchProviderIntegrationTests(unittest.TestCase):
    def test_provider_skips_weather_query_before_web_search(self) -> None:
        """Verify that weather queries skip the provider and fall through
        to web research (which is tested by the existing test suite)."""
        from august.web_research import WebResearchEngine

        WebResearchEngine()
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "weather", "time_relevance": "dynamic"},
            raw_query="what is the weather today",
        )

        provider_router = ProviderRouter()
        result = provider_router.route(intent)
        self.assertIsNone(result, "Weather queries should not be handled by any provider")

    def test_office_holder_skips_provider(self) -> None:
        """Office-holder queries should not be handled by Wikipedia."""

        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"offices": ["Chief Minister"]},
            raw_query="who is the current chief minister of west bengal",
        )

        provider_router = ProviderRouter()
        result = provider_router.route(intent)
        self.assertIsNone(result)

    @patch("august.providers.wikipedia_provider.requests.get")
    def test_wikipedia_fetch_works_and_validates(self, mock_get: MagicMock) -> None:
        """Test the full provider flow: fetch → validate → return."""
        from august.result_validator import validate_article_content, verify_answer_relevance
        from august.web_research import WebResearchEngine

        WebResearchEngine()

        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "query": {"search": [{"title": "Polymorphism", "pageid": 12345}]}
        }

        extract_text = (
            "Polymorphism is the provision of a single interface to entities "
            "of different types. In computer science, polymorphism is a "
            "feature of object-oriented programming languages. It allows "
            "objects of different types to respond to the same method call. "
            "There are two main types: compile-time polymorphism and runtime "
            "polymorphism. Compile-time polymorphism is achieved through method "
            "overloading, while runtime polymorphism uses method overriding."
        )

        summary_response = Mock()
        summary_response.status_code = 200
        summary_response.json.return_value = {
            "title": "Polymorphism",
            "extract": extract_text,
            "content_urls": {
                "desktop": {
                    "page": "https://en.wikipedia.org/wiki/Polymorphism"
                }
            },
            "type": "standard",
        }

        mock_get.side_effect = [search_response, summary_response]

        intent = _make_intent(
            query_type="definition",
            entities=["Polymorphism"],
            raw_query="what is polymorphism",
            topic="polymorphism",
        )

        provider = WikipediaProvider()
        result = provider.fetch(intent)

        self.assertTrue(result.success)
        self.assertGreater(result.confidence, 0.5)

        # Validate the result through the existing validation pipeline
        validation = validate_article_content(result.raw_text, intent, title=result.title)
        self.assertTrue(validation["valid"], f"Article validation failed: {validation.get('reason')}")

        verification = verify_answer_relevance(result.raw_text, "what is polymorphism", intent)
        self.assertTrue(verification["valid"], f"Answer verification failed: {verification.get('reason')}")

    @patch("august.web_research.ProviderRouter")
    @patch("august.web_research.WebResearchEngine._search")
    def test_web_research_falls_through_when_provider_none(
        self, mock_search: MagicMock, mock_router_class: MagicMock
    ) -> None:
        """When provider returns None, web research should proceed normally."""
        from august.web_research import WebResearchEngine

        mock_router = Mock()
        mock_router.route.return_value = None
        mock_router_class.return_value = mock_router

        mock_search.return_value = [
            Mock(title="Test Result", href="https://example.com/test", snippet="test snippet")
        ]

        engine = WebResearchEngine()
        with patch.object(engine, "_fetch_article_text", return_value="This is a test article with enough text." * 20):
            result = engine.research("test query", ai_parser=None, include_attribution=False)

        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
