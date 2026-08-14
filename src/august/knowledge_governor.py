from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from august.entity_guard import entities_conflict, extract_entities, merge_entities
from august.sanity_validator import validate_query_sanity
from august.utils.logger import get_logger, log_event

logger = get_logger("KnowledgeGovernor")

MEMORY_TYPES = {
    "static_knowledge",
    "dynamic_fact",
    "conversation",
    "user_preference",
}

BLOCKED_MEMORY_PATTERNS = [
    "tell me more",
    "search it",
    "yes",
    "no",
    "give example",
    "explain more",
]
CONVERSATIONAL_PATTERNS = (
    "how are you",
    "how r u",
    "what's up",
    "whats up",
    "hello",
    "hi",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "thank you",
    "thanks",
    "nice",
)

DYNAMIC_FACT_TTL_HOURS = 24
STATIC_MEMORY_TTL_DAYS = 365
CONFIDENCE_DECAY_PER_DAY = 0.01
MAX_CONFIDENCE_DECAY = 0.45
SOURCE_CONFIDENCE_CAPS = {
    "verified_local": 0.92,
    "local": 0.92,
    "verified_web": 0.88,
    "web": 0.88,
    "ai": 0.80,
    "conversational": 0.40,
}


class KnowledgeGovernor:
    def classify_memory(self, query: str, answer: str = "") -> str:
        normalized = self._normalize(query)
        answer_normalized = self._normalize(answer)
        if self.is_conversational_query(normalized):
            memory_type = "conversation"
        elif self._looks_like_preference(normalized, answer_normalized):
            memory_type = "user_preference"
        elif self._looks_dynamic(normalized, answer_normalized):
            memory_type = "dynamic_fact"
        else:
            memory_type = "static_knowledge"
        log_event(logger, "memory_category_assigned", source="knowledge_governor", success=True, query=normalized, memory_type=memory_type)
        return memory_type

    def should_store(self, query: str, answer: str) -> tuple[bool, str]:
        normalized = self._normalize(query)
        if self.is_conversational_query(normalized):
            log_event(logger, "conversation_memory_detected", source="knowledge_governor", success=True, query=normalized)
            log_event(logger, "knowledge_storage_blocked", source="knowledge_governor", success=True, query=normalized, reason="conversation")
            log_event(logger, "conversational_memory_blocked", source="knowledge_governor", success=True, query=normalized)
            return False, "conversation"
        sanity = validate_query_sanity(normalized)
        if not sanity.is_valid:
            log_event(logger, "knowledge_storage_blocked", source="knowledge_governor", success=True, query=normalized, reason=sanity.reason)
            return False, "static_knowledge"
        memory_type = self.classify_memory(normalized, answer)
        return memory_type != "conversation", memory_type

    def should_store_research(self, query: str, answer: str, confidence: float) -> bool:
        """Gate whether a web research result should be persisted to memory.

        Returns False (and logs ``research_memory_blocked``) when the result
        is too weak to be trustworthy.
        """
        if confidence < 0.8:
            log_event(logger, "research_memory_blocked", source="knowledge_governor", success=False, query=query, reason="low_confidence", confidence=round(confidence, 3))
            return False

        normalized_answer = self._normalize(answer)
        filler_phrases = (
            "is a concept that refers to",
            "is an important concept",
            "core idea, common use cases, and trade-offs",
            "it generally involves understanding",
        )
        if any(phrase in normalized_answer for phrase in filler_phrases):
            log_event(logger, "research_memory_blocked", source="knowledge_governor", success=False, query=query, reason="filler_content")
            return False

        if len((answer or "").strip()) < 50:
            log_event(logger, "research_memory_blocked", source="knowledge_governor", success=False, query=query, reason="answer_too_short")
            return False

        log_event(logger, "research_memory_stored", source="knowledge_governor", success=True, query=query, confidence=round(confidence, 3))
        return True

    def cap_confidence(self, confidence: float, source: str) -> float:
        normalized_source = self._normalize(source) or "unknown"
        cap = SOURCE_CONFIDENCE_CAPS.get(normalized_source)
        if cap is None:
            return max(0.0, min(1.0, float(confidence)))
        capped = min(max(0.0, float(confidence)), cap)
        if capped < confidence:
            log_event(
                logger,
                "confidence_capped",
                source="knowledge_governor",
                success=True,
                answer_source=normalized_source,
                original_confidence=round(float(confidence), 3),
                capped_confidence=round(capped, 3),
            )
        return capped

    def is_retrieval_eligible(self, current_query: str, entry: dict[str, Any], min_confidence: float) -> tuple[bool, float, str]:
        stored_query = str(entry.get("question") or entry.get("query") or "")
        stored_answer = str(entry.get("answer") or "")
        query_entities = extract_entities(current_query)
        memory_entities = merge_entities(extract_entities(stored_query), extract_entities(stored_answer))
        if entities_conflict(query_entities, memory_entities):
            log_event(
                logger,
                "memory_rejected_entity_conflict",
                source="knowledge_governor",
                success=False,
                query=current_query,
                memory_query=stored_query,
            )
            return False, 0.0, "entity_conflict"

        base_confidence = self._safe_float(entry.get("confidence"), 0.0)
        effective_confidence = self.effective_confidence(base_confidence, entry.get("timestamp"))
        if base_confidence - effective_confidence >= 0.001:
            log_event(
                logger,
                "memory_confidence_decay",
                source="knowledge_governor",
                success=True,
                query=stored_query,
                base_confidence=round(base_confidence, 3),
                effective_confidence=round(effective_confidence, 3),
            )
        if effective_confidence < min_confidence:
            return False, effective_confidence, "low_confidence"

        if self.is_expired(entry):
            log_event(logger, "memory_expired", source="knowledge_governor", success=False, query=stored_query, memory_type=self.memory_type_for_entry(entry))
            return False, effective_confidence, "expired"

        return True, effective_confidence, ""

    def memory_type_for_entry(self, entry: dict[str, Any]) -> str:
        memory_type = str(entry.get("memory_type") or "").strip()
        if memory_type in MEMORY_TYPES:
            return memory_type
        return self.classify_memory(str(entry.get("question") or entry.get("query") or ""), str(entry.get("answer") or ""))

    def is_expired(self, entry: dict[str, Any]) -> bool:
        timestamp = self.parse_timestamp(entry.get("timestamp"))
        if timestamp is None:
            return True
        memory_type = self.memory_type_for_entry(entry)
        age = datetime.now(timezone.utc) - timestamp
        if memory_type == "dynamic_fact":
            return age > timedelta(hours=DYNAMIC_FACT_TTL_HOURS)
        if memory_type == "conversation":
            return True
        if memory_type == "static_knowledge":
            return age > timedelta(days=STATIC_MEMORY_TTL_DAYS)
        return False

    def effective_confidence(self, confidence: float, timestamp_value: Any) -> float:
        timestamp = self.parse_timestamp(timestamp_value)
        if timestamp is None:
            return 0.0
        age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400)
        penalty = min(MAX_CONFIDENCE_DECAY, age_days * CONFIDENCE_DECAY_PER_DAY)
        return max(0.0, float(confidence) - penalty)

    def is_conversational_query(self, query: str) -> bool:
        normalized = self._normalize(query)
        if any(normalized == pattern or normalized.startswith(pattern) for pattern in BLOCKED_MEMORY_PATTERNS):
            return True
        return any(normalized == pattern or normalized.startswith(pattern) for pattern in CONVERSATIONAL_PATTERNS)

    def parse_timestamp(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _looks_dynamic(self, query: str, answer: str) -> bool:
        dynamic_markers = (
            "chief minister",
            "prime minister",
            "president",
            "current",
            "latest",
            "today",
            "yesterday",
            "news",
            "stock price",
            "share price",
            "ranking",
            "cricket",
            "match",
            "won",
            "election",
        )
        combined = f"{query} {answer}"
        return any(marker in combined for marker in dynamic_markers)

    def _looks_like_preference(self, query: str, answer: str) -> bool:
        combined = f"{query} {answer}"
        return any(marker in combined for marker in ("i prefer", "my favorite", "favourite", "use chrome", "open spotify"))

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
