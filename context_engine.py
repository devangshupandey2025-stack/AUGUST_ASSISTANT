from __future__ import annotations

import datetime
from collections import deque
from typing import Any

from utils.logger import get_logger

logger = get_logger("Context")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="IST")


class ContextEngine:
    def __init__(self, history_size: int = 8) -> None:
        self._recent_commands: deque[str] = deque(maxlen=history_size)
        self._last_interaction_at = datetime.datetime.now(IST)

    def get_time_of_day(self, now: datetime.datetime | None = None) -> str:
        current = now.astimezone(IST) if now else datetime.datetime.now(IST)
        if current.hour < 12:
            return "morning"
        if current.hour < 18:
            return "afternoon"
        return "night"

    def touch(self, command_text: str | None = None) -> None:
        self._last_interaction_at = datetime.datetime.now(IST)
        if command_text:
            self._recent_commands.append(command_text)

    def snapshot(self) -> dict[str, Any]:
        return {
            "time_of_day": self.get_time_of_day(),
            "recent_commands": list(self._recent_commands),
            "last_interaction_at": self._last_interaction_at.isoformat(),
        }

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
        if self._recent_commands:
            return f"{salutation}, {user_name}. I'm back and ready to help."
        return f"{salutation}, {user_name}."

    def describe_recent_activity(self) -> str:
        if not self._recent_commands:
            return ""
        recent = list(self._recent_commands)[-2:]
        return "Recently you asked about " + " and ".join(recent) + "."
