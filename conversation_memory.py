from __future__ import annotations

import re
from contextlib import nullcontext
from typing import Any


def get_conversation_memory(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    return dict((snapshot or {}).get("conversation", {}) or {})


def get_last_topic(snapshot: dict[str, Any] | None) -> str:
    conversation = get_conversation_memory(snapshot)
    return str(conversation.get("last_topic", "") or "").strip()


def get_preferred_style(snapshot: dict[str, Any] | None) -> str:
    conversation = get_conversation_memory(snapshot)
    style = str(conversation.get("preferred_response_style", "balanced") or "balanced").strip().lower()
    return style if style in {"brief", "balanced", "detailed"} else "balanced"


def get_faq_answer(snapshot: dict[str, Any] | None, question: str) -> str:
    conversation = get_conversation_memory(snapshot)
    normalized = normalize_text(question)
    answers = conversation.get("faq_answers", {}) or {}
    return str(answers.get(normalized, "") or "").strip()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def infer_response_style(text: str, current: str = "balanced") -> str:
    normalized = normalize_text(text)
    if any(phrase in normalized for phrase in ("briefly", "in short", "short answer", "keep it short", "quickly")):
        return "brief"
    if any(phrase in normalized for phrase in ("in detail", "detailed", "go deep", "deep dive", "thoroughly")):
        return "detailed"
    return current if current in {"brief", "balanced", "detailed"} else "balanced"


def remember_answer(memory_store: Any, raw_text: str, response: str, topic: str = "") -> None:
    normalized = normalize_text(raw_text)
    if not normalized or not hasattr(memory_store, "_data") or not hasattr(memory_store, "_write"):
        return

    lock = getattr(memory_store, "_lock", None)
    context_manager = lock if lock is not None else nullcontext()
    with context_manager:
        data = memory_store._data
        conversation = data.setdefault(
            "conversation",
            {
                "last_topic": "",
                "preferred_response_style": "balanced",
                "faq_counts": {},
                "faq_answers": {},
            },
        )
        conversation["preferred_response_style"] = infer_response_style(
            raw_text,
            str(conversation.get("preferred_response_style", "balanced") or "balanced"),
        )
        if topic:
            conversation["last_topic"] = topic.strip()
        elif normalized.endswith("?") or normalized.startswith(
            ("what ", "who ", "why ", "how ", "which ", "tell me", "explain", "do you think")
        ):
            conversation["last_topic"] = normalized

        faq_counts = conversation.setdefault("faq_counts", {})
        faq_counts[normalized] = int(faq_counts.get(normalized, 0)) + 1
        if response and faq_counts[normalized] >= 2:
            conversation.setdefault("faq_answers", {})[normalized] = response.strip()

        memory_store._write(data)
