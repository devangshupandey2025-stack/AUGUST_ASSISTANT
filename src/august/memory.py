from __future__ import annotations

import copy
import json
import threading
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from august.config import config
from august.utils.logger import get_logger

logger = get_logger("Memory")
MEMORY_FILE = Path(config.files.get("memory", "memory.json"))


class MemoryStore:
    def __init__(self, memory_path: Path | str = MEMORY_FILE) -> None:
        self.memory_path = Path(memory_path)
        self._lock = threading.RLock()
        self._data = self._load()
        self._execution_learning_enabled = False

    def _default_state(self) -> dict[str, Any]:
        return {
            "profile": {
                "user_name": config.user_name,
            },
            "habits": {
                "frequent_apps": {},
                "time_of_day_usage": {
                    "morning": {},
                    "afternoon": {},
                    "night": {},
                },
            },
            "command_history": [],
            "learned_patterns": {},
            "pattern_counters": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.memory_path.exists():
            state = self._default_state()
            self._write(state)
            return state

        try:
            raw = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load memory from %s: %s", self.memory_path, exc)
            raw = self._default_state()
            self._write(raw)
            return raw

        state = self._default_state()
        state.update(raw)
        state["profile"] = {**self._default_state()["profile"], **raw.get("profile", {})}
        state["habits"] = {**self._default_state()["habits"], **raw.get("habits", {})}
        habits = state["habits"]
        habits["time_of_day_usage"] = {
            **self._default_state()["habits"]["time_of_day_usage"],
            **raw.get("habits", {}).get("time_of_day_usage", {}),
        }
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self.memory_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def save(self) -> None:
        with self._lock:
            self._write(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get_user_name(self) -> str:
        with self._lock:
            return str(self._data["profile"].get("user_name") or config.user_name)

    def set_user_name(self, value: str) -> None:
        if not value.strip():
            return
        with self._lock:
            self._data["profile"]["user_name"] = value.strip()
            self._write(self._data)

    def get_learned_plan(self, phrase: str) -> list[dict[str, Any]] | None:
        if not self._execution_learning_enabled:
            return None
        with self._lock:
            return copy.deepcopy(self._data.get("learned_patterns", {}).get(phrase))

    def record_interaction(
        self,
        raw_text: str,
        plan: Any,
        response_message: str,
        context: dict[str, Any],
    ) -> None:
        normalized_text = (raw_text or "").strip().lower()
        if not normalized_text:
            return

        commands = []
        for command in getattr(plan, "commands", []):
            serialized = asdict(command)
            commands.append(serialized)

        with self._lock:
            history = self._data.setdefault("command_history", [])
            history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "text": normalized_text,
                    "source": getattr(plan, "source", "unknown"),
                    "commands": commands,
                    "response": response_message,
                    "context": context,
                }
            )
            self._data["command_history"] = history[-200:]
            self._update_habits(commands, context.get("time_of_day", "afternoon"))
            self._update_learning(normalized_text, commands)
            self._write(self._data)

    def _update_habits(self, commands: list[dict[str, Any]], time_of_day: str) -> None:
        frequent_apps = self._data["habits"].setdefault("frequent_apps", {})
        time_usage = self._data["habits"].setdefault("time_of_day_usage", {})
        slot_usage = time_usage.setdefault(time_of_day, {})
        for command in commands:
            if command.get("action") != "open_app":
                continue
            app_name = str(command.get("payload", {}).get("app", "")).strip()
            if not app_name:
                continue
            frequent_apps[app_name] = int(frequent_apps.get(app_name, 0)) + 1
            slot_usage[app_name] = int(slot_usage.get(app_name, 0)) + 1

    def _update_learning(self, normalized_text: str, commands: list[dict[str, Any]]) -> None:
        if not commands:
            return

        counters = self._data.setdefault("pattern_counters", {})
        counters[normalized_text] = int(counters.get(normalized_text, 0)) + 1
        count = counters[normalized_text]
        if count >= 3:
            logger.info("Learning repeated command phrase '%s'", normalized_text)
            self._data.setdefault("learned_patterns", {})[normalized_text] = commands

    def get_recent_command_texts(self, limit: int = 5) -> list[str]:
        with self._lock:
            history = self._data.get("command_history", [])
            return [entry.get("text", "") for entry in history[-limit:]]

    def get_last_app(self) -> str:
        with self._lock:
            history = list(reversed(self._data.get("command_history", [])))
            for entry in history:
                for command in reversed(entry.get("commands", [])):
                    payload = command.get("payload", {})
                    app_name = str(payload.get("app", "")).strip().lower()
                    if app_name:
                        return app_name
            return ""

    def get_last_action(self) -> str:
        with self._lock:
            history = list(reversed(self._data.get("command_history", [])))
            for entry in history:
                for command in reversed(entry.get("commands", [])):
                    action = str(command.get("action", "")).strip().lower()
                    if action:
                        return action
            return ""

    def suggest_next_app(self, time_of_day: str) -> str:
        suggestions = self.suggest_frequent_apps(time_of_day, limit=1)
        return suggestions[0] if suggestions else ""

    def suggest_frequent_apps(self, time_of_day: str, limit: int = 2) -> list[str]:
        with self._lock:
            usage = self._data.get("habits", {}).get("time_of_day_usage", {}).get(time_of_day, {})
            if not usage:
                usage = self._data.get("habits", {}).get("frequent_apps", {})
            ranked = Counter(usage).most_common(limit)
            return [app for app, _ in ranked]
