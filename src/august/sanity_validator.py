from __future__ import annotations

import re
from dataclasses import dataclass

KNOWN_CITIES = {
    "kolkata",
    "mumbai",
    "delhi",
    "new delhi",
    "bengaluru",
    "bangalore",
    "chennai",
    "hyderabad",
    "pune",
    "lucknow",
    "jaipur",
    "ahmedabad",
}

US_STATES = {
    "california",
    "texas",
    "florida",
    "washington",
    "new york",
    "illinois",
    "ohio",
}


@dataclass(frozen=True)
class SanityValidationResult:
    is_valid: bool
    reason: str = ""
    confidence_factor: float = 1.0
    clarification: str = ""


def validate_query_sanity(query: str) -> SanityValidationResult:
    normalized = _normalize(query)
    if not normalized:
        return SanityValidationResult(True)

    city_capital = re.search(r"\bcapital of ([a-z\s]+)\b", normalized)
    if city_capital:
        target = city_capital.group(1).strip()
        if target in KNOWN_CITIES:
            return SanityValidationResult(
                is_valid=False,
                reason="invalid_capital_of_city",
                confidence_factor=0.3,
                clarification=f"Do you mean the state or country where {target.title()} is located?",
            )

    pm_target = re.search(r"\bprime minister of ([a-z\s]+)\b", normalized)
    if pm_target:
        target = pm_target.group(1).strip()
        if target in US_STATES:
            return SanityValidationResult(
                is_valid=False,
                reason="invalid_prime_minister_region",
                confidence_factor=0.3,
                clarification=f"{target.title()} does not have a prime minister. Do you want the governor instead?",
            )

    cm_target = re.search(r"\bchief minister of ([a-z\s]+)\b", normalized)
    if cm_target:
        target = cm_target.group(1).strip()
        if target in {"india", "united states", "usa"}:
            return SanityValidationResult(
                is_valid=False,
                reason="invalid_chief_minister_country",
                confidence_factor=0.35,
                clarification=f"{target.title()} does not have a chief minister. Do you want a state-level leader?",
            )

    compare_match = re.search(r"\b(?:difference between|compare)\s+([a-z0-9\s]+)\s+(?:and|vs|versus)\s+([a-z0-9\s]+)\b", normalized)
    if compare_match:
        left = _normalize(compare_match.group(1))
        right = _normalize(compare_match.group(2))
        if left and right and left == right:
            return SanityValidationResult(
                is_valid=False,
                reason="invalid_comparison_same_entity",
                confidence_factor=0.4,
                clarification="You are comparing the same thing. What two different items should I compare?",
            )

    return SanityValidationResult(True)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
