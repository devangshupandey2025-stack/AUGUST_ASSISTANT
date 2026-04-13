from __future__ import annotations

import re

from conversation_memory import normalize_text

LOCAL_CONFIDENCE_THRESHOLD = 0.7
TEMPLATES = [
    "In simple terms, {}",
    "Basically, {}",
    "Here's a quick explanation: {}",
]

_TEMPLATE_CURSOR = 0

FALLBACK_PATTERNS = {
    "comparison": ("difference between", "vs", "compare"),
    "algorithmic": ("how to", "steps", "approach", "algorithm"),
    "definition": ("what is", "define", "explain"),
    "factual": ("capital of", "prime minister", "who is"),
}
CASUAL_QUERIES = {"how are you", "how r u", "what's up", "whats up"}


def classify_query(query: str) -> str:
    cleaned = normalize_text(query)
    if cleaned in CASUAL_QUERIES:
        return "casual"
    if any(keyword in cleaned for keyword in FALLBACK_PATTERNS["comparison"]):
        return "comparison"
    if any(keyword in cleaned for keyword in FALLBACK_PATTERNS["algorithmic"]) or cleaned.startswith(("how ", "steps ")):
        return "algorithmic"
    if any(keyword in cleaned for keyword in FALLBACK_PATTERNS["definition"]):
        return "conceptual"
    if any(keyword in cleaned for keyword in FALLBACK_PATTERNS["factual"]):
        return "factual"
    return "unknown"


def try_local_answer(query: str) -> dict[str, str | bool | float]:
    normalized = normalize_text(query)
    if not normalized:
        return {"success": False, "text": "", "error": "empty_query", "confidence": 0.0, "source": "none", "kind": "unknown"}
    if _is_followup_query(normalized):
        return {"success": False, "text": "", "error": "followup_query", "confidence": 0.0, "source": "local_failure", "kind": "unknown"}
    if normalized in CASUAL_QUERIES:
        return {"success": False, "text": "", "error": "casual_query", "confidence": 0.0, "source": "local_failure", "kind": "casual"}

    query_type = classify_query(normalized)

    factual = _factual_answer(normalized)
    if factual:
        return _success(factual, 0.86, source="local_match", kind=query_type)

    cs = _cs_answer(normalized, query_type)
    if cs:
        return _success(cs, 0.88, source="local_match", kind=query_type)

    algorithmic = _algorithmic_answer(normalized, query_type)
    if algorithmic:
        return _success(algorithmic, 0.84, source="local_match", kind="algorithmic")

    definition = _definition_answer(normalized, query_type)
    if definition:
        return _success(definition, 0.78, source="local_match", kind="conceptual")

    generated = _generate_lightweight_answer(normalized, query_type)
    if generated:
        return _success(generated, 0.72, source="local_generated", kind=query_type)

    return {"success": False, "text": "", "error": "no_local_match", "confidence": 0.0, "source": "local_failure", "kind": query_type}


def _factual_answer(normalized: str) -> str:
    if "prime minister of india" in normalized or normalized in {"who is pm of india", "who is the pm of india", "current pm of india"}:
        return _with_template("the Prime Minister of India is Narendra Modi.")

    capital_map = {
        "india": "New Delhi",
        "france": "Paris",
        "japan": "Tokyo",
        "usa": "Washington, D.C.",
        "united states": "Washington, D.C.",
        "uk": "London",
        "united kingdom": "London",
    }
    capital_match = re.search(r"capital of ([a-z\s]+)", normalized)
    if capital_match:
        country = re.sub(r"\s+", " ", capital_match.group(1)).strip()
        if country in capital_map:
            return _with_template(f"the capital of {country.title()} is {capital_map[country]}.")

    if "what is gravity" in normalized:
        return _with_template("gravity is the force that attracts objects with mass toward each other.")
    if "what is photosynthesis" in normalized:
        return _with_template("photosynthesis is the process by which plants use sunlight, water, and carbon dioxide to produce food and oxygen.")
    if "what is atom" in normalized or "what is an atom" in normalized:
        return _with_template("an atom is the basic unit of matter, made of protons, neutrons, and electrons.")
    if "what is molecule" in normalized or "what is a molecule" in normalized:
        return _with_template("a molecule is a group of atoms chemically bonded together.")

    if normalized.startswith("what is ai"):
        return _with_template("AI, or artificial intelligence, is the field of building systems that can perform tasks that usually require human intelligence.")

    return ""


def _definition_answer(normalized: str, query_type: str) -> str:
    if query_type not in {"conceptual", "comparison"}:
        return ""

    if "difference between linked list and doubly linked list" in normalized:
        return _with_template(
            "a singly linked list stores a pointer to the next node, while a doubly linked list stores pointers to both next and previous nodes, enabling traversal in both directions."
        )
    if "difference between stack and queue" in normalized:
        return _with_template("a stack follows LIFO order, while a queue follows FIFO order.")
    if "difference between array and linked list" in normalized:
        return _with_template("arrays use contiguous memory and fast index access, while linked lists use node pointers and make insertions and deletions easier at known positions.")

    if normalized.startswith("define "):
        topic = normalized.removeprefix("define ").strip(" ?.")
        if topic:
            return _with_template(f"{topic} is a concept that refers to a core idea used to explain how something works.")

    if normalized.startswith(("what is ", "explain ")):
        topic = normalized.removeprefix("what is ").removeprefix("explain ").strip(" ?.")
        if topic and len(topic.split()) <= 4:
            return _with_template(f"{topic} is a concept that refers to how a specific idea, process, or system is understood and applied.")

    return ""


def _cs_answer(normalized: str, query_type: str) -> str:
    if query_type not in {"conceptual", "comparison", "algorithmic", "unknown"}:
        return ""

    if "linked list" in normalized and "doubly" in normalized:
        return _with_template(
            "a singly linked list has one pointer per node to the next node, while a doubly linked list has two pointers per node to the next and previous nodes."
        )
    if "linked list" in normalized:
        return _with_template("a linked list is a linear data structure where each node stores data and a reference to the next node.")
    if "stack" in normalized and "queue" not in normalized:
        return _with_template("a stack is a linear data structure that follows Last In, First Out ordering.")
    if "queue" in normalized and "priority queue" not in normalized:
        return _with_template("a queue is a linear data structure that follows First In, First Out ordering.")
    if "binary tree" in normalized:
        return _with_template("a binary tree is a hierarchical data structure where each node has at most two children, commonly called left and right.")
    if "sorting" in normalized or "sort algorithm" in normalized:
        return _with_template("sorting arranges elements in a specific order, and common algorithms include bubble sort, merge sort, quick sort, and heap sort.")
    if "time complexity" in normalized or "big o" in normalized:
        return _with_template("time complexity describes how runtime grows with input size, typically expressed using Big O notation like O(1), O(log n), O(n), or O(n log n).")
    if "oop concepts" in normalized or "object oriented programming concepts" in normalized:
        return _with_template("the core OOP concepts are encapsulation, inheritance, polymorphism, and abstraction.")
    if "polymorphism" in normalized:
        return _with_template("polymorphism in OOP means one interface can represent many forms, so the same method call can behave differently for different object types.")
    if "encapsulation" in normalized:
        return _with_template("encapsulation bundles data and methods together and limits direct access to internal state.")
    if "inheritance" in normalized:
        return _with_template("inheritance allows one class to reuse and extend fields and methods of another class.")
    if "abstraction" in normalized:
        return _with_template("abstraction exposes essential behavior while hiding implementation details.")
    if "oop" in normalized or "object oriented programming" in normalized:
        return _with_template(
            "object-oriented programming organizes code into objects with state and behavior, using concepts like encapsulation, inheritance, polymorphism, and abstraction."
        )
    return ""


def _algorithmic_answer(normalized: str, query_type: str) -> str:
    if query_type != "algorithmic":
        return ""

    if "palindrome" in normalized and "linked list" in normalized:
        return _with_template(
            "to check if a linked list is a palindrome, find the middle node, reverse the second half, compare both halves node by node, and optionally restore the list."
        )
    if ("reverse" in normalized and "linked list" in normalized) or ("how to" in normalized and "linked list" in normalized):
        return _with_template(
            "to reverse a linked list, iterate through nodes while repointing each next reference to the previous node, then move the head to the last processed node."
        )
    if "binary search" in normalized:
        return _with_template("for binary search, keep low and high pointers, check the middle value, and repeatedly narrow the half where the target can exist.")
    if "sorting" in normalized:
        return _with_template("a practical sorting approach is to choose merge sort for stable O(n log n) behavior or quick sort for fast average performance.")
    if "time complexity" in normalized:
        return _with_template("analyzing time complexity usually means counting dominant operations and expressing growth using Big O notation.")
    return ""


def _generate_lightweight_answer(normalized: str, query_type: str) -> str:
    if query_type not in {"conceptual", "algorithmic", "comparison"}:
        return ""
    if normalized.startswith(("give example", "an example", "example", "tell me more", "go on", "continue")):
        return ""
    if query_type == "comparison" and not any(
        keyword in normalized for keyword in ("linked list", "stack", "queue", "binary tree", "array", "sorting", "time complexity", "oop")
    ):
        return ""

    topic = normalized
    for prefix in ("what is ", "define ", "explain ", "how to ", "difference between "):
        if topic.startswith(prefix):
            topic = topic[len(prefix) :]
            break
    topic = topic.strip(" ?.")
    token_count = len(topic.split())
    if not topic or token_count > 10:
        return ""
    if query_type == "conceptual" and token_count > 4:
        return ""
    if query_type == "comparison" and token_count > 8:
        return ""
    return _with_template(f"{topic} is an important concept. It generally involves understanding the core idea, common use cases, and trade-offs.")


def _is_followup_query(normalized: str) -> bool:
    return normalized.startswith(("give example", "an example", "example", "tell me more", "go on", "continue"))


def _with_template(message: str) -> str:
    global _TEMPLATE_CURSOR
    template = TEMPLATES[_TEMPLATE_CURSOR % len(TEMPLATES)]
    _TEMPLATE_CURSOR += 1
    return template.format(message)


def _success(text: str, confidence: float, source: str, kind: str) -> dict[str, str | bool | float]:
    final_confidence = float(confidence)
    if final_confidence < LOCAL_CONFIDENCE_THRESHOLD:
        return {
            "success": False,
            "text": "",
            "error": "confidence_below_threshold",
            "confidence": final_confidence,
            "source": "local_failure",
            "kind": kind,
        }
    return {
        "success": True,
        "text": text,
        "error": "",
        "confidence": final_confidence,
        "source": source,
        "kind": kind,
    }


def get_local_confidence_threshold() -> float:
    return LOCAL_CONFIDENCE_THRESHOLD


def get_templates() -> list[str]:
    return list(TEMPLATES)


def get_integration_snippet() -> str:
    return """cached = answer_memory.retrieve(query)
if cached:
    return cached

local_result = try_local_answer(query)
if local_result.get("success"):
    return local_result["text"]

ai_result = try_ai_answer(query)
if ai_result.get("success"):
    return ai_result["text"]

return "I'm not getting a clean answer. Want me to search it?"
"""
