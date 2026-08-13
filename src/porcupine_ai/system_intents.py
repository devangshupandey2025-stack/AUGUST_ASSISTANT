from __future__ import annotations

import datetime
import re

from context_engine import IST
from intent_parser import CommandPlan, ParsedCommand
from utils.logger import get_logger

logger = get_logger("SystemIntents")


class SystemIntentResolver:
    def resolve(self, text: str) -> CommandPlan | None:
        if not text:
            return None

        if self._is_calendar_query(text):
            return CommandPlan(
                commands=[ParsedCommand(action="calendar_today", source="system", priority=100)],
                raw_text=text,
                source="system",
            )

        if self._is_time_query(text):
            return CommandPlan(
                commands=[ParsedCommand(action="current_time", source="system", priority=100)],
                raw_text=text,
                source="system",
            )

        if self._is_date_query(text):
            return CommandPlan(
                commands=[ParsedCommand(action="current_date", source="system", priority=100)],
                raw_text=text,
                source="system",
            )

        if self._is_greeting(text):
            return CommandPlan(
                commands=[ParsedCommand(action="greeting", source="system", priority=100)],
                raw_text=text,
                source="system",
            )

        return None

    def _is_time_query(self, text: str) -> bool:
        return bool(re.search(r"\b(what time is it|time right now|current time|tell me the time)\b", text))

    def _is_date_query(self, text: str) -> bool:
        return bool(re.search(r"\b(what date is it|today's date|current date|date today)\b", text))

    def _is_calendar_query(self, text: str) -> bool:
        return bool(re.search(r"\b(what is my schedule|what's my schedule|what's on my schedule|calendar|events today|schedule today)\b", text))

    def _is_greeting(self, text: str) -> bool:
        return text in {"hello", "hi", "hey", "good morning", "good afternoon", "good evening"}


def formatted_time() -> str:
    return datetime.datetime.now(IST).strftime("%I:%M %p")


def formatted_date() -> str:
    return datetime.datetime.now(IST).strftime("%A, %d %B %Y")
