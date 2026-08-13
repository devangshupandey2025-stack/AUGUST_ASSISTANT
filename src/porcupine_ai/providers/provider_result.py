from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderResult:
    """Standard result object returned by every provider.

    All providers must return this type — no custom return types allowed.
    """

    success: bool
    provider: str = ""
    confidence: float = 0.0
    source: str = ""
    title: str = ""
    summary: str = ""
    raw_text: str = ""
    url: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    structured_data: dict[str, object] = field(default_factory=dict)
