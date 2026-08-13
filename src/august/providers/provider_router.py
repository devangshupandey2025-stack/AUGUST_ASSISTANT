"""Provider Router — routes a query intent to the appropriate provider.

If a provider can handle the intent and returns a successful result, that
result is returned.  Otherwise the caller should fall back to generic web
research.
"""

from __future__ import annotations

from query_understanding import QueryIntent
from providers.base_provider import BaseProvider
from providers.provider_result import ProviderResult
from providers.wikipedia_provider import WikipediaProvider
from providers.weather_provider import WeatherProvider
from utils.logger import get_logger, log_event

logger = get_logger("ProviderRouter")

# ---------------------------------------------------------------------------
# Provider registry — add new providers here.  No router changes needed.
# ---------------------------------------------------------------------------
AVAILABLE_PROVIDERS: list[BaseProvider] = [
    WikipediaProvider(),
    WeatherProvider(),
]


class ProviderRouter:
    """Holds a list of registered providers and routes intents to them.

    New providers must inherit from ``BaseProvider`` and be added to
    ``AVAILABLE_PROVIDERS``.  No other changes are needed.
    """

    def __init__(self) -> None:
        self._providers: list[BaseProvider] = list(AVAILABLE_PROVIDERS)

    def route(self, intent: QueryIntent) -> ProviderResult | None:
        """Try each registered provider in order.

        Returns the first successful ``ProviderResult``, or ``None`` if no
        provider can handle the intent or all providers failed.
        """
        log_event(logger, "provider_router_started", source="provider_router",
                  success=True, query_type=intent.type,
                  entities=intent.entities, topic=intent.topic)

        for provider in self._providers:
            provider_name = provider.__class__.__name__

            if not provider.can_handle(intent):
                log_event(logger, "provider_skipped", source="provider_router",
                          success=True, provider=provider_name,
                          reason="cannot_handle")
                continue

            log_event(logger, "provider_selected", source="provider_router",
                      success=True, provider=provider_name)

            try:
                result = provider.fetch(intent)
            except Exception as exc:
                log_event(logger, "provider_failed", source="provider_router",
                          success=False, provider=provider_name,
                          error=str(exc))
                log_event(logger, "provider_fallback", source="provider_router",
                          success=True, provider=provider_name,
                          reason="exception")
                return None

            if result.success:
                log_event(logger, "provider_success", source="provider_router",
                          success=True, provider=provider_name,
                          confidence=round(result.confidence, 3),
                          title=result.title)
                return result

            log_event(logger, "provider_failed", source="provider_router",
                      success=False, provider=provider_name,
                      reason="empty_result")
            log_event(logger, "provider_fallback", source="provider_router",
                      success=True, provider=provider_name,
                      reason="empty_result")

        return None

    def register(self, provider: BaseProvider) -> None:
        """Register a new provider at runtime (for testing / future use)."""
        self._providers.append(provider)
