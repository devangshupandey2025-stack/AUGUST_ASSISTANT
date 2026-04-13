from __future__ import annotations

import re
from collections import deque

PERSONALITY = {
    "tone": "calm, confident, slightly witty",
    "verbosity": "low",
    "style": "natural, not robotic",
}

SEARCH_RESPONSES = [
    "Let me check that.",
    "Looking that up.",
    "One sec, searching.",
]

ACK_RESPONSES = [
    "Got it.",
    "Alright.",
    "Okay.",
]

STARTUP_GREETINGS = [
    "Good afternoon. Ready when you are.",
    "Hey. What do you need?",
    "Let's get started.",
]

ACTION_MICRO_RESPONSES = {
    "open_app": ["Opening it.", "On it.", "Done. Opening it."],
    "close_app": ["Done.", "Closing it.", "All set."],
    "search_web": SEARCH_RESPONSES,
}

ANSWER_PREFIXES = [
    "In simple terms, ",
    "Here's the idea: ",
]

CASUAL_RESPONSES = {
    "what's up": "I'm good. What about you?",
    "whats up": "I'm good. What about you?",
    "how are you": "I'm good. What about you?",
    "how r u": "I'm good. What about you?",
}

HOOKS = [
    " Want an example?",
    " Need more detail?",
]


class PersonalityEngine:
    def __init__(self) -> None:
        self._history: deque[str] = deque(maxlen=3)
        self._cursor: dict[str, int] = {}

    def casual_response(self, normalized_text: str) -> str:
        return CASUAL_RESPONSES.get(normalized_text, "")

    def variation(self, category: str) -> str:
        pools = {
            "search": SEARCH_RESPONSES,
            "ack": ACK_RESPONSES,
            "startup": STARTUP_GREETINGS,
        }
        options = pools.get(category, ACK_RESPONSES)
        return self._pick(category, options)

    def micro_response(self, action: str) -> str:
        options = ACTION_MICRO_RESPONSES.get(action, ACK_RESPONSES)
        return self._pick(f"micro:{action}", options)

    def classify_response_type(self, text: str) -> str:
        lowered = " ".join((text or "").split()).lower()
        if not lowered:
            return "system"
        if lowered in CASUAL_RESPONSES:
            return "casual"
        if lowered.startswith(("opening ", "closing ", "searching ", "done.", "got it.", "okay.", "alright.")):
            return "action"
        if "want me to search" in lowered or "not getting that right now" in lowered:
            return "failure"
        if lowered.startswith(("you've got ", "you have ", "today's schedule")):
            return "schedule"
        if any(marker in lowered for marker in ("current", "capital", "prime minister", "president")):
            return "factual"
        if any(marker in lowered for marker in (" is ", " means ", "refers to", "concept", "data structure")):
            return "conceptual"
        return "system"

    def render_for_tts(self, text: str) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return ""

        lowered = cleaned.lower()
        if lowered.startswith(("good morning,", "good afternoon,", "good evening,")):
            return self.variation("startup")
        if lowered.startswith("here is your schedule for today."):
            return self._format_schedule(cleaned)
        if lowered.startswith(("searching the web for", "opening google for", "opening youtube results for")):
            return self.variation("search")
        if lowered.startswith(("opening ", "launching ")):
            return self.micro_response("open_app")
        if lowered.startswith(("closing ", "closed ")):
            return self.micro_response("close_app")
        if "couldn't get a clean answer" in lowered or "not getting a clean answer" in lowered:
            return "Hmm... not getting that right now. Want me to look it up?"

        response_type = self.classify_response_type(cleaned)
        if response_type == "conceptual":
            return self._with_sparse_prefix(cleaned)
        return cleaned

    def add_optional_hook(self, text: str) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned or cleaned.endswith("?"):
            return cleaned
        if self.classify_response_type(cleaned) != "conceptual":
            return cleaned
        # Sparse hooks: every 4th conceptual response.
        hook_index = self._cursor.get("hook", 0)
        self._cursor["hook"] = hook_index + 1
        if hook_index % 4 != 0:
            return cleaned
        hook = HOOKS[(hook_index // 4) % len(HOOKS)]
        return cleaned + hook

    def split_for_speech(self, text: str, max_len: int = 120) -> list[str]:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return []
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        chunks: list[str] = []
        for part in parts:
            if len(part) <= max_len:
                chunks.append(part)
                continue
            words = part.split()
            current: list[str] = []
            for word in words:
                current.append(word)
                if len(" ".join(current)) >= max_len:
                    chunks.append(" ".join(current))
                    current = []
            if current:
                chunks.append(" ".join(current))
        return chunks

    def _pick(self, key: str, options: list[str]) -> str:
        if not options:
            return ""
        index = self._cursor.get(key, 0) % len(options)
        candidate = options[index]
        self._cursor[key] = self._cursor.get(key, 0) + 1
        if self._history and candidate == self._history[-1]:
            candidate = options[(index + 1) % len(options)]
        self._history.append(candidate)
        return candidate

    def _with_sparse_prefix(self, text: str) -> str:
        lowered = text.lower()
        if lowered.startswith(("in simple terms,", "here's the idea:")):
            return text
        prefix_counter = self._cursor.get("concept_prefix", 0)
        self._cursor["concept_prefix"] = prefix_counter + 1
        if prefix_counter % 3 != 0:
            return text
        prefix = ANSWER_PREFIXES[(prefix_counter // 3) % len(ANSWER_PREFIXES)]
        if text[:1].isupper():
            return prefix + text[:1].lower() + text[1:]
        return prefix + text

    def _format_schedule(self, original: str) -> str:
        details = original[len("Here is your schedule for today.") :].strip()
        if not details:
            return "You've got 0 events today."
        segments = [segment.strip() for segment in re.split(r"[.;]", details) if segment.strip()]
        count = len(segments) if segments else 1
        return f"You've got {count} events today. {details}"


personality_engine = PersonalityEngine()
