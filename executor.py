from __future__ import annotations

import os
import subprocess
import time
import webbrowser
from ctypes import POINTER, cast
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus

from AppOpener import open as open_app_fallback
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

from intent_parser import CommandPlan, ParsedCommand
from modules.calendar_module import fetch_todays_events
from system_intents import formatted_date, formatted_time
from utils.logger import get_logger, log_event

logger = get_logger("Executor")


@dataclass
class ExecutionResult:
    success: bool
    message: str
    executed_count: int = 0


class Executor:
    def __init__(self, reminder_handler: Callable[[dict[str, str]], str] | None = None) -> None:
        self.reminder_handler = reminder_handler
        self._handlers: dict[str, Callable[[ParsedCommand], ExecutionResult]] = {
            "open_app": self._open_app,
            "close_app": self._close_app,
            "search_web": self._search_web,
            "shutdown": self._shutdown,
            "restart": self._restart,
            "volume_control": self._volume_control,
            "calendar_today": self._calendar_today,
            "create_reminder": self._create_reminder,
            "current_time": self._current_time,
            "current_date": self._current_date,
            "greeting": self._greeting,
        }

    def execute_plan(self, plan: CommandPlan) -> ExecutionResult:
        logger.info("Executing command plan with %s step(s) from source '%s'", len(plan.commands), plan.source)
        messages: list[str] = []
        executed_count = 0

        for command in plan.commands:
            result = self.execute(command)
            if result.message:
                messages.append(result.message)
            if not result.success:
                return ExecutionResult(False, " ".join(messages).strip(), executed_count=executed_count)
            executed_count += 1

        return ExecutionResult(True, " ".join(messages).strip(), executed_count=executed_count)

    def execute(self, command: ParsedCommand) -> ExecutionResult:
        started = time.perf_counter()
        logger.info("Executing action '%s' from source '%s'", command.action, command.source)
        handler = self._handlers.get(command.action)
        if not handler:
            logger.error("No executor registered for action '%s'", command.action)
            return ExecutionResult(False, "I do not know how to do that yet.")

        try:
            result = handler(command)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                logger,
                "execution_result",
                source=command.source,
                action=command.action,
                success=result.success,
                execution_time_ms=elapsed_ms,
            )
            return result
        except Exception as exc:
            logger.exception("Execution failed for action '%s': %s", command.action, exc)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                logger,
                "execution_result",
                source=command.source,
                action=command.action,
                success=False,
                execution_time_ms=elapsed_ms,
                error=str(exc),
            )
            return ExecutionResult(False, "I ran into an error while handling that command.")

    def _open_app(self, command: ParsedCommand) -> ExecutionResult:
        app_name = command.payload["app"]
        rerouted = self._reroute_app_like_web_command(app_name)
        if rerouted:
            logger.info("Safety layer rerouted open_app '%s' to search_web", app_name)
            return self._search_web(rerouted)

        from config import config

        app_config = config.get_app_config(app_name)
        if app_config and app_config.path:
            logger.info("Opening configured app '%s' from %s", app_name, app_config.path)
            os.startfile(app_config.path)
            return ExecutionResult(True, f"Opening {app_name}.")

        try:
            logger.info("Opening app '%s' with AppOpener fallback", app_name)
            open_app_fallback(app_name, match_closest=True, throw_error=True)
            return ExecutionResult(True, f"Opening {app_name}.")
        except Exception as exc:
            logger.error("Failed to open app '%s': %s", app_name, exc)
            return ExecutionResult(False, f"I could not open {app_name}.")

    def _close_app(self, command: ParsedCommand) -> ExecutionResult:
        app_name = command.payload["app"]
        from config import config

        app_config = config.get_app_config(app_name)
        process_name = (app_config.process_name if app_config else "") or self._guess_process_name(app_name)

        logger.info("Closing app '%s' using process '%s'", app_name, process_name)
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return ExecutionResult(True, f"Closing {app_name}.")

        logger.warning("taskkill failed for '%s': %s", process_name, result.stderr.strip())
        return ExecutionResult(False, f"I could not close {app_name}.")

    def _search_web(self, command: ParsedCommand) -> ExecutionResult:
        site = command.payload.get("site", "google")
        query = command.payload.get("query", "").strip()
        url = self._build_web_url(site, query)
        logger.info("Opening site '%s' for query '%s'", site, query)
        webbrowser.open(url)
        if site == "youtube" and query:
            return ExecutionResult(True, f"Opening YouTube results for {query}.")
        if site != "google" and not query:
            return ExecutionResult(True, f"Opening {site}.")
        if site != "google" and query:
            return ExecutionResult(True, f"Opening {site} for {query}.")
        return ExecutionResult(True, f"Searching the web for {query}.")

    def _calendar_today(self, command: ParsedCommand) -> ExecutionResult:
        del command
        schedule = fetch_todays_events()
        return ExecutionResult(True, schedule)

    def _create_reminder(self, command: ParsedCommand) -> ExecutionResult:
        if not self.reminder_handler:
            return ExecutionResult(False, "Reminder scheduling is not available right now.")
        message = self.reminder_handler(command.payload)
        success_prefixes = ("reminder set",)
        return ExecutionResult(message.lower().startswith(success_prefixes), message)

    def _current_time(self, command: ParsedCommand) -> ExecutionResult:
        del command
        return ExecutionResult(True, f"The time right now is {formatted_time()}.")

    def _current_date(self, command: ParsedCommand) -> ExecutionResult:
        del command
        return ExecutionResult(True, f"Today is {formatted_date()}.")

    def _greeting(self, command: ParsedCommand) -> ExecutionResult:
        del command
        return ExecutionResult(True, "Hello. How can I help you?")

    def _shutdown(self, command: ParsedCommand) -> ExecutionResult:
        del command
        logger.info("Issuing shutdown command")
        subprocess.Popen(["shutdown", "/s", "/t", "1"])
        return ExecutionResult(True, "Shutting down the system.")

    def _restart(self, command: ParsedCommand) -> ExecutionResult:
        del command
        logger.info("Issuing restart command")
        subprocess.Popen(["shutdown", "/r", "/t", "1"])
        return ExecutionResult(True, "Restarting the system.")

    def _volume_control(self, command: ParsedCommand) -> ExecutionResult:
        level = command.payload["level"]
        volume = self._get_volume_interface()

        if level == "mute":
            volume.SetMute(1, None)
            return ExecutionResult(True, "Muting the volume.")

        if level == "unmute":
            volume.SetMute(0, None)
            return ExecutionResult(True, "Restoring the volume.")

        current = volume.GetMasterVolumeLevelScalar()
        step = 0.1
        if level == "up":
            volume.SetMasterVolumeLevelScalar(min(1.0, current + step), None)
            return ExecutionResult(True, "Increasing the volume.")

        if level == "down":
            volume.SetMasterVolumeLevelScalar(max(0.0, current - step), None)
            return ExecutionResult(True, "Decreasing the volume.")

        return ExecutionResult(False, "I could not adjust the volume.")

    def _get_volume_interface(self):
        speakers = AudioUtilities.GetSpeakers()
        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _guess_process_name(self, app_name: str) -> str:
        if app_name.endswith(".exe"):
            return app_name
        return f"{app_name}.exe"

    def _build_web_url(self, site: str, query: str) -> str:
        normalized_site = site.lower().strip()
        if normalized_site == "youtube":
            if query:
                return f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            return "https://www.youtube.com"

        site_homepages = {
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "reddit": "https://www.reddit.com",
            "google": "https://www.google.com",
        }
        if normalized_site in site_homepages and not query:
            return site_homepages[normalized_site]

        if query:
            return f"https://www.google.com/search?q={quote_plus(query)}"
        return site_homepages.get(normalized_site, "https://www.google.com")

    def _reroute_app_like_web_command(self, app_name: str) -> ParsedCommand | None:
        cleaned = (app_name or "").strip().lower()
        if cleaned.startswith("google for "):
            query = cleaned[len("google for ") :].strip()
            return ParsedCommand(action="search_web", payload={"site": "google", "query": query}, source="safety")
        if cleaned.startswith("youtube for "):
            query = cleaned[len("youtube for ") :].strip()
            return ParsedCommand(action="search_web", payload={"site": "youtube", "query": query}, source="safety")
        if cleaned in {"google", "youtube", "gmail", "github", "reddit"}:
            return ParsedCommand(action="search_web", payload={"site": cleaned, "query": ""}, source="safety")
        return None
