from __future__ import annotations

from abc import ABC, abstractmethod

from query_understanding import QueryIntent

from providers.provider_result import ProviderResult


class BaseProvider(ABC):
    """Abstract base class for all information providers.

    Every provider must implement:
      - can_handle(intent) -> bool   — determine whether this provider can
                                        answer the query
      - fetch(intent) -> ProviderResult — retrieve and return the information
    """

    @abstractmethod
    def can_handle(self, intent: QueryIntent) -> bool:
        ...

    @abstractmethod
    def fetch(self, intent: QueryIntent) -> ProviderResult:
        ...
