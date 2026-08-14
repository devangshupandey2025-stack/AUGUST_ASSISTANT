from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from august.utils.logger import get_logger

logger = get_logger("Config")
CONFIG_FILE = Path("config.json")


@dataclass(frozen=True)
class AppConfig:
    name: str
    path: str
    process_name: str


class Config:
    def __init__(self, config_path: Path | str = CONFIG_FILE) -> None:
        self.config_path = Path(config_path)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            logger.warning("Config file not found at %s. Using defaults.", self.config_path)
            return {}

        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", self.config_path, exc)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def user_name(self) -> str:
        return self.get("user_name", "User")

    @property
    def wake_phrase(self) -> str:
        return self.get("wake_phrase", "daddy's home")

    @property
    def ollama(self) -> dict[str, Any]:
        defaults = {
            "url": "http://localhost:11434/api/generate",
            "model": "llama3",
            "timeout_seconds": 30,
            "max_retries": 2,
        }
        defaults.update(self.get("ollama", {}))
        return defaults

    @property
    def gemini(self) -> dict[str, Any]:
        defaults = {
            "api_url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            "model": "gemini-2.5-flash",
            "timeout_seconds": 30,
            "max_retries": 2,
            "api_key": "",
        }
        defaults.update(self.get("gemini", {}))
        if not defaults.get("api_key"):
            defaults["api_key"] = os.getenv("GEMINI_API_KEY", "")
        return defaults

    @property
    def speech(self) -> dict[str, Any]:
        defaults = {
            "ambient_adjust_seconds": 1,
            "wake_phrase_limit_seconds": 3,
            "command_timeout_seconds": 5,
            "command_phrase_limit_seconds": 8,
            "wake_fuzzy_threshold": 80,
            "language": "en-US",
        }
        defaults.update(self.get("speech", {}))
        return defaults

    @property
    def tts(self) -> dict[str, Any]:
        defaults = {
            "rate": 165,
            "preferred_voice": "zira",
        }
        defaults.update(self.get("tts", {}))
        return defaults

    @property
    def app_paths(self) -> dict[str, str]:
        return self.get("paths", {}).get("apps", {})

    @property
    def app_processes(self) -> dict[str, str]:
        return self.get("process_names", {})

    @property
    def app_aliases(self) -> dict[str, str]:
        return self.get("app_aliases", {})

    @property
    def credentials(self) -> dict[str, str]:
        return self.get("credentials", {})

    @property
    def files(self) -> dict[str, str]:
        defaults = {
            "reminders": "reminders.json",
            "memory": "memory.json",
        }
        defaults.update(self.get("files", {}))
        return defaults

    @property
    def scheduler(self) -> dict[str, Any]:
        defaults = {
            "poll_seconds": 15,
            "idle_suggestion_seconds": 300,
        }
        defaults.update(self.get("scheduler", {}))
        return defaults

    def resolve_app_name(self, raw_name: str) -> str:
        cleaned = (raw_name or "").strip().lower()
        if not cleaned:
            return cleaned
        return self.app_aliases.get(cleaned, cleaned)

    def get_app_config(self, raw_name: str) -> AppConfig | None:
        app_name = self.resolve_app_name(raw_name)
        path = self.app_paths.get(app_name)
        process_name = self.app_processes.get(app_name)
        if not path and not process_name:
            return None
        return AppConfig(name=app_name, path=path or "", process_name=process_name or "")


config = Config()
