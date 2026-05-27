from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from config import config
from conversation_memory import get_faq_answer, get_last_topic, get_preferred_style, normalize_text
from intent_parser import CommandPlan, ParsedCommand, SUPPORTED_ACTIONS
from utils.logger import get_logger, log_event

logger = get_logger("AIParser")


class AIParserError(Exception):
    pass


@dataclass
class AnswerResult:
    text: str
    source: str = "ai"
    should_offer_search: bool = False
    fallback_query: str = ""
    topic: str = ""
    error: str = ""


@dataclass
class AIParser:
    ANSWER_RETRY_LIMIT = 2

    endpoint_template: str = config.gemini["api_url"]
    model: str = config.gemini["model"]
    timeout_seconds: int = int(config.gemini["timeout_seconds"])
    max_retries: int = int(config.gemini["max_retries"])
    api_key: str = config.gemini["api_key"]

    def parse(self, user_input: str, context: dict[str, Any] | None = None) -> CommandPlan | None:
        normalized = normalize_text(user_input)
        if self._looks_like_question(normalized) or self._is_follow_up(normalized):
            log_event(logger, "ai_parse_skipped", source="ai", reason="answer_query", success=True)
            return None
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

        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
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
                    backoff_seconds = self._backoff_seconds(attempt)
                    log_event(logger, "ai_retry", source="ai", success=False, attempt=attempt, reason=last_error, backoff_seconds=backoff_seconds)
                    time.sleep(backoff_seconds)
                    continue
            except requests.RequestException as exc:
                last_error = str(exc)

            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if attempt <= self.max_retries:
                backoff_seconds = self._backoff_seconds(attempt)
                log_event(
                    logger,
                    "ai_retry",
                    source="ai",
                    success=False,
                    attempt=attempt,
                    execution_time_ms=elapsed_ms,
                    reason=last_error,
                    backoff_seconds=backoff_seconds,
                )
                time.sleep(backoff_seconds)
                continue
            log_event(
                logger,
                "ai_final_failure",
                source="ai",
                success=False,
                attempt=attempt,
                execution_time_ms=elapsed_ms,
                reason=last_error or "unknown",
                backoff_seconds=0.0,
            )
            break

        return self._safe_fallback(user_input)

    def answer(self, user_input: str, context: dict[str, Any] | None = None, memory: dict[str, Any] | None = None) -> str:
        return self.answer_with_fallback(user_input, context=context, memory=memory).text

    def try_ai_answer(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, str | bool]:
        context = context or {}
        memory = memory or {}
        if not self.api_key:
            return {"success": False, "text": "", "error": "missing_api_key"}

        prompt = self._build_answer_prompt(user_input, context, memory)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
            },
        }
        payload_error = self._validate_request_payload(payload, request_kind="ai_answer")
        if payload_error:
            return {"success": False, "text": "", "error": "invalid_payload"}

        last_error = "unknown"
        total_attempts = self.ANSWER_RETRY_LIMIT + 1
        for attempt in range(1, total_attempts + 1):
            try:
                response = requests.post(
                    self.endpoint_template.format(model=self.model),
                    json=payload,
                    timeout=self.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key,
                    },
                )

                response.raise_for_status()
                result = response.json()
                text = self._extract_candidate_text(result)
                answer = " ".join(text.split())
                if not answer:
                    return {"success": False, "text": "", "error": "invalid_payload"}
                return {"success": True, "text": answer, "error": ""}
            except requests.Timeout:
                last_error = "timeout"
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                last_error = f"http_{status}"
            except (json.JSONDecodeError, AIParserError, ValueError, TypeError):
                return {"success": False, "text": "", "error": "invalid_payload"}
            except requests.RequestException as exc:
                last_error = str(exc) or "request_error"
            except Exception as exc:
                last_error = str(exc) or "unknown"

            if attempt <= self.ANSWER_RETRY_LIMIT and self._is_retryable_answer_error(last_error):
                backoff_seconds = self._answer_backoff_seconds(attempt)
                log_event(
                    logger,
                    "ai_retry",
                    source="ai",
                    success=False,
                    attempt=attempt,
                    reason=last_error,
                    backoff_seconds=backoff_seconds,
                )
                time.sleep(backoff_seconds)
                continue
            break

        log_event(logger, "ai_final_failure", source="ai", success=False, attempt=total_attempts, reason=last_error, backoff_seconds=0.0)
        return {"success": False, "text": "", "error": last_error}

    def summarize_web_content(
        self,
        text: str,
        query: str = "",
        source_url: str = "",
        title: str = "",
    ) -> dict[str, str | bool]:
        clean_text = " ".join((text or "").split())
        if not clean_text:
            return {"success": False, "text": "", "error": "empty_text"}
        if not self.api_key:
            return {"success": False, "text": "", "error": "missing_api_key"}

        excerpt = clean_text[:9000]
        prompt = (
            "Summarize the extracted web article content in 3-5 concise sentences. "
            "Keep factual accuracy, remove redundancy, and keep the language simple for voice output. "
            "Do not include markdown or bullet points.\n\n"
            f"Query: {query or 'N/A'}\n"
            f"Title: {title or 'N/A'}\n"
            f"Source URL: {source_url or 'N/A'}\n"
            f"Extracted content:\n{excerpt}"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1},
        }
        payload_error = self._validate_request_payload(payload, request_kind="web_summary")
        if payload_error:
            return {"success": False, "text": "", "error": "invalid_payload"}

        last_error = "unknown"
        total_attempts = self.ANSWER_RETRY_LIMIT + 1
        for attempt in range(1, total_attempts + 1):
            try:
                response = requests.post(
                    self.endpoint_template.format(model=self.model),
                    json=payload,
                    timeout=self.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key,
                    },
                )
                response.raise_for_status()
                result = response.json()
                summary = " ".join(self._extract_candidate_text(result).split())
                if not summary:
                    return {"success": False, "text": "", "error": "invalid_payload"}
                return {"success": True, "text": summary, "error": ""}
            except requests.Timeout:
                last_error = "timeout"
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                last_error = f"http_{status}"
            except (json.JSONDecodeError, AIParserError, ValueError, TypeError):
                return {"success": False, "text": "", "error": "invalid_payload"}
            except requests.RequestException as exc:
                last_error = str(exc) or "request_error"
            except Exception as exc:
                last_error = str(exc) or "unknown"

            if attempt <= self.ANSWER_RETRY_LIMIT and self._is_retryable_answer_error(last_error):
                time.sleep(self._answer_backoff_seconds(attempt))
                continue
            break
        return {"success": False, "text": "", "error": last_error}

    def answer_with_fallback(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
    ) -> AnswerResult:
        context = context or {}
        memory = memory or {}
        normalized = normalize_text(user_input)
        remembered = get_faq_answer(memory, normalized)
        if remembered:
            return AnswerResult(
                text=remembered,
                source="memory",
                topic=self._extract_topic(user_input, context, memory),
            )

        if not self.api_key:
            log_event(logger, "ai_answer_skipped", source="ai", reason="missing_api_key", success=False)
            return self._offline_answer_result(user_input, context, memory, error="missing_api_key")

        prompt = self._build_answer_prompt(user_input, context, memory)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
            },
        }
        payload_error = self._validate_request_payload(payload, request_kind="answer_with_fallback")
        if payload_error:
            return self._offline_answer_result(user_input, context, memory, error="invalid_payload")

        last_error: str | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            started = time.perf_counter()
            try:
                response = requests.post(
                    self.endpoint_template.format(model=self.model),
                    json=payload,
                    timeout=self.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.api_key,
                    },
                )
                response.raise_for_status()
                result = response.json()
                text = self._extract_candidate_text(result)
                answer = " ".join(text.split())
                if not answer:
                    raise AIParserError("empty_answer")
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "ai_answer_success",
                    source="ai",
                    success=True,
                    attempt=attempt,
                    execution_time_ms=elapsed_ms,
                )
                return AnswerResult(
                    text=answer,
                    source="ai",
                    topic=self._extract_topic(user_input, context, memory),
                )
            except requests.Timeout:
                last_error = "timeout"
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                last_error = f"http_{status}"
            except (requests.RequestException, AIParserError) as exc:
                last_error = str(exc)

            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if attempt <= self.max_retries:
                backoff_seconds = self._backoff_seconds(attempt)
                log_event(
                    logger,
                    "ai_retry",
                    source="ai",
                    success=False,
                    attempt=attempt,
                    execution_time_ms=elapsed_ms,
                    reason=last_error,
                    backoff_seconds=backoff_seconds,
                )
                time.sleep(backoff_seconds)
                continue
            log_event(
                logger,
                "ai_final_failure",
                source="ai",
                success=False,
                attempt=attempt,
                execution_time_ms=elapsed_ms,
                reason=last_error or "unknown",
                backoff_seconds=0.0,
            )
            break

        return self._offline_answer_result(user_input, context, memory, error=last_error or "unknown")

    def _build_request_payload(self, user_input: str, context: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(user_input, context)
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        }

    def _build_answer_prompt(self, user_input: str, context: dict[str, Any], memory: dict[str, Any]) -> str:
        recent_commands = memory.get("command_history", [])[-3:]
        preferred_style = get_preferred_style(memory)
        last_topic = str(context.get("last_topic") or get_last_topic(memory) or "").strip()
        return f"""
You are the spoken answer layer for a Windows voice assistant named Jarvis.
Answer the user's informational question directly.
Keep the answer concise, accurate, and easy to read aloud.
Do not return JSON.
Do not describe internal tools.
If the question is ambiguous, make the best reasonable interpretation and say so briefly.
Respect the user's preferred response style: {preferred_style}.
If the message is a follow-up such as "give example" or "tell me more", continue the previous topic when it is provided.

Current context:
{json.dumps(context, ensure_ascii=True)}

Recent memory:
{json.dumps(recent_commands, ensure_ascii=True)}

Previous topic:
{last_topic or "none"}

User question:
{user_input}
""".strip()

    def _validate_request_payload(self, payload: dict[str, Any], request_kind: str = "generic") -> str | None:
        if not isinstance(payload, dict):
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="payload_not_object")
            return "payload_not_object"
        if "contents" not in payload or not isinstance(payload["contents"], list) or not payload["contents"]:
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="missing_contents")
            return "missing_contents"
        first = payload["contents"][0]
        if not isinstance(first, dict) or "parts" not in first:
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="missing_parts")
            return "missing_parts"
        role = str(first.get("role", "")).strip().lower()
        if role not in {"user", "model"}:
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="missing_role")
            return "missing_role"
        parts = first["parts"]
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict) or "text" not in parts[0]:
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="missing_text")
            return "missing_text"
        if not str(parts[0]["text"]).strip():
            log_event(logger, "payload_validation_failed", source="ai", success=False, request_kind=request_kind, reason="empty_prompt")
            return "empty_prompt"
        log_event(logger, "payload_validation_success", source="ai", success=True, request_kind=request_kind)
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
7. If the request is ambiguous, return an empty JSON array instead of guessing.
8. If the request is informational, conversational, toxic, or casual chat, return an empty JSON array.

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
        if self._looks_like_question(cleaned) or self._looks_conversational(cleaned) or self._looks_toxic(cleaned):
            return None
        if self._looks_unclear(cleaned):
            return None
        if cleaned.startswith("search ") or cleaned.startswith("google ") or cleaned.startswith("find ") or cleaned.startswith("look up "):
            query = cleaned.split(" ", 1)[1].strip() if " " in cleaned else ""
            if query:
                return CommandPlan(
                    commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": query}, source="fallback", priority=85)],
                    raw_text=user_input,
                    source="fallback",
                )
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
        if cleaned.startswith(("watch ", "play ")):
            query = cleaned.split(" ", 1)[1].strip() if " " in cleaned else ""
            if query:
                return CommandPlan(
                    commands=[ParsedCommand(action="search_web", payload={"site": "youtube", "query": query}, source="fallback", priority=90)],
                    raw_text=user_input,
                    source="fallback",
                )
        if re.match(r"^open\s+(youtube|google|gmail|github|reddit)\b(?:\s+for\s+(.+))?$", cleaned):
            match = re.match(r"^open\s+(youtube|google|gmail|github|reddit)\b(?:\s+for\s+(.+))?$", cleaned)
            if match:
                payload = {"site": match.group(1)}
                if match.group(2):
                    payload["query"] = match.group(2).strip()
                return CommandPlan(
                    commands=[ParsedCommand(action="search_web", payload=payload, source="fallback", priority=90)],
                    raw_text=user_input,
                    source="fallback",
                )
        return None

    def _backoff_seconds(self, attempt: int) -> float:
        return min(6.0, 0.75 * (2 ** (attempt - 1)))

    def _answer_backoff_seconds(self, attempt: int) -> float:
        return min(1.0, 0.5 * (2 ** (attempt - 1)))

    def _is_retryable_answer_error(self, error: str) -> bool:
        normalized = str(error or "").strip().lower()
        return normalized in {"timeout", "http_429", "http_503", "request_error"} or "connection" in normalized

    def _offline_answer_result(
        self,
        user_input: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        error: str,
    ) -> AnswerResult:
        normalized = normalize_text(user_input)
        topic = self._extract_topic(user_input, context, memory)
        if normalized.startswith("do you think"):
            return AnswerResult(
                text="I think it depends on the situation. If you want, I can look it up and give you a clearer answer.",
                source="fallback",
                should_offer_search=True,
                fallback_query=topic or normalized,
                topic=topic,
                error=error,
            )
        if normalized.startswith("which is better"):
            return AnswerResult(
                text="That usually depends on what matters most to you. Want me to search the latest comparisons?",
                source="fallback",
                should_offer_search=True,
                fallback_query=topic or normalized,
                topic=topic,
                error=error,
            )
        if self._is_follow_up(normalized) and topic:
            return AnswerResult(
                text=f"I can keep going on {topic}, but I'm having trouble fetching the full answer right now. Want me to search it?",
                source="fallback",
                should_offer_search=True,
                fallback_query=topic,
                topic=topic,
                error=error,
            )
        return AnswerResult(
            text="I'm having trouble with that. Want me to search it?",
            source="fallback",
            should_offer_search=True,
            fallback_query=topic or normalized,
            topic=topic,
            error=error,
        )

    def _extract_topic(self, user_input: str, context: dict[str, Any], memory: dict[str, Any]) -> str:
        normalized = normalize_text(user_input)
        if self._is_follow_up(normalized):
            last_topic = str(context.get("last_topic") or get_last_topic(memory) or "").strip()
            return last_topic or normalized
        stripped = re.sub(
            r"^(what is|what's|who is|why|how|tell me about|can you explain|explain|is it true that|do you think|which is better)\s+",
            "",
            normalized,
        )
        return stripped or normalized

    def _is_follow_up(self, normalized: str) -> bool:
        return normalized in {
            "give example",
            "an example",
            "example",
            "tell me more",
            "go on",
            "continue",
            "why is that",
            "how so",
            "explain more",
        } or normalized.startswith(("give me an example", "can you give an example"))

    def _looks_like_question(self, normalized: str) -> bool:
        if "?" in normalized:
            return True
        return normalized.startswith(
            (
                "what ",
                "what's ",
                "who ",
                "why ",
                "how ",
                "which ",
                "tell me about",
                "can you explain",
                "is it true that",
                "do you think",
                "which ai is best",
            )
        )

    def _looks_conversational(self, normalized: str) -> bool:
        return normalized in {
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "how are you",
        }

    def _looks_unclear(self, normalized: str) -> bool:
        tokens = normalized.split()
        return len(tokens) <= 2 and normalized not in {"open chrome", "open spotify", "close chrome", "close spotify"}

    def _looks_toxic(self, normalized: str) -> bool:
        return any(term in normalized for term in ("fuck you", "screw you", "idiot", "stupid"))
