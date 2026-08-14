from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from august.config import config
from august.utils.logger import get_logger

logger = get_logger("IntentParser")

SUPPORTED_ACTIONS = {
    "open_app",
    "close_app",
    "search_web",
    "shutdown",
    "restart",
    "volume_control",
    "calendar_today",
    "create_reminder",
    "current_time",
    "current_date",
    "greeting",
    "generate_document",
    "web_research",
}


@dataclass
class ParsedCommand:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "rule"
    priority: int = 50
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "payload": dict(self.payload),
            "source": self.source,
            "priority": self.priority,
            "requires_confirmation": self.requires_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "memory") -> "ParsedCommand":
        payload = data.get("payload")
        if payload is None:
            payload = {
                key: value
                for key, value in data.items()
                if key not in {"action", "source", "priority", "requires_confirmation"}
            }
        return cls(
            action=str(data["action"]),
            payload=dict(payload or {}),
            source=str(data.get("source", source)),
            priority=int(data.get("priority", 50)),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
        )


@dataclass
class CommandPlan:
    commands: list[ParsedCommand]
    raw_text: str
    source: str = "rule"

    def requires_confirmation(self) -> bool:
        return any(command.requires_confirmation for command in self.commands)


class IntentParser:
    def __init__(self, memory_store: Any | None = None) -> None:
        self.memory_store = memory_store
        self.open_verbs = ("open", "launch", "start", "run")
        self.close_verbs = ("close", "exit", "quit", "terminate", "kill")
        self.search_verbs = ("search", "google", "find", "look up")

    def parse(self, text: str | None) -> CommandPlan | None:
        if not text:
            return None

        normalized = self._normalize(text)
        logger.debug("Rule parser received normalized text: %s", normalized)

        learned = self._parse_learned_pattern(normalized)
        if learned:
            logger.info("Learned phrase matched for text '%s'", normalized)
            return learned

        plan = (
            self._parse_system_action(normalized)
            or self._parse_calendar(normalized)
            or self._parse_web_search(normalized)
            or self._parse_reminder(normalized)
            or self._parse_open_command(normalized)
            or self._parse_close_command(normalized)
            or self._parse_volume(normalized)
        )
        if plan:
            logger.info("Rule parser matched %s step(s) from text '%s'", len(plan.commands), normalized)
        else:
            logger.info("Rule parser found no match for text '%s'", normalized)
        return plan

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _strip_politeness(self, text: str) -> str:
        cleaned = text
        wake_phrase = re.escape((config.wake_phrase or "").strip().lower())
        wake_patterns = []
        if wake_phrase:
            wake_patterns.extend(
                (
                    rf"^(hey\s+{wake_phrase})\s+",
                    rf"^(ok\s+{wake_phrase})\s+",
                    rf"^({wake_phrase})\s+",
                )
            )
        for pattern in (
            r"^(please)\s+",
            r"^(can you)\s+",
            r"^(could you)\s+",
            r"^(would you)\s+",
            r"^(tell me)\s+",
            *wake_patterns,
            r"^(assistant)\s+",
        ):
            cleaned = re.sub(pattern, "", cleaned).strip()
        return cleaned

    def _plan(self, raw_text: str, command: ParsedCommand) -> CommandPlan:
        return CommandPlan(commands=[command], raw_text=raw_text, source=command.source)

    def _parse_learned_pattern(self, text: str) -> CommandPlan | None:
        if not self.memory_store:
            return None
        learned_commands = self.memory_store.get_learned_plan(text)
        if not learned_commands:
            return None
        commands = [ParsedCommand.from_dict(item, source="memory") for item in learned_commands]
        for command in commands:
            command.source = "memory"
        return CommandPlan(commands=commands, raw_text=text, source="memory")

    def _parse_system_action(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        if re.search(r"\b(shut down|shutdown|power off|turn off the computer)\b", cleaned):
            return self._plan(text, ParsedCommand(action="shutdown", priority=100, requires_confirmation=True))
        if re.search(r"\b(restart|reboot)\b", cleaned):
            return self._plan(text, ParsedCommand(action="restart", priority=100, requires_confirmation=True))
        return None

    def _parse_calendar(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        if re.search(r"\b(what is my schedule|what's my schedule|what's on my schedule|schedule today|calendar|events today)\b", cleaned):
            return self._plan(text, ParsedCommand(action="calendar_today", priority=95))
        return None

    def _parse_web_search(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)

        shorthand_match = re.match(r"^(youtube|google|gmail|github|reddit)\s+for\s+(.+)$", cleaned)
        if shorthand_match:
            return self._plan(
                text,
                ParsedCommand(
                    action="search_web",
                    payload={"site": shorthand_match.group(1), "query": shorthand_match.group(2).strip()},
                    priority=90,
                ),
            )

        watch_match = re.match(r"^(watch|play)\s+(.+)$", cleaned)
        if watch_match:
            return self._plan(
                text,
                ParsedCommand(
                    action="search_web",
                    payload={"query": watch_match.group(2).strip(), "site": "youtube"},
                    priority=90,
                ),
            )

        explicit_match = re.match(rf"^({'|'.join(self.search_verbs)})\s+(for\s+)?(.+)$", cleaned)
        if explicit_match:
            return self._plan(
                text,
                ParsedCommand(
                    action="search_web",
                    payload={"query": explicit_match.group(3).strip(), "site": "google"},
                    priority=85,
                ),
            )

        open_web_match = re.match(r"^open\s+(youtube|google|gmail|github|reddit)\b(?:\s+for\s+(.+))?$", cleaned)
        if open_web_match:
            payload = {"site": open_web_match.group(1)}
            if open_web_match.group(2):
                payload["query"] = open_web_match.group(2).strip()
            return self._plan(text, ParsedCommand(action="search_web", payload=payload, priority=90))

        return None

    def _parse_reminder(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        match = re.match(
            r"^(remind me to|set a reminder to|set reminder to)\s+(.+?)\s+(in\s+\d+\s+(?:seconds?|minutes?|hours?))$",
            cleaned,
        )
        if not match:
            return None

        return self._plan(
            text,
            ParsedCommand(
                action="create_reminder",
                payload={"task": match.group(2).strip(), "time_text": match.group(3).strip()},
                priority=80,
            ),
        )

    def _parse_open_command(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        match = re.match(rf"^({'|'.join(self.open_verbs)})\s+(.+)$", cleaned)
        if not match:
            return None

        app_name = self._cleanup_app_name(match.group(2))
        if not app_name:
            return None
        if re.match(r"^(google|youtube|gmail|github|reddit)\b", app_name):
            return None

        return self._plan(
            text,
            ParsedCommand(
                action="open_app",
                payload={"app": config.resolve_app_name(app_name)},
                priority=70,
            ),
        )

    def _parse_close_command(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        match = re.match(rf"^({'|'.join(self.close_verbs)})\s+(.+)$", cleaned)
        if not match:
            return None

        app_name = self._cleanup_app_name(match.group(2))
        if not app_name:
            return None

        return self._plan(
            text,
            ParsedCommand(
                action="close_app",
                payload={"app": config.resolve_app_name(app_name)},
                priority=70,
            ),
        )

    def _parse_volume(self, text: str) -> CommandPlan | None:
        cleaned = self._strip_politeness(text)
        if "volume" not in cleaned and "sound" not in cleaned:
            return None

        if re.search(r"\b(increase|raise|up|louder|turn up)\b", cleaned):
            return self._plan(text, ParsedCommand(action="volume_control", payload={"level": "up"}))
        if re.search(r"\b(decrease|lower|down|quieter|turn down)\b", cleaned):
            return self._plan(text, ParsedCommand(action="volume_control", payload={"level": "down"}))
        if re.search(r"\b(mute|silent|silence)\b", cleaned):
            return self._plan(text, ParsedCommand(action="volume_control", payload={"level": "mute"}))
        if re.search(r"\b(unmute|restore sound)\b", cleaned):
            return self._plan(text, ParsedCommand(action="volume_control", payload={"level": "unmute"}))
        return None

    def _cleanup_app_name(self, value: str) -> str:
        cleaned = re.sub(r"\b(app|application|please)\b", "", value).strip()
        return config.resolve_app_name(cleaned)
