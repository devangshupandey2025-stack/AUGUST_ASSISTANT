from __future__ import annotations

import datetime
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

from config import config
from context_engine import ContextEngine, IST
from memory import MemoryStore
from utils.logger import get_logger

logger = get_logger("Scheduler")


class AssistantScheduler:
    def __init__(
        self,
        memory_store: MemoryStore,
        context_engine: ContextEngine,
        speak_callback: Callable[[str], None],
    ) -> None:
        self.memory_store = memory_store
        self.context_engine = context_engine
        self.speak = speak_callback
        self.reminders_path = Path(config.files.get("reminders", "reminders.json"))
        self.poll_seconds = int(config.scheduler["poll_seconds"])
        self.idle_suggestion_seconds = int(config.scheduler["idle_suggestion_seconds"])
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_suggestion_at: datetime.datetime | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="assistant-scheduler", daemon=True)
        self._thread.start()
        logger.info("Assistant scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def add_reminder_from_command(self, payload: dict[str, str]) -> str:
        task = str(payload.get("task", "")).strip()
        time_text = str(payload.get("time_text", "")).strip()
        if not task or not time_text:
            return "I need both a reminder task and a time."

        reminder_time = self._parse_time_text(time_text)
        if reminder_time is None:
            return "I could not understand that reminder time. Try saying in 10 minutes."

        reminders = self._load_reminders()
        reminders.append(
            {
                "task": task,
                "time": reminder_time.isoformat(),
                "triggered": False,
                "created_at": datetime.datetime.now(IST).isoformat(),
            }
        )
        self._save_reminders(reminders)
        spoken_time = reminder_time.strftime("%I:%M %p")
        logger.info("Reminder added for task '%s' at %s", task, spoken_time)
        return f"Reminder set for {task} at {spoken_time}."

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._trigger_due_reminders()
                self._maybe_suggest_frequent_app()
            except Exception as exc:
                logger.exception("Scheduler loop error: %s", exc)
            self._stop_event.wait(self.poll_seconds)

    def _trigger_due_reminders(self) -> None:
        reminders = self._load_reminders()
        if not reminders:
            return

        now = datetime.datetime.now(IST)
        changed = False
        for reminder in reminders:
            if reminder.get("triggered"):
                continue
            due_at = self._parse_iso(reminder.get("time", ""))
            if due_at and now >= due_at:
                task = reminder.get("task", "your task")
                logger.info("Triggering reminder for '%s'", task)
                self.speak(f"Reminder: {task}.")
                reminder["triggered"] = True
                changed = True

        if changed:
            self._save_reminders(reminders)

    def _maybe_suggest_frequent_app(self) -> None:
        if not self.context_engine.is_idle_for(self.idle_suggestion_seconds):
            return

        now = datetime.datetime.now(IST)
        if self._last_suggestion_at and (now - self._last_suggestion_at).total_seconds() < self.idle_suggestion_seconds:
            return

        apps = self.memory_store.suggest_frequent_apps(self.context_engine.get_time_of_day(), limit=1)
        if not apps:
            return

        self._last_suggestion_at = now
        app = apps[0]
        logger.info("Offering proactive suggestion for app '%s'", app)
        self.speak(f"You often use {app} around this time. Say open {app} if you want me to launch it.")

    def _load_reminders(self) -> list[dict[str, object]]:
        if not self.reminders_path.exists():
            self._save_reminders([])
            return []
        try:
            return json.loads(self.reminders_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load reminders from %s: %s", self.reminders_path, exc)
            return []

    def _save_reminders(self, reminders: list[dict[str, object]]) -> None:
        self.reminders_path.write_text(json.dumps(reminders, indent=2), encoding="utf-8")

    def _parse_time_text(self, text: str) -> datetime.datetime | None:
        cleaned = text.strip().lower()
        relative_match = re.search(r"in\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)", cleaned)
        if not relative_match:
            return None

        value = int(relative_match.group(1))
        unit = relative_match.group(2)
        delta = datetime.timedelta(seconds=value)
        if "minute" in unit:
            delta = datetime.timedelta(minutes=value)
        elif "hour" in unit:
            delta = datetime.timedelta(hours=value)

        return datetime.datetime.now(IST) + delta

    def _parse_iso(self, value: str) -> datetime.datetime | None:
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
