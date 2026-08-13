from __future__ import annotations

import re
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

from knowledge_governor import KnowledgeGovernor
from utils.logger import get_logger, log_event

logger = get_logger("AnswerMemory")


class AnswerMemory:
    FILLER_WORDS = {"please", "bro", "hello", "hey", "hi"}
    WEAK_LOOKUP_QUERIES = {"yes", "no", "it"}
    INVALID_MEMORY_PATTERNS = (
        "how are you",
        "what's up",
        "whats up",
        "search it",
    )
    INVALID_MEMORY_EXACT = {"yes", "no", "search it"}
    BLOCKED_PHRASES = (
        "not getting a clean answer",
        "want me to search",
        "i didn't quite get that",
        "i did not quite get that",
    )

    def __init__(
        self,
        memory_store: Any,
        expiry_days: int = 30,
        similarity_threshold: float = 0.5,
        min_confidence: float = 0.7,
    ) -> None:
        self.memory_store = memory_store
        self.expiry_days = int(expiry_days)
        self.similarity_threshold = float(similarity_threshold)
        self.min_confidence = float(min_confidence)
        self.governor = KnowledgeGovernor()
        self.last_rejection_reason = ""

    def retrieve(self, question: str, allow_dynamic: bool = True) -> str | None:
        normalized_query = self.normalize_query(question)
        self.last_rejection_reason = ""
        if not normalized_query or normalized_query in self.WEAK_LOOKUP_QUERIES:
            return None

        best_answer = ""
        best_score = 0.0
        query_tokens = self._tokenize(normalized_query)

        for entry in self._iter_valid_entries():
            if not allow_dynamic and self.governor.memory_type_for_entry(entry) == "dynamic_fact":
                continue
            stored_question = self.normalize_query(str(entry.get("question") or entry.get("query") or ""))
            if not stored_question:
                continue

            eligible, effective_confidence, _reason = self.governor.is_retrieval_eligible(
                normalized_query,
                entry,
                self.min_confidence,
            )
            if not eligible:
                if _reason == "expired" and self.governor.memory_type_for_entry(entry) == "dynamic_fact":
                    score = self.similarity_score(normalized_query, query_tokens, stored_question)
                    if score > self.similarity_threshold:
                        self.last_rejection_reason = "expired_dynamic_fact"
                continue

            score = self.similarity_score(normalized_query, query_tokens, stored_question)
            weighted_score = score * effective_confidence
            if score > self.similarity_threshold and weighted_score > best_score:
                answer = str(entry.get("answer", "")).strip()
                if answer:
                    best_score = weighted_score
                    best_answer = answer

        return best_answer or None

    def store(self, question: str, answer: str, confidence: float, timestamp: str | None = None, source: str = "unknown") -> bool:
        normalized_question = self.normalize_query(question)
        clean_answer = " ".join((answer or "").strip().split())
        if not normalized_question or not self._is_storeable_query(normalized_question) or not self._is_safe_answer(clean_answer):
            return False
        should_store, memory_type = self.governor.should_store(normalized_question, clean_answer)
        if not should_store:
            return False

        confidence_value = self.governor.cap_confidence(max(0.0, min(1.0, self._safe_float(confidence, 0.0))), source)
        log_event(
            logger,
            "source_reliability_applied",
            source="answer_memory",
            success=True,
            answer_source=source,
            confidence=round(confidence_value, 3),
        )
        timestamp_value = timestamp or datetime.now(timezone.utc).isoformat()
        entries = self._get_entries()

        for entry in entries:
            stored_question = self.normalize_query(str(entry.get("question") or entry.get("query") or ""))
            existing_source = str(entry.get("source", "unknown") or "unknown").strip().lower()
            query_similarity = self.similarity_score(normalized_question, self._tokenize(normalized_question), stored_question)
            answer_similarity = self._answer_similarity(clean_answer, str(entry.get("answer", "")).strip())
            if query_similarity > 0.9 and answer_similarity > 0.9 and existing_source == source.strip().lower():
                entry["timestamp"] = timestamp_value
                entry["confidence"] = max(confidence_value, self._safe_float(entry.get("confidence"), 0.0))
                entry["source"] = source
                entry["memory_type"] = memory_type
                entry["query"] = normalized_question
                entry["question"] = normalized_question
                self._set_entries(entries)
                log_event(logger, "memory_deduplicated", source="answer_memory", success=True, query=normalized_question)
                return True

            if stored_question != normalized_question:
                continue
            if memory_type == "dynamic_fact" and self.governor.memory_type_for_entry(entry) == "dynamic_fact":
                entry["answer"] = clean_answer
                entry["confidence"] = confidence_value
                entry["timestamp"] = timestamp_value
                entry["source"] = source
                entry["memory_type"] = memory_type
                entry["query"] = normalized_question
                entry["question"] = normalized_question
                self._set_entries(entries)
                log_event(logger, "memory_deduplicated", source="answer_memory", success=True, query=normalized_question)
                return True
            if str(entry.get("answer", "")).strip() != clean_answer:
                continue
            entry["confidence"] = max(confidence_value, self._safe_float(entry.get("confidence"), 0.0))
            entry["timestamp"] = timestamp_value
            entry["source"] = source
            entry["memory_type"] = memory_type
            entry["query"] = normalized_question
            self._set_entries(entries)
            return True

        entries.append(
            {
                "query": normalized_question,
                "question": normalized_question,
                "answer": clean_answer,
                "source": source,
                "memory_type": memory_type,
                "confidence": confidence_value,
                "timestamp": timestamp_value,
            }
        )
        self._set_entries(entries[-300:])
        return True

    def mark_user_confirmed(self, question: str, answer: str | None = None) -> bool:
        normalized_question = self.normalize_query(question)
        if not normalized_question:
            return False

        entries = self._get_entries()
        candidates = [entry for entry in entries if self.normalize_query(str(entry.get("question", ""))) == normalized_question]
        if not candidates:
            return False

        if answer:
            clean_answer = " ".join(answer.strip().split())
            for entry in candidates:
                if str(entry.get("answer", "")).strip() == clean_answer:
                    entry["confidence"] = 1.0
                    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
                    self._set_entries(entries)
                    return True

        latest = max(candidates, key=lambda item: self._parse_iso(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        latest["confidence"] = 1.0
        latest["timestamp"] = datetime.now(timezone.utc).isoformat()
        self._set_entries(entries)
        return True

    def similarity_score(self, query: str, query_tokens: set[str], stored_question: str) -> float:
        if query == stored_question:
            return 1.0
        if query in stored_question or stored_question in query:
            return 0.85

        stored_tokens = self._tokenize(stored_question)
        if not query_tokens or not stored_tokens:
            return 0.0
        overlap = len(query_tokens & stored_tokens)
        union = len(query_tokens | stored_tokens)
        if union == 0:
            return 0.0
        return overlap / union

    def _answer_similarity(self, answer: str, stored_answer: str) -> float:
        normalized_answer = self.normalize_query(answer)
        normalized_stored = self.normalize_query(stored_answer)
        if not normalized_answer or not normalized_stored:
            return 0.0
        return self.similarity_score(normalized_answer, self._tokenize(normalized_answer), normalized_stored)

    def normalize_query(self, text: str) -> str:
        cleaned = (text or "").strip().lower()
        cleaned = re.sub(r"[^\w\s]", " ", cleaned)
        tokens = [token for token in re.split(r"\s+", cleaned) if token and token not in self.FILLER_WORDS]
        return " ".join(tokens)

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in text.split() if token}

    def _is_safe_answer(self, answer: str) -> bool:
        if not answer:
            return False
        if len(answer) < 20 or len(answer.split()) < 4:
            return False
        lowered = answer.lower()
        return not any(phrase in lowered for phrase in self.BLOCKED_PHRASES)

    def _is_storeable_query(self, normalized_query: str) -> bool:
        tokens = [token for token in normalized_query.split() if token]
        if len(tokens) < 3:
            return False
        lowered = normalized_query.lower()
        if lowered in self.INVALID_MEMORY_EXACT:
            return False
        return not any(pattern in lowered for pattern in self.INVALID_MEMORY_PATTERNS)

    def _iter_valid_entries(self) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for entry in self._get_entries():
            valid.append(entry)
        return valid

    def _get_entries(self) -> list[dict[str, Any]]:
        snapshot = self.memory_store.snapshot() if hasattr(self.memory_store, "snapshot") else {}
        entries = snapshot.get("answer_memory", [])
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _set_entries(self, entries: list[dict[str, Any]]) -> None:
        lock = getattr(self.memory_store, "_lock", None)
        context_manager = lock if lock is not None else nullcontext()
        with context_manager:
            data = getattr(self.memory_store, "_data", {})
            if not isinstance(data, dict):
                return
            data["answer_memory"] = entries
            if hasattr(self.memory_store, "_write"):
                self.memory_store._write(data)

    def _parse_iso(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
