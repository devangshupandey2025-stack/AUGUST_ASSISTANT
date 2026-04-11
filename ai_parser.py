from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from config import config
from intent_parser import CommandPlan, ParsedCommand, SUPPORTED_ACTIONS
from utils.logger import get_logger, log_event

logger = get_logger("AIParser")


class AIParserError(Exception):
    pass


@dataclass
class AIParser:
    endpoint_template: str = config.gemini["api_url"]
    model: str = config.gemini["model"]
    timeout_seconds: int = int(config.gemini["timeout_seconds"])
    max_retries: int = int(config.gemini["max_retries"])
    api_key: str = config.gemini["api_key"]

    def parse(self, user_input: str, context: dict[str, Any] | None = None) -> CommandPlan | None:
        if not self.api_key:
            log_event(logger, "ai_parse_skipped", source="ai", reason="missing_api_key", success=False)
            return self._safe_fallback(user_input)

        request_payload = self._build_request_payload(user_input, context or {})
        request_error = self._validate_request_payload(request_payload)
        if request_error:
            log_event(logger, "ai_request_invalid", source="ai", success=False, reason=request_error)
            return self._safe_fallback(user_input)

        last_error: str | None = None
        invalid_retry_available = True

        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            try:
                response = requests.post(
                    self.endpoint_template.format(model=self.model),
                    json=request_payload,
                    timeout=self.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key,
                    },
                )
                response.raise_for_status()
                result = response.json()
                raw_text = self._extract_candidate_text(result)
                data = self._parse_json_text(raw_text)
                plan = self._validate_payload(data, user_input)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "ai_parse_success",
                    source="ai",
                    success=True,
                    attempt=attempt,
                    execution_time_ms=elapsed_ms,
                    steps=len(plan.commands),
                )
                return plan
            except requests.Timeout:
                last_error = "timeout"
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                last_error = f"http_{status}"
                if exc.response is not None and exc.response.status_code == 400:
                    body = exc.response.text[:500]
                    log_event(
                        logger,
                        "ai_http_error",
                        source="ai",
                        success=False,
                        attempt=attempt,
                        status_code=400,
                        body=body,
                    )
            except (json.JSONDecodeError, AIParserError) as exc:
                last_error = str(exc)
                if invalid_retry_available:
                    invalid_retry_available = False
                    log_event(
                        logger,
                        "ai_validation_retry",
                        source="ai",
                        success=False,
                        attempt=attempt,
                        reason=last_error,
                    )
                    continue
            except requests.RequestException as exc:
                last_error = str(exc)

            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                logger,
                "ai_parse_failure",
                source="ai",
                success=False,
                attempt=attempt,
                execution_time_ms=elapsed_ms,
                reason=last_error,
            )
            if attempt <= self.max_retries:
                time.sleep(self._backoff_seconds(attempt))

        log_event(logger, "ai_parse_fallback", source="fallback", success=False, reason=last_error or "unknown")
        return self._safe_fallback(user_input)

    def _build_request_payload(self, user_input: str, context: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(user_input, context)
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        }

    def _validate_request_payload(self, payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return "payload_not_object"
        if "contents" not in payload or not isinstance(payload["contents"], list) or not payload["contents"]:
            return "missing_contents"
        first = payload["contents"][0]
        if not isinstance(first, dict) or "parts" not in first:
            return "missing_parts"
        parts = first["parts"]
        if not isinstance(parts, list) or not parts or "text" not in parts[0]:
            return "missing_text"
        if not str(parts[0]["text"]).strip():
            return "empty_prompt"
        return None

    def _build_prompt(self, user_input: str, context: dict[str, Any]) -> str:
        return f"""
You are a command planner for a Windows 11 voice assistant.
Return exactly one JSON array and nothing else.
Never return markdown.
Never return explanations.
Each array item must be one action object.

Supported action objects:
- {{"action":"open_app","app":"chrome"}}
- {{"action":"close_app","app":"chrome"}}
- {{"action":"search_web","query":"python tutorials","site":"google|youtube|gmail|github|reddit"}}
- {{"action":"calendar_today"}}
- {{"action":"create_reminder","task":"drink water","time_text":"in 10 minutes"}}
- {{"action":"shutdown"}}
- {{"action":"restart"}}
- {{"action":"volume_control","level":"up|down|mute|unmute"}}

Rules:
1. Always return a JSON array, even for one action.
2. Use lowercase string values.
3. If the user asks for multiple steps, split them into sequential actions.
4. Use search_web for browsing, watching, websites, songs, or search requests.
5. Use site youtube for songs, videos, or play requests.
6. Never return open_app for google, youtube, gmail, github, or reddit.
7. If the request is ambiguous, convert it into one safe search_web action.

Current context:
{json.dumps(context, ensure_ascii=True)}

User input:
{user_input}
""".strip()

    def _extract_candidate_text(self, response_payload: dict[str, Any]) -> str:
        candidates = response_payload.get("candidates") or []
        if not candidates:
            raise AIParserError("no_candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise AIParserError("no_content_parts")
        text = parts[0].get("text", "").strip()
        if not text:
            raise AIParserError("empty_text")
        return text

    def _parse_json_text(self, raw_text: str) -> Any:
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise AIParserError(f"invalid_json:{exc.msg}") from exc

    def _validate_payload(self, payload: Any, raw_text: str) -> CommandPlan:
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list) or not payload:
            raise AIParserError("payload_not_nonempty_array")

        commands: list[ParsedCommand] = []
        for item in payload:
            if not isinstance(item, dict):
                raise AIParserError("action_not_object")
            commands.append(self._validate_command(item))

        return CommandPlan(commands=commands, raw_text=raw_text, source="ai")

    def _validate_command(self, payload: dict[str, Any]) -> ParsedCommand:
        action = payload.get("action")
        if action not in SUPPORTED_ACTIONS:
            raise AIParserError(f"unsupported_action:{action}")

        if action in {"open_app", "close_app"}:
            app = str(payload.get("app", "")).strip().lower()
            if not app:
                raise AIParserError("missing_app")
            if app in {"google", "youtube", "gmail", "github", "reddit"}:
                raise AIParserError("unsafe_open_app_site")
            return ParsedCommand(action=action, payload={"app": config.resolve_app_name(app)}, source="ai", priority=70)

        if action == "search_web":
            query = str(payload.get("query", "")).strip().lower()
            site = str(payload.get("site", "google")).strip().lower() or "google"
            if site not in {"google", "youtube", "gmail", "github", "reddit"}:
                site = "google"
            if not query and site == "google":
                raise AIParserError("missing_query")
            return ParsedCommand(action=action, payload={"query": query, "site": site}, source="ai", priority=85)

        if action == "volume_control":
            level = str(payload.get("level", "")).strip().lower()
            if level not in {"up", "down", "mute", "unmute"}:
                raise AIParserError(f"invalid_volume:{level}")
            return ParsedCommand(action=action, payload={"level": level}, source="ai")

        if action == "calendar_today":
            return ParsedCommand(action=action, source="ai", priority=95)

        if action == "create_reminder":
            task = str(payload.get("task", "")).strip().lower()
            time_text = str(payload.get("time_text", "")).strip().lower()
            if not task or not time_text:
                raise AIParserError("invalid_reminder")
            return ParsedCommand(action=action, payload={"task": task, "time_text": time_text}, source="ai", priority=80)

        if action in {"shutdown", "restart"}:
            return ParsedCommand(action=action, source="ai", priority=100, requires_confirmation=True)

        raise AIParserError(f"unsupported_terminal_action:{action}")

    def _safe_fallback(self, user_input: str) -> CommandPlan:
        cleaned = " ".join((user_input or "").strip().lower().split())
        if cleaned.startswith("youtube for "):
            return CommandPlan(
                commands=[ParsedCommand(action="search_web", payload={"site": "youtube", "query": cleaned[12:]}, source="fallback", priority=90)],
                raw_text=user_input,
                source="fallback",
            )
        if cleaned.startswith("google for "):
            return CommandPlan(
                commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": cleaned[11:]}, source="fallback", priority=85)],
                raw_text=user_input,
                source="fallback",
            )
        return CommandPlan(
            commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": cleaned}, source="fallback", priority=80)],
            raw_text=user_input,
            source="fallback",
        )

    def _backoff_seconds(self, attempt: int) -> float:
        return min(6.0, 0.75 * (2 ** (attempt - 1)))
