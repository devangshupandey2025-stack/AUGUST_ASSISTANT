from __future__ import annotations

import datetime
import re
from collections import deque
from typing import Any

from config import config
from followup_utils import is_follow_up_query
from utils.logger import get_logger, log_event

logger = get_logger("Context")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="IST")


class ContextEngine:
    INTERACTION_TIMEOUT_SECONDS = 20
    INVALID_CONTEXT_INPUTS = {"yes", "no", "search", "search it", "it"}
    CASUAL_LAST_QUERY_INPUTS = {"how are you", "how r u", "what's up", "whats up"}

    FOLLOW_UP_HINTS = {
        "yes",
        "no",
        "answer",
        "search",
        "tell me",
        "explain",
        "look it up",
    }
    CONTINUATION_HINTS = {
        "give example",
        "example",
        "tell me more",
        "go on",
        "continue",
        "explain more",
        "why",
    }
    RESET_HINTS = {"reset context", "clear context", "reset conversation", "forget conversation"}
    WAKE_HINTS = {"august", "hey august", "ok august", "jarvis", "hey jarvis", "ok jarvis"}

    def __init__(self, history_size: int = 5) -> None:
        self._recent_commands: deque[str] = deque(maxlen=history_size)
        self._last_interaction_at = datetime.datetime.now(IST)
        self._last_query = ""
        self._last_answer = ""
        self._last_action = ""
        self._last_app = ""
        self._pending_interaction: dict[str, Any] | None = None
        self._conversation_history: deque[dict[str, str]] = deque(maxlen=history_size)
        self._candidate_query = ""

    def get_time_of_day(self, now: datetime.datetime | None = None) -> str:
        current = now.astimezone() if now else datetime.datetime.now().astimezone()
        if current.hour < 12:
            return "morning"
        if current.hour < 18:
            return "afternoon"
        return "night"

    def update_context(
        self,
        command_text: str | None = None,
        plan: Any | None = None,
        last_action: str | None = None,
        last_app: str | None = None,
        pending_interaction: dict[str, Any] | None = None,
        pending_clarification: dict[str, Any] | None = None,
        response_text: str | None = None,
        skip_query_update: bool = False,
    ) -> dict[str, Any]:
        self._last_interaction_at = datetime.datetime.now(IST)
        normalized_command = self._normalize(command_text)
        auto_skip_query_update = bool(normalized_command and is_follow_up_query(normalized_command))
        if auto_skip_query_update:
            skip_query_update = True
            log_event(logger, "followup_detected", source="context", success=True, query=normalized_command)

        if normalized_command:
            self._recent_commands.append(normalized_command)
            if not skip_query_update:
                self._candidate_query = normalized_command
            if self._is_reset_command(normalized_command):
                self._reset_context(keep_recent=True)
                snapshot = self.get_context()
                logger.info("Context reset via explicit command")
                logger.debug("Context updated: %s", snapshot)
                return snapshot
            if self._pending_interaction and self._is_unrelated_command(normalized_command):
                self._pending_interaction = None
                logger.info("Context cleared pending interaction due to unrelated command")

        command_app, command_action = self._extract_plan_state(plan)
        if command_action:
            self._last_action = command_action
            if command_action == "open_app" and command_app:
                self._last_app = command_app
            if not skip_query_update and command_action == "search_web" and self._should_capture_answer_query(normalized_command):
                self._last_query = normalized_command
                logger.info("last_query updated -> %s", self._last_query)
            if response_text:
                self._last_answer = response_text.strip()
            self._append_history(self._last_query, self._last_answer)
            logger.info("Context updated from plan action='%s' app='%s'", command_action, self._last_app)

        if last_app:
            action_hint = (last_action or self._last_action or "").strip().lower()
            if action_hint == "open_app":
                self._last_app = last_app.strip().lower()
                logger.info("Context updated last_app='%s' explicitly", self._last_app)

        if last_action:
            normalized_action = last_action.strip().lower()
            self._last_action = normalized_action
            if not skip_query_update and normalized_action == "answer_query" and self._should_capture_answer_query(self._candidate_query):
                self._last_query = self._candidate_query
                logger.info("last_query updated -> %s", self._last_query)
                if response_text:
                    self._last_answer = response_text.strip()
                self._append_history(self._last_query, self._last_answer)
            logger.info("Context updated last_action='%s' explicitly", self._last_action)

        pending_payload = pending_interaction if pending_interaction is not None else pending_clarification
        if pending_payload is not None:
            self._pending_interaction = self._normalize_pending_interaction(pending_payload)
            if self._pending_interaction:
                logger.info("Context updated pending interaction type='%s'", self._pending_interaction.get("type", ""))
            else:
                logger.info("Context cleared pending interaction")

        snapshot = self.get_context()
        logger.debug("Context updated: %s", snapshot)
        return snapshot

    def get_context(self) -> dict[str, Any]:
        self._clear_expired_pending_interaction()
        pending = dict(self._pending_interaction) if self._pending_interaction else None
        return {
            "last_query": self._last_query,
            "last_answer": self._last_answer,
            "last_action": self._last_action,
            "last_app": self._last_app,
            "pending_interaction": pending,
            "conversation_history": list(self._conversation_history),
            "timestamp": self._last_interaction_at.isoformat(),
            # Backward-compatible keys:
            "recent_commands": list(self._recent_commands),
            "time_of_day": self.get_time_of_day(),
            "last_interaction_at": self._last_interaction_at.isoformat(),
            "pending_clarification": pending,
        }

    def touch(self, command_text: str | None = None) -> None:
        self.update_context(command_text=command_text)

    def snapshot(self) -> dict[str, Any]:
        return self.get_context()

    def set_pending_interaction(self, pending: dict[str, Any]) -> None:
        self._pending_interaction = self._normalize_pending_interaction(pending)

    def clear_pending_interaction(self) -> None:
        self._pending_interaction = None

    def resolve_interaction(self, user_input: str) -> dict[str, Any] | None:
        self._clear_expired_pending_interaction()
        if not self._pending_interaction:
            return None
        normalized = self._normalize(user_input)
        pending = dict(self._pending_interaction)
        pending_type = str(pending.get("type", "")).strip().lower()
        options = [str(item).strip().lower() for item in pending.get("options", []) if str(item).strip()]
        if not normalized:
            return {"status": "pending", "type": pending_type, "original_query": pending.get("original_query", ""), "options": options}
        if normalized in {"no", "nope", "cancel", "stop"}:
            self._pending_interaction = None
            return {"status": "cancelled", "type": pending_type}
        if normalized in {"yes", "answer", "search"} | self.FOLLOW_UP_HINTS:
            self._pending_interaction = None
            return {
                "status": "resolved",
                "type": pending_type,
                "choice": normalized,
                "original_query": str(pending.get("original_query", "")).strip(),
                "options": options,
            }
        if self._is_unrelated_command(normalized):
            self._pending_interaction = None
            return {"status": "bypassed", "type": pending_type}
        return {"status": "pending", "type": pending_type, "original_query": pending.get("original_query", ""), "options": options}

    # Backward-compatible names:
    def set_pending_clarification(self, pending: dict[str, Any]) -> None:
        self.set_pending_interaction(pending)

    def clear_pending_clarification(self) -> None:
        self.clear_pending_interaction()

    def is_idle_for(self, seconds: int) -> bool:
        delta = datetime.datetime.now(IST) - self._last_interaction_at
        return delta.total_seconds() >= seconds

    def build_adaptive_greeting(self, user_name: str) -> str:
        time_of_day = self.get_time_of_day()
        salutation = {
            "morning": "Good morning",
            "afternoon": "Good afternoon",
            "night": "Good evening",
        }[time_of_day]
        user_name = (user_name or config.user_name).strip() or "User"
        if self._recent_commands:
            return f"{salutation}, {user_name}. I'm back and ready to help."
        return f"{salutation}, {user_name}."

    def describe_recent_activity(self) -> str:
        if not self._recent_commands:
            return ""
        recent = list(self._recent_commands)[-2:]
        return "Recently you asked about " + " and ".join(recent) + "."

    def _extract_plan_state(self, plan: Any | None) -> tuple[str, str]:
        if not plan:
            return "", ""
        commands = getattr(plan, "commands", None) or []
        if not commands:
            return "", ""
        latest = commands[-1]
        action = str(getattr(latest, "action", "") or "").strip().lower()
        payload = getattr(latest, "payload", {}) or {}
        app = str(payload.get("app", "") or "").strip().lower()
        return app, action

    def _normalize(self, text: str | None) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def _append_history(self, query: str, response: str) -> None:
        clean_query = (query or "").strip()
        if not clean_query:
            return
        self._conversation_history.append(
            {
                "query": clean_query,
                "response": (response or "").strip(),
            }
        )

    def _normalize_pending_interaction(self, pending: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(pending, dict):
            return None
        pending_type = str(pending.get("type", "") or "").strip().lower()
        original_query = str(pending.get("original_query", "") or "").strip()
        if not pending_type:
            return None
        options = [str(item).strip().lower() for item in pending.get("options", []) if str(item).strip()]
        ts = pending.get("timestamp")
        timestamp = self._parse_timestamp(ts) or datetime.datetime.now(IST)
        if pending_type == "app_disambiguation":
            pending_type = "app_clarification"
        if pending_type == "search_offer":
            pending_type = "answer_vs_search"
        return {
            "type": pending_type,
            "original_query": original_query,
            "options": options[:2],
            "timestamp": timestamp.isoformat(),
        }

    def _parse_timestamp(self, value: Any) -> datetime.datetime | None:
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(float(value), tz=IST)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST)
        return None

    def _clear_expired_pending_interaction(self) -> None:
        if not self._pending_interaction:
            return
        ts = self._parse_timestamp(self._pending_interaction.get("timestamp"))
        if ts is None:
            self._pending_interaction = None
            return
        age = (datetime.datetime.now(IST) - ts).total_seconds()
        if age > self.INTERACTION_TIMEOUT_SECONDS:
            self._pending_interaction = None

    def _is_reset_command(self, normalized_command: str) -> bool:
        return normalized_command in self.RESET_HINTS

    def _is_unrelated_command(self, normalized_command: str) -> bool:
        if not normalized_command or normalized_command in self.WAKE_HINTS:
            return False
        if normalized_command in self.FOLLOW_UP_HINTS or normalized_command in self.CONTINUATION_HINTS:
            return False
        if normalized_command.startswith(("open ", "close ", "search ", "play ", "watch ", "launch ", "start ", "run ", "remind ", "set ")):
            return True
        return False

    def _should_capture_answer_query(self, normalized_query: str) -> bool:
        normalized = self._normalize(normalized_query)
        if not normalized or normalized in self.WAKE_HINTS:
            return False
        if is_follow_up_query(normalized):
            return False
        if self._should_update_last_query(normalized):
            return True
        return self._is_forced_question_query(normalized)

    def _should_update_last_query(self, query: str) -> bool:
        normalized = self._normalize(query)
        if not normalized:
            return False
        if is_follow_up_query(normalized):
            return False
        if normalized in self.INVALID_CONTEXT_INPUTS:
            return False
        if normalized in self.CASUAL_LAST_QUERY_INPUTS:
            return False
        return len(normalized.split()) >= 3

    def _is_forced_question_query(self, query: str) -> bool:
        normalized = self._normalize(query)
        if not normalized:
            return False
        if normalized in self.CASUAL_LAST_QUERY_INPUTS:
            return False
        if any(
            normalized.startswith(prefix)
            for prefix in (
                "what ",
                "who ",
                "why ",
                "how ",
                "which ",
                "define ",
                "explain ",
                "tell me",
                "difference between",
                "compare ",
            )
        ):
            return True
        if any(marker in normalized for marker in ("difference between", "compare ", "vs ", " versus ")):
            return True
        if any(marker in normalized for marker in ("algorithm", "complexity", "binary search", "dynamic programming", "sorting")):
            return True
        return False

    def _reset_context(self, keep_recent: bool) -> None:
        self._last_query = ""
        self._last_answer = ""
        self._last_action = ""
        self._last_app = ""
        self._pending_interaction = None
        self._conversation_history.clear()
        self._candidate_query = ""
        if not keep_recent:
            self._recent_commands.clear()
