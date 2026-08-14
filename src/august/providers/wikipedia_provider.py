"""Wikipedia API Provider — fetches definitions, concepts, people, places,
technologies, organizations, and historical events via the official Wikipedia
REST API and MediaWiki Action API.  Never scrapes HTML.
"""

from __future__ import annotations

import re
import time
import urllib.parse

import requests

from august.providers.base_provider import BaseProvider
from august.providers.provider_result import ProviderResult
from august.query_understanding import QueryIntent
from august.utils.logger import get_logger, log_event

logger = get_logger("WikipediaProvider")

API_BASE = "https://en.wikipedia.org"
REST_API = f"{API_BASE}/api/rest_v1"
MEDIAWIKI_API = f"{API_BASE}/w/api.php"

REQUEST_TIMEOUT = 8
WIKIPEDIA_HEADERS = {
    "User-Agent": "JARVIS/6.0 (https://github.com/jarvis; jarvis@example.com) requests/6.0",
}

# ---------------------------------------------------------------------------
# Intent types / topic categories that Wikipedia should NOT handle
# ---------------------------------------------------------------------------
_SKIP_TYPES: frozenset[str] = frozenset({
    "comparison",
    "conversation",
    "tutorial",
    "reasoning",
    "news",
})

_SKIP_TOPIC_CATEGORIES: frozenset[str] = frozenset({
    "weather",
    "news",
    "business",
})

_SKIP_METADATA_KEYS: frozenset[str] = frozenset({
    "offices",
})


class WikipediaProvider(BaseProvider):
    """Fetches stable-knowledge content (definitions, concepts, people,
    places, technologies, historical events) from Wikipedia."""

    def can_handle(self, intent: QueryIntent) -> bool:
        if intent.type in _SKIP_TYPES:
            return False

        topic_category = intent.metadata.get("topic_category", "")
        if topic_category in _SKIP_TOPIC_CATEGORIES:
            return False

        if intent.metadata.get("offices"):
            return False

        if intent.type == "definition":
            return True

        if intent.type == "research":
            return True

        if intent.type == "dynamic_fact":
            time_rel = intent.metadata.get("time_relevance", "")
            if time_rel == "dynamic" and topic_category == "news":
                return False
            return True

        return False

    def fetch(self, intent: QueryIntent) -> ProviderResult:
        topic = self._resolve_topic(intent)
        if not topic:
            log_event(logger, "wikipedia_query", source="wikipedia_provider",
                      success=False, reason="empty_topic")
            return ProviderResult(success=False, provider="Wikipedia",
                                  confidence=0.0)

        log_event(logger, "wikipedia_query", source="wikipedia_provider",
                  success=True, topic=topic)

        # Step 1: search for the best page title
        page_title = self._search_page(topic)
        if not page_title:
            log_event(logger, "wikipedia_result", source="wikipedia_provider",
                      success=False, topic=topic, reason="page_not_found")
            return ProviderResult(success=False, provider="Wikipedia",
                                  confidence=0.0)

        # Step 2: fetch the page summary
        summary_data = self._fetch_summary(page_title)
        if not summary_data:
            log_event(logger, "wikipedia_result", source="wikipedia_provider",
                      success=False, topic=topic, reason="summary_fetch_failed")
            return ProviderResult(success=False, provider="Wikipedia",
                                  confidence=0.0)

        extract = self._as_str(summary_data.get("extract", ""))
        if not extract:
            log_event(logger, "wikipedia_result", source="wikipedia_provider",
                      success=False, topic=topic, reason="empty_extract")
            return ProviderResult(success=False, provider="Wikipedia",
                                  confidence=0.0)

        title = self._as_str(summary_data.get("title", page_title)) or page_title
        content_urls = self._as_dict(summary_data.get("content_urls"))
        desktop_urls = self._as_dict(content_urls.get("desktop"))
        page_url = self._as_str(desktop_urls.get("page", ""))
        is_redirect = summary_data.get("redirect_from", None) is not None
        is_disambiguation = self._as_str(summary_data.get("type", "")) == "disambiguation"

        confidence = self._compute_confidence(
            topic=topic,
            page_title=title,
            extract=extract,
            is_redirect=is_redirect,
            is_disambiguation=is_disambiguation,
        )

        log_event(logger, "wikipedia_result", source="wikipedia_provider",
                  success=True, topic=topic, page_title=title,
                  extract_length=len(extract),
                  confidence=round(confidence, 3), is_redirect=is_redirect)

        return ProviderResult(
            success=True,
            provider="Wikipedia",
            confidence=confidence,
            source="Wikipedia",
            title=title,
            summary=extract,
            raw_text=extract,
            url=page_url,
            metadata={
                "page_title": title,
                "topic": topic,
                "is_redirect": is_redirect,
                "is_disambiguation": is_disambiguation,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_topic(self, intent: QueryIntent) -> str:
        """Determine the Wikipedia search topic from the intent."""
        if intent.entities:
            return " ".join(intent.entities)
        if intent.topic:
            return intent.topic
        return intent.raw_query.strip()

    def _search_page(self, query: str) -> str | None:
        """Search Wikipedia for the best page title matching *query*."""
        params: dict[str, str | int] = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 3,
            "srprop": "title",
        }
        try:
            response = requests.get(
                MEDIAWIKI_API,
                params=params,
                headers=WIKIPEDIA_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log_event(logger, "wikipedia_search_failed",
                      source="wikipedia_provider", success=False,
                      query=query, error=str(exc))
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        if not isinstance(data, dict):
            return None
        query_data = self._as_dict(data.get("query", {}))
        search_results = query_data.get("search", [])
        if not search_results:
            return None
        if not isinstance(search_results, list):
            return None
        first_result = search_results[0] if search_results else {}
        if not isinstance(first_result, dict):
            return None
        page_title = self._as_str(first_result.get("title", ""))
        return page_title or None

    def _fetch_summary(self, page_title: str) -> dict[str, object] | None:
        """Fetch the summary/extract for a Wikipedia page."""
        encoded = urllib.parse.quote(page_title.replace(" ", "_"), safe="")
        url = f"{REST_API}/page/summary/{encoded}"

        for attempt in (1, 2):
            try:
                response = requests.get(url, headers=WIKIPEDIA_HEADERS, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    payload = response.json()
                    return payload if isinstance(payload, dict) else None
                if response.status_code == 404:
                    log_event(logger, "wikipedia_page_not_found",
                              source="wikipedia_provider", success=False,
                              page_title=page_title)
                    return None
                response.raise_for_status()
            except requests.RequestException as exc:
                log_event(logger, "wikipedia_summary_failed",
                          source="wikipedia_provider", success=False,
                          page_title=page_title, attempt=attempt,
                          error=str(exc))
                if attempt == 1:
                    time.sleep(0.5)
                continue

        return None

    def _as_str(self, value: object) -> str:
        return value if isinstance(value, str) else str(value or "")

    def _as_dict(self, value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    def _compute_confidence(
        self,
        topic: str,
        page_title: str,
        extract: str,
        is_redirect: bool,
        is_disambiguation: bool,
    ) -> float:
        """Compute 0.0–1.0 confidence for a Wikipedia result."""
        score = 0.0

        # 1. Page exists (max 0.20)
        score += 0.20

        # 2. Disambiguation penalty (max -0.15)
        if is_disambiguation:
            score -= 0.15

        # 3. Title match quality (max 0.35)
        topic_lower = topic.lower().strip()
        title_lower = page_title.lower().strip()
        if topic_lower == title_lower:
            score += 0.35
        elif topic_lower in title_lower or title_lower in topic_lower:
            score += 0.25
        else:
            # Token-level overlap
            topic_tokens = set(re.findall(r"[a-z0-9]+", topic_lower))
            title_tokens = set(re.findall(r"[a-z0-9]+", title_lower))
            if topic_tokens and title_tokens:
                overlap = len(topic_tokens & title_tokens) / len(topic_tokens)
                score += 0.35 * overlap

        # 4. Extract length (max 0.25)
        length = len(extract)
        if length >= 800:
            score += 0.25
        elif length >= 400:
            score += 0.20
        elif length >= 200:
            score += 0.15
        elif length >= 100:
            score += 0.10
        else:
            score += 0.05

        # 5. Redirect penalty (max -0.20)
        if is_redirect:
            score -= 0.20

        return max(0.0, min(1.0, score))
