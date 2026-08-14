from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from august.answer_fallback import classify_query, try_local_answer
from august.answer_memory import AnswerMemory
from august.config import config
from august.conversation_memory import get_last_topic, remember_answer
from august.followup_utils import is_follow_up_query
from august.garbage_detector import detect_garbage_input
from august.intent_parser import SUPPORTED_ACTIONS, CommandPlan, ParsedCommand
from august.personality_engine import personality_engine
from august.sanity_validator import validate_query_sanity
from august.utils.logger import get_logger, log_event

logger = get_logger("DecisionEngine")

QUERY_TYPES = {
    "command": "command",
    "static_knowledge": "static_knowledge",
    "dynamic_fact": "dynamic_fact",
    "current_event": "current_event",
    "reasoning": "reasoning",
    "conversation": "conversation",
}


@dataclass
class DecisionResult:
    mode: str
    source: str
    plan: CommandPlan | None = None
    response: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class PendingClarification:
    kind: str
    prompt: str
    original_text: str
    options: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0


class DecisionEngine:
    CLARIFICATION_TIMEOUT_SECONDS = 20
    SESSION_CACHE_TTL = 120
    HIGH_CONFIDENCE_THRESHOLD = 0.7
    MEDIUM_CONFIDENCE_THRESHOLD = 0.4
    SEARCH_PROMPT = "I'm having trouble getting a reliable answer. Do you want me to search it?"
    GARBAGE_INPUT_RESPONSE = "That doesn't look like a valid command. Can you rephrase?"
    INVALID_CONTEXT_INPUTS = {"yes", "no", "search", "search it", "it", "answer"}
    BLOCKED_LAST_QUERY_INPUTS = {"yes", "no", "search", "search it", "it"}
    CASUAL_LAST_QUERY_INPUTS = {"how are you", "how r u", "what's up", "whats up"}
    ANSWER_PATTERNS = (
        r"^what is\b",
        r"^what's\b",
        r"^what are\b",
        r"^who is\b",
        r"^who are\b",
        r"^when is\b",
        r"^where is\b",
        r"^why\b",
        r"^how\b",
        r"^do you think\b",
        r"^which is better\b",
        r"^which\b.*\bbest\b",
        r"^tell me about\b",
        r"^can you explain\b",
        r"^is it true that\b",
        r"^explain\b",
        r"^difference between\b",
        r"^what(?:'s| is) the difference between\b",
        r"^compare\b",
        r"^define\b",
        r"^latest\b",
        r"^news\b",
        r"^what(?:'s| is) happening\b",
    )
    FOLLOW_UP_PATTERNS = (
        r"^give example\b",
        r"^give (?:me )?an? example\b",
        r"^example\b",
        r"^tell me more\b",
        r"^go on\b",
        r"^continue\b",
        r"^why is that\b",
        r"^how so\b",
        r"^explain more\b",
        r"^what do you mean\b",
    )
    CASUAL_RESPONSES = {
        "hi": "Hi. What can I help you with?",
        "hello": "Hello. What can I do for you?",
        "hey": "Hey. What do you need?",
        "thanks": "Anytime.",
        "thank you": "Anytime.",
        "how are you": "I'm doing good. What about you?",
        "good morning": "Good morning.",
        "good afternoon": "Good afternoon.",
        "good evening": "Good evening.",
    }
    CONVERSATIONAL_QUERY_PATTERNS = (
        "how are you",
        "how r u",
        "what's up",
        "whats up",
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank you",
        "good morning",
        "good afternoon",
        "good evening",
        "nice",
    )
    TOXIC_PATTERNS = (
        r"\bfuck you\b",
        r"\bscrew you\b",
        r"\byou suck\b",
        r"\bidiot\b",
        r"\bstupid\b",
    )
    YES_WORDS = {"yes", "yeah", "yep", "sure", "okay", "ok", "please do", "do it", "go ahead"}
    NO_WORDS = {"no", "nope", "cancel", "stop", "don't", "do not", "not now"}
    WEB_SITES = {"google", "youtube", "gmail", "github", "reddit"}
    FOLLOW_UP_RESPONSE_HINTS = ("yes", "no", "answer", "search", "tell me", "explain", "look it up")
    WEB_RESEARCH_QUERY_TYPES = {"dynamic_fact", "current_event", "reasoning"}

    def __init__(self, ai_parser: Any, memory_store: Any, app_registry: Any | None = None) -> None:
        self.ai_parser = ai_parser
        self.memory_store = memory_store
        self.app_registry = app_registry
        self.answer_memory = AnswerMemory(memory_store=memory_store)
        self._pending_clarification: PendingClarification | None = None
        self._last_topic = ""
        self._last_query = ""
        self._session_cache: dict[str, dict[str, Any]] = {}

    def decide(
        self,
        raw_text: str,
        parsed_plan: CommandPlan | None,
        context: dict[str, Any],
        memory: dict[str, Any] | None = None,
    ) -> DecisionResult:
        cleaned_text = self._clean_repetition(raw_text)
        memory_snapshot = memory or self.memory_store.snapshot()
        normalized = self._normalize(cleaned_text)
        notes: list[str] = []
        self._sync_pending_clarification(context)
        self._clear_expired_pending_clarification()

        if self._pending_clarification:
            resolved = self.resolve_interaction(cleaned_text, normalized, context, memory_snapshot, notes)
            if resolved is not None:
                return self._finalize(resolved, cleaned_text)

        context_last_query = self._normalize(str(context.get("last_knowledge_query") or context.get("last_query", "") or ""))
        effective_last_query = self._latest_valid_last_query(context_last_query)
        document_plan = self._parse_document_generation_intent(normalized)
        if document_plan is not None:
            return self._finalize(
                DecisionResult(mode="action", source="decision.document_generation", plan=document_plan, notes=notes + ["generate_document"]),
                cleaned_text,
            )

        if normalized == "yes":
            if self._is_fallback_reply_context(context) and effective_last_query:
                plan = CommandPlan(
                    commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": effective_last_query}, source="decision", priority=82)],
                    raw_text=cleaned_text,
                    source="decision",
                )
                return self._finalize(
                    DecisionResult(mode="action", source="decision.search_from_yes", plan=plan, notes=notes + ["yes_to_fallback_search"]),
                    cleaned_text,
                )
            return self._finalize_answer_with_confidence(
                raw_text=cleaned_text,
                response="I'm not confident I understood that. Can you clarify?",
                source="decision.ignored_yes",
                notes=notes + ["ignored_yes"],
                confidence=0.2,
            )
        if normalized in {"search", "search it"} and effective_last_query:
            plan = CommandPlan(
                commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": effective_last_query}, source="decision", priority=82)],
                raw_text=cleaned_text,
                source="decision",
            )
            return self._finalize(
                DecisionResult(mode="action", source="decision.search_from_context", plan=plan, notes=notes + ["search_last_query"]),
                cleaned_text,
            )

        if self._should_reuse_last_query(normalized, effective_last_query):
            notes.append("reused_last_query")
            return self._finalize(
                self._run_answer_pipeline(
                    raw_text=cleaned_text,
                    answer_query=effective_last_query,
                    context=dict(context),
                    memory=memory_snapshot,
                    notes=notes,
                ),
                cleaned_text,
            )

        if self._is_toxic(normalized):
            return self._finalize_answer_with_confidence(
                raw_text=cleaned_text,
                response="Alright, noted.",
                source="decision.toxic",
                notes=["toxic_handled"],
                confidence=0.95,
            )

        garbage_message = self._garbage_response(normalized)
        if garbage_message:
            return self._finalize_answer_with_confidence(
                raw_text=cleaned_text,
                response=garbage_message,
                source="decision.garbage",
                notes=["garbage_input"],
                confidence=0.95,
            )

        casual_response = self._casual_response(normalized)
        if casual_response:
            return self._finalize_answer_with_confidence(
                raw_text=cleaned_text,
                response=casual_response,
                source="decision.casual",
                notes=["casual_input"],
                confidence=0.95,
            )

        topic = self._topic_from_context(memory_snapshot)
        answer_context = dict(context)
        if topic:
            answer_context["last_topic"] = topic

        if self._is_answer_query(normalized, parsed_plan):
            return self._finalize(
                self._run_answer_pipeline(
                    raw_text=cleaned_text,
                    answer_query=cleaned_text,
                    context=answer_context,
                    memory=memory_snapshot,
                    notes=notes,
                ),
                cleaned_text,
            )

        if self._is_follow_up(normalized):
            resolved_query = effective_last_query or str(context.get("last_query", "") or "").strip()
            if not resolved_query:
                return self._finalize_answer_with_confidence(
                    raw_text=cleaned_text,
                    response="What would you like me to explain?",
                    source="decision.followup_missing_context",
                    notes=notes + ["followup_without_context"],
                    confidence=0.5,
                )
            log_event(logger, "followup_resolved", source="decision", success=True, original=normalized, resolved=resolved_query)
            refined_query = self._refine_followup_query(cleaned_text, resolved_query)
            notes.append("followup_resolved")
            return self._finalize(
                self._run_answer_pipeline(
                    raw_text=cleaned_text,
                    answer_query=refined_query,
                    context=answer_context,
                    memory=memory_snapshot,
                    notes=notes or ["informational_query"],
                ),
                cleaned_text,
            )

        candidate_plan = parsed_plan or self._infer_from_context(normalized, context, memory_snapshot, notes)
        if candidate_plan is None:
            return self._finalize(self._classify_without_plan(cleaned_text, normalized, answer_context, memory_snapshot, notes), cleaned_text)

        corrected_plan = self._correct_plan(candidate_plan, normalized, context, memory_snapshot, notes)
        ambiguity = self._detect_plan_ambiguity(corrected_plan, normalized, notes)
        if ambiguity is not None:
            return self._finalize(ambiguity, cleaned_text)

        confidence, confidence_notes = self._score_confidence(normalized, corrected_plan)
        notes.extend(confidence_notes)
        confidence_level = self._log_decision_confidence(confidence, corrected_plan)
        if confidence_level == "low":
            return self._finalize(
                DecisionResult(
                    mode="answer",
                    source="decision.low_confidence",
                    response="I'm not confident I understood that. Can you clarify?",
                    notes=notes + [f"confidence:{confidence:.2f}", "confidence_low"],
                ),
                cleaned_text,
            )
        if confidence_level == "medium":
            clarification = self._clarification_for_medium_confidence(corrected_plan)
            self._set_pending_clarification(
                kind="action_confirmation",
                prompt=clarification,
                original_text=cleaned_text,
                payload={"plan": corrected_plan},
            )
            log_event(
                logger,
                "clarification_requested",
                source="decision",
                success=True,
                reason="medium_confidence",
                action=corrected_plan.commands[0].action if corrected_plan.commands else "",
            )
            return self._finalize(
                DecisionResult(
                    mode="answer",
                    source="decision.medium_confidence",
                    response=clarification,
                    notes=notes + [f"confidence:{confidence:.2f}", "confidence_medium"],
                ),
                cleaned_text,
            )

        validation_error = self._validate_plan(corrected_plan)
        if validation_error:
            logger.warning("Decision plan invalid for '%s': %s", cleaned_text, validation_error)
            response = f"I need a bit more detail before I can act: {validation_error.replace('_', ' ')}."
            return self._finalize(
                DecisionResult(mode="answer", source="decision.validation", response=response, notes=notes + [validation_error]),
                cleaned_text,
            )

        return self._finalize(
            DecisionResult(mode="action", source="decision.action", plan=corrected_plan, notes=notes + [f"confidence:{confidence:.2f}"]),
            cleaned_text,
        )

    def _finalize(self, result: DecisionResult, raw_text: str) -> DecisionResult:
        log_event(
            logger,
            "decision_result",
            source=result.source,
            success=result.mode in {"action", "answer"},
            mode=result.mode,
            raw_text=raw_text,
            notes=result.notes,
            steps=len(result.plan.commands) if result.plan else 0,
        )
        return result

    def _finalize_answer_with_confidence(
        self,
        raw_text: str,
        response: str,
        source: str,
        notes: list[str],
        confidence: float,
    ) -> DecisionResult:
        confidence_level = self._log_decision_confidence(confidence, None)
        final_response = response
        final_source = source
        final_notes = list(notes) + [f"confidence:{confidence:.2f}", f"confidence_{confidence_level}"]
        if confidence_level == "low":
            final_response = "I'm not confident I understood that. Can you clarify?"
            final_source = "decision.low_confidence"
        return self._finalize(
            DecisionResult(mode="answer", source=final_source, response=final_response, notes=final_notes),
            raw_text,
        )

    def _log_decision_confidence(self, confidence: float, plan: CommandPlan | None) -> str:
        confidence_level = self._confidence_level(confidence)
        first_action = plan.commands[0].action if plan and plan.commands else ""
        log_event(
            logger,
            "decision_confidence_level",
            source="decision",
            success=confidence_level == "high",
            confidence=round(confidence, 2),
            level=confidence_level,
            action=first_action,
        )
        return confidence_level

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def _clean_repetition(self, text: str) -> str:
        words = self._normalize(text).split()
        if not words:
            return ""
        cleaned: list[str] = [words[0]]
        for idx in range(1, len(words)):
            if words[idx] != words[idx - 1]:
                cleaned.append(words[idx])
        return " ".join(cleaned)

    def _is_invalid_context_input(self, text: str) -> bool:
        return self._normalize(text) in self.INVALID_CONTEXT_INPUTS

    def _update_last_query(self, query: str) -> None:
        normalized = self._normalize(query)
        if not normalized:
            return
        if not self._should_update_last_query(normalized) and not self._is_forced_question_query(normalized):
            return
        self._last_query = normalized
        logger.info("last_query updated -> %s", self._last_query)

    def _latest_valid_last_query(self, context_last_query: str) -> str:
        normalized_context = self._normalize(context_last_query)
        if normalized_context and (self._should_update_last_query(normalized_context) or self._is_forced_question_query(normalized_context)):
            return normalized_context

        normalized_internal = self._normalize(self._last_query)
        if normalized_internal and (self._should_update_last_query(normalized_internal) or self._is_forced_question_query(normalized_internal)):
            return normalized_internal
        return ""

    def _should_update_last_query(self, query: str) -> bool:
        normalized = self._normalize(query)
        if not normalized:
            return False
        if is_follow_up_query(normalized):
            return False
        if normalized in self.BLOCKED_LAST_QUERY_INPUTS:
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
        if any(re.search(pattern, normalized) for pattern in self.ANSWER_PATTERNS):
            return True
        if any(marker in normalized for marker in ("difference between", "compare ", "vs ", " versus ")):
            return True
        if any(marker in normalized for marker in ("algorithm", "complexity", "binary search", "dynamic programming", "sorting")):
            return True
        return False

    def resolve_interaction(
        self,
        raw_text: str,
        normalized: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        notes: list[str],
    ) -> DecisionResult | None:
        pending = self._pending_clarification
        if pending is None:
            return None

        if normalized in self.NO_WORDS:
            if pending.kind in {"search_offer", "answer_vs_search", "action_confirmation"}:
                log_event(logger, "pending_interaction_resolved", source="decision", success=True, interaction_type=pending.kind, choice="cancel")
            self._pending_clarification = None
            return DecisionResult(mode="answer", source="decision.clarification_cancelled", response="Alright, I won't do anything with that.", notes=["clarification_cancelled"])

        if pending.kind in {"search_offer", "answer_vs_search"}:
            original_query = str(pending.payload.get("original_query") or pending.original_text).strip() or pending.original_text
            if "search" in normalized or "look it up" in normalized:
                query = str(pending.payload.get("query", "")).strip() or self._normalize(original_query)
                query = self._resolve_context_query(query, context)
                plan = CommandPlan(
                    commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": query}, source="decision", priority=80)],
                    raw_text=original_query,
                    source="decision",
                )
                log_event(logger, "pending_interaction_resolved", source="decision", success=True, interaction_type=pending.kind, choice="search")
                self._pending_clarification = None
                notes.append("resolved_to_search")
                return DecisionResult(mode="action", source="decision.answer_vs_search", plan=plan, notes=notes)
            if normalized in self.YES_WORDS or "answer" in normalized or "tell me" in normalized or "explain" in normalized:
                log_event(logger, "pending_interaction_resolved", source="decision", success=True, interaction_type=pending.kind, choice="answer")
                self._pending_clarification = None
                return self._run_answer_pipeline(
                    raw_text=original_query,
                    answer_query=original_query,
                    context=context,
                    memory=memory,
                    notes=notes + ["resolved_to_answer"],
                )
            if self._looks_like_command(normalized):
                self._pending_clarification = None
                notes.append("interaction_cleared_unrelated")
                return None
            self._pending_clarification = None
            return self._run_answer_pipeline(
                raw_text=original_query,
                answer_query=original_query,
                context=context,
                memory=memory,
                notes=notes + ["resolved_to_answer"],
            )

        if pending.kind == "action_confirmation":
            stored_plan = pending.payload.get("plan")
            if normalized in self.YES_WORDS and isinstance(stored_plan, CommandPlan):
                log_event(logger, "pending_interaction_resolved", source="decision", success=True, interaction_type=pending.kind, choice="confirm")
                self._pending_clarification = None
                notes.append("confirmed_medium_confidence_action")
                return DecisionResult(mode="action", source="decision.action_confirmation", plan=stored_plan, notes=notes)
            if normalized in self.NO_WORDS:
                self._pending_clarification = None
                return DecisionResult(
                    mode="answer",
                    source="decision.action_confirmation_cancelled",
                    response="Alright, I won't do anything with that.",
                    notes=["clarification_cancelled"],
                )
            return DecisionResult(mode="answer", source="decision.clarification_pending", response=pending.prompt, notes=["awaiting_clarification"])

        if self._looks_like_command(normalized) and normalized not in self.YES_WORDS and normalized not in self.NO_WORDS:
            self._pending_clarification = None
            notes.append("interaction_cleared_unrelated")
            return None

        if pending.kind in {"app_choice", "app_disambiguation", "app_clarification"}:
            chosen = self._match_clarification_option(normalized, pending.options)
            if not chosen and normalized in self.YES_WORDS and pending.options:
                chosen = pending.options[0]
            if chosen:
                resolved_path = self._lookup_app_path(chosen)
                payload = {"app": chosen}
                if resolved_path:
                    payload["path"] = resolved_path
                plan = CommandPlan(
                    commands=[ParsedCommand(action="open_app", payload=payload, source="decision", priority=78)],
                    raw_text=pending.original_text,
                    source="decision",
                )
                self._pending_clarification = None
                notes.append("clarification_resolved")
                return DecisionResult(mode="action", source="decision.clarification", plan=plan, notes=notes)
            options = " or ".join(pending.options[:2])
            return DecisionResult(mode="answer", source="decision.clarification_pending", response=f"Did you mean {options}?", notes=["awaiting_clarification"])

        self._pending_clarification = None
        return None

    def _sync_pending_clarification(self, context: dict[str, Any]) -> None:
        if self._pending_clarification is not None:
            return
        raw_pending = context.get("pending_interaction")
        if not isinstance(raw_pending, dict):
            raw_pending = context.get("pending_clarification")
        if not isinstance(raw_pending, dict):
            return
        pending_type = str(raw_pending.get("type", "")).strip().lower()
        original_query = str(raw_pending.get("original_query", "")).strip()
        options = [str(item).strip().lower() for item in raw_pending.get("options", []) if str(item).strip()]
        timestamp = raw_pending.get("timestamp")
        timestamp_value = float(timestamp) if isinstance(timestamp, (int, float)) else time.time()
        if not pending_type or not original_query:
            return
        if pending_type == "search_offer":
            pending_type = "answer_vs_search"
        if pending_type == "app_disambiguation":
            pending_type = "app_clarification"
        prompt = self.SEARCH_PROMPT if pending_type in {"answer_vs_search", "search_offer"} else ""
        if pending_type in {"app_disambiguation", "app_clarification"} and len(options) >= 2:
            prompt = f"Did you mean {options[0]} or {options[1]}?"
        self._pending_clarification = PendingClarification(
            kind=pending_type,
            prompt=prompt,
            original_text=original_query,
            options=options[:2],
            payload={"original_query": original_query},
            timestamp=timestamp_value,
        )

    def _set_pending_clarification(
        self,
        kind: str,
        prompt: str,
        original_text: str,
        options: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        normalized_kind = (kind or "").strip().lower()
        if normalized_kind == "search_offer":
            normalized_kind = "answer_vs_search"
        if normalized_kind == "app_disambiguation":
            normalized_kind = "app_clarification"
        self._pending_clarification = PendingClarification(
            kind=normalized_kind,
            prompt=prompt,
            original_text=original_text,
            options=list(options or [])[:2],
            payload=dict(payload or {}),
            timestamp=time.time(),
        )

    def _clear_expired_pending_clarification(self) -> None:
        pending = self._pending_clarification
        if pending is None or not pending.timestamp:
            return
        if time.time() - pending.timestamp > self.CLARIFICATION_TIMEOUT_SECONDS:
            self._pending_clarification = None

    def _is_followup_response(self, normalized: str) -> bool:
        return any(word in normalized for word in self.FOLLOW_UP_RESPONSE_HINTS)

    def _should_reuse_last_query(self, normalized: str, last_query: str) -> bool:
        if not last_query:
            return False
        if normalized == "answer":
            return True
        return False

    def _is_answer_query(self, normalized: str, parsed_plan: CommandPlan | None) -> bool:
        if self._is_follow_up(normalized):
            return False
        if self._looks_like_command(normalized):
            return False
        if parsed_plan:
            actions = {command.action for command in parsed_plan.commands}
            if actions & {"current_time", "current_date", "calendar_today", "greeting", "shutdown", "restart"}:
                return False
            if actions == {"search_web"} and not self._looks_like_search_request(normalized):
                return any(re.search(pattern, normalized) for pattern in self.ANSWER_PATTERNS)
        query_type = self._classify_query_type(normalized, parsed_plan=None)
        return any(re.search(pattern, normalized) for pattern in self.ANSWER_PATTERNS) or query_type in {
            QUERY_TYPES["static_knowledge"],
            QUERY_TYPES["dynamic_fact"],
            QUERY_TYPES["current_event"],
            QUERY_TYPES["reasoning"],
        }

    def _is_follow_up(self, normalized: str) -> bool:
        return is_follow_up_query(normalized) or any(re.search(pattern, normalized) for pattern in self.FOLLOW_UP_PATTERNS)

    def _is_toxic(self, normalized: str) -> bool:
        return any(re.search(pattern, normalized) for pattern in self.TOXIC_PATTERNS)

    def _casual_response(self, normalized: str) -> str:
        personality_reply = personality_engine.casual_response(normalized)
        if personality_reply:
            return personality_reply
        return self.CASUAL_RESPONSES.get(normalized, "")

    def _garbage_response(self, normalized: str) -> str:
        if not normalized:
            log_event(logger, "garbage_detected", source="decision", success=True, reason="empty_input")
            return self.GARBAGE_INPUT_RESPONSE
        if self._is_follow_up(normalized):
            return ""
        has_known_intent = any(
            (
                self._looks_like_command(normalized),
                self._looks_like_question(normalized),
                normalized in self.CASUAL_RESPONSES,
                normalized in self.YES_WORDS,
                normalized in self.NO_WORDS,
                normalized in {"open", "close", "search", "play", "watch", "run", "start", "launch", "mute", "unmute", "shutdown", "restart"},
            )
        )
        verdict = detect_garbage_input(normalized, has_known_intent=has_known_intent)
        if verdict.get("is_garbage"):
            log_event(logger, "garbage_detected", source="decision", success=True, reason=verdict.get("reason", "unknown"), query=normalized)
            return self.GARBAGE_INPUT_RESPONSE
        return ""

    def _classify_without_plan(
        self,
        raw_text: str,
        normalized: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        notes: list[str],
    ) -> DecisionResult:
        self._log_decision_confidence(0.2, None)
        if self._looks_like_command(normalized):
            return DecisionResult(
                mode="answer",
                source="decision.low_confidence",
                response="I'm not confident I understood that. Can you clarify?",
                notes=notes + ["clarify_command", "confidence_low"],
            )
        if self._looks_like_question(normalized) or self._is_follow_up(normalized):
            return self._run_answer_pipeline(
                raw_text=raw_text,
                answer_query=raw_text,
                context=context,
                memory=memory,
                notes=notes + ["fallback_answer_mode"],
            )
        return DecisionResult(
            mode="answer",
            source="decision.low_confidence",
            response="I'm not confident I understood that. Can you clarify?",
            notes=notes + ["unclear_input", "confidence_low"],
        )

    def _infer_from_context(
        self,
        normalized: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        notes: list[str],
    ) -> CommandPlan | None:
        if not normalized:
            return None

        if normalized in {"close", "close it", "close that", "exit", "quit"}:
            app_name = self._resolve_app_from_context(context, memory)
            if app_name:
                notes.append("resolved_close_from_context")
                return CommandPlan(
                    commands=[ParsedCommand(action="close_app", payload={"app": app_name}, source="decision", priority=75)],
                    raw_text=normalized,
                    source="decision",
                )

        if normalized in {"open", "open it", "open that", "open it again", "launch it"}:
            app_name = self._resolve_app_from_context(context, memory) or self.memory_store.suggest_next_app(context.get("time_of_day", "afternoon"))
            if app_name:
                notes.append("resolved_open_from_context")
                return CommandPlan(
                    commands=[ParsedCommand(action="open_app", payload={"app": app_name}, source="decision", priority=70)],
                    raw_text=normalized,
                    source="decision",
                )

        if normalized.startswith("close "):
            target = normalized.removeprefix("close ").strip()
            if target in {"it", "that", "this"}:
                app_name = self._resolve_app_from_context(context, memory)
                if app_name:
                    notes.append("resolved_pronoun_close")
                    return CommandPlan(
                        commands=[ParsedCommand(action="close_app", payload={"app": app_name}, source="decision", priority=75)],
                        raw_text=normalized,
                        source="decision",
                    )

        if normalized.startswith(("open ", "launch ", "start ", "run ")):
            entity = normalized.split(" ", 1)[1].strip() if " " in normalized else ""
            if entity:
                if self._should_reroute_app_to_search(entity):
                    notes.append("rerouted_for_search")
                    return self._build_search_plan(normalized, entity)
                if entity in {"it", "that", "this"}:
                    app_name = self._resolve_app_from_context(context, memory)
                    if app_name:
                        notes.append("resolved_pronoun_open")
                        return CommandPlan(
                            commands=[ParsedCommand(action="open_app", payload={"app": app_name}, source="decision", priority=70)],
                            raw_text=normalized,
                            source="decision",
                        )
                notes.append("direct_open_inference")
                return CommandPlan(
                    commands=[ParsedCommand(action="open_app", payload={"app": config.resolve_app_name(entity)}, source="decision", priority=70)],
                    raw_text=normalized,
                    source="decision",
                )

        return None

    def _correct_plan(
        self,
        plan: CommandPlan,
        normalized: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        notes: list[str],
    ) -> CommandPlan:
        corrected_commands: list[ParsedCommand] = []
        for command in plan.commands:
            corrected = ParsedCommand(
                action=command.action,
                payload=dict(command.payload),
                source=command.source,
                priority=command.priority,
                requires_confirmation=command.requires_confirmation,
            )

            if corrected.action == "open_app":
                app_name = self._clean_open_app_value(str(corrected.payload.get("app", "")).strip().lower())
                corrected.payload["app"] = app_name
                if self._should_reroute_app_to_search(app_name):
                    notes.append("open_app_to_search")
                    corrected_commands.append(self._search_command_from_entity(app_name, corrected.source))
                    continue
                if not app_name and normalized.startswith(("open", "launch", "start", "run")):
                    inferred = self._resolve_app_from_context(context, memory) or self.memory_store.suggest_next_app(context.get("time_of_day", "afternoon"))
                    if inferred:
                        corrected.payload["app"] = self._clean_open_app_value(inferred)
                        notes.append("filled_missing_open_app")
                elif app_name in self.WEB_SITES:
                    notes.append("open_site_to_search")
                    corrected_commands.append(self._search_command_from_entity(app_name, corrected.source))
                    continue
                resolved_match = self._lookup_app_match(corrected.payload.get("app", ""))
                if resolved_match:
                    corrected.payload["path"] = str(resolved_match.get("path", "")).strip()
                    corrected.payload["_registry_match_type"] = str(resolved_match.get("match_type", "")).strip()
                    corrected.payload["_registry_match_confidence"] = float(resolved_match.get("confidence", 0.0) or 0.0)
                    notes.append("registry_match_open_app")

            if corrected.action == "close_app":
                app_name = str(corrected.payload.get("app", "")).strip().lower()
                if app_name in {"", "it", "that", "this"}:
                    inferred = self._resolve_app_from_context(context, memory)
                    if inferred:
                        corrected.payload["app"] = inferred
                        notes.append("filled_missing_close_app")

            if corrected.action == "search_web":
                if self._is_answer_query(normalized, None) or self._is_follow_up(normalized):
                    notes.append("search_plan_reclassified")
                    continue
                if self._looks_like_app_open_request(normalized):
                    app_query = self._extract_open_query(normalized) or str(corrected.payload.get("query", "")).strip().lower()
                    app_query = self._clean_open_app_value(app_query)
                    resolved_match = self._lookup_app_match(app_query)
                    if resolved_match:
                        corrected_commands.append(
                            ParsedCommand(
                                action="open_app",
                                payload={
                                    "app": app_query,
                                    "path": str(resolved_match.get("path", "")).strip(),
                                    "_registry_match_type": str(resolved_match.get("match_type", "")).strip(),
                                    "_registry_match_confidence": float(resolved_match.get("confidence", 0.0) or 0.0),
                                },
                                source=corrected.source,
                                priority=max(corrected.priority, 75),
                                requires_confirmation=corrected.requires_confirmation,
                            )
                        )
                        notes.append("corrected_search_to_open_app")
                        continue
                site = str(corrected.payload.get("site", "google")).strip().lower() or "google"
                query = str(corrected.payload.get("query", "")).strip().lower()
                corrected.payload["site"] = site if site in self.WEB_SITES else "google"
                resolved_context_query = self._resolve_context_query(query, context)
                if resolved_context_query != query:
                    corrected.payload["query"] = resolved_context_query
                    query = resolved_context_query
                if not query and corrected.payload["site"] == "google":
                    corrected.payload["query"] = normalized
                    notes.append("filled_search_query")

            corrected_commands.append(corrected)

        if not corrected_commands and (self._is_answer_query(normalized, None) or self._is_follow_up(normalized)):
            return CommandPlan(commands=[], raw_text=plan.raw_text, source="decision.answer_reclassified")
        return CommandPlan(commands=corrected_commands, raw_text=plan.raw_text, source=plan.source)

    def _detect_plan_ambiguity(self, plan: CommandPlan, normalized: str, notes: list[str]) -> DecisionResult | None:
        if not plan.commands:
            memory_snapshot = self.memory_store.snapshot()
            return self._run_answer_pipeline(
                raw_text=normalized,
                answer_query=normalized,
                context={"last_topic": self._last_topic or get_last_topic(memory_snapshot)},
                memory=memory_snapshot,
                notes=notes + ["reclassified_to_answer"],
            )

        first = plan.commands[0]
        if first.action != "open_app":
            return None

        app_name = str(first.payload.get("app", "")).strip().lower()
        if not app_name:
            return None

        ambiguity_query = self._extract_raw_open_target(normalized) or app_name
        cleaned_target = self._clean_open_app_value(ambiguity_query)
        if self._has_trailing_noise_word(ambiguity_query) and cleaned_target:
            prompt = f"Did you mean {cleaned_target}?"
            self._set_pending_clarification(
                kind="app_clarification",
                prompt=prompt,
                original_text=normalized,
                options=[cleaned_target],
                payload={"original_query": normalized},
            )
            return DecisionResult(mode="answer", source="decision.clarification", response=prompt, notes=notes + ["trailing_noise_suggestion"])
        matches = self._find_ambiguous_app_matches(ambiguity_query)
        if len(matches) == 1 and self._clean_open_app_value(ambiguity_query) != matches[0]:
            option = matches[0]
            prompt = f"Did you mean {option}?"
            self._set_pending_clarification(
                kind="app_clarification",
                prompt=prompt,
                original_text=normalized,
                options=[option],
                payload={"original_query": normalized},
            )
            return DecisionResult(mode="answer", source="decision.clarification", response=prompt, notes=notes + ["single_app_suggestion"])
        if len(matches) < 2:
            return None
        if first.payload.get("path") and len(app_name) > 4:
            return None

        options = matches[:2]
        prompt = f"Did you mean {options[0]} or {options[1]}?"
        self._set_pending_clarification(
            kind="app_clarification",
            prompt=prompt,
            original_text=normalized,
            options=options,
            payload={"original_query": normalized},
        )
        return DecisionResult(mode="answer", source="decision.clarification", response=prompt, notes=notes + ["ambiguous_app"])

    def _has_trailing_noise_word(self, value: str) -> bool:
        tokens = [token for token in re.split(r"\s+", (value or "").strip().lower()) if token]
        if len(tokens) < 2:
            return False
        return tokens[-1] in {"hello", "please", "bro"}

    def _score_confidence(self, normalized: str, plan: CommandPlan) -> tuple[float, list[str]]:
        score = 0.45
        notes: list[str] = []

        if plan.source in {"system", "rule"}:
            score += 0.25
            notes.append("rule_based_plan")
        elif plan.source == "ai":
            score += 0.15
            notes.append("ai_plan")
        elif plan.source == "fallback":
            score -= 0.1
            notes.append("fallback_plan")

        if len(plan.commands) > 1:
            score += 0.1
        first = plan.commands[0] if plan.commands else None
        if not first:
            return 0.0, notes + ["empty_plan"]

        if first.action in {"open_app", "close_app"}:
            app = str(first.payload.get("app", "")).strip().lower()
            if (first.action == "open_app" and normalized.startswith(("open ", "launch ", "start ", "run "))) or (
                first.action == "close_app" and normalized.startswith(("close ", "exit ", "quit "))
            ):
                score += 0.15
                notes.append("explicit_app_command")
            if first.action == "close_app" and normalized in {"close", "close it", "close that", "exit", "quit"}:
                score += 0.2
                notes.append("resolved_close_context")
                app = str(first.payload.get("app", "")).strip().lower()
                if app:
                    score += 0.1
                    notes.append("resolved_close_target")
            if len(app.split()) == 1 and len(app) <= 4 and not first.payload.get("path"):
                score -= 0.2
                notes.append("short_app_name")
            if first.payload.get("path"):
                match_type = str(first.payload.get("_registry_match_type", "")).strip().lower()
                match_confidence = float(first.payload.get("_registry_match_confidence", 0.8) or 0.8)
                score += 0.2 * match_confidence
                notes.append("resolved_path")
                if match_type == "partial":
                    score -= 0.35
                    notes.append("partial_registry_match")
                elif match_type == "fuzzy":
                    score -= 0.1
                    notes.append("fuzzy_registry_match")
            elif first.action == "open_app" and self._looks_non_application_phrase(app):
                score -= 0.4
                notes.append("semantic_app_confidence_downgrade")
                log_event(logger, "semantic_app_confidence_downgrade", source="decision", success=True, app=app)
        if first.action == "search_web":
            if self._looks_like_search_request(normalized) or normalized.startswith(("open youtube", "open google", "open github", "open reddit", "open gmail")):
                score += 0.2
            else:
                score -= 0.25
                notes.append("implicit_search")

        return max(0.0, min(score, 0.99)), notes

    def _clarification_for_medium_confidence(self, plan: CommandPlan) -> str:
        if plan.commands:
            command = plan.commands[0]
            if command.action == "open_app" and not command.payload.get("path"):
                app_name = str(command.payload.get("app", "")).strip()
                if self._looks_non_application_phrase(app_name):
                    return "Did you want me to search that instead?"
        return f"Did you mean to {self._describe_action(plan)}?"

    def _describe_action(self, plan: CommandPlan) -> str:
        if not plan.commands:
            return "do that"
        command = plan.commands[0]
        if command.action == "open_app":
            app_name = str(command.payload.get("app", "")).strip()
            return f"open {app_name}" if app_name else "open that app"
        if command.action == "close_app":
            app_name = str(command.payload.get("app", "")).strip()
            return f"close {app_name}" if app_name else "close that app"
        if command.action == "search_web":
            site = str(command.payload.get("site", "google")).strip().lower() or "google"
            query = str(command.payload.get("query", "")).strip()
            if query:
                if site == "google":
                    return f"search for {query}"
                return f"search {site} for {query}"
            return f"open {site}"
        if command.action == "generate_document":
            topic = str(command.payload.get("topic", "")).strip()
            return f"create a document on {topic}" if topic else "create a document"
        return command.action.replace("_", " ")

    def _confidence_level(self, confidence: float) -> str:
        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            return "high"
        if confidence >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            return "medium"
        return "low"

    def _validate_plan(self, plan: CommandPlan | None) -> str | None:
        if not plan or not plan.commands:
            return "empty_plan"

        for command in plan.commands:
            if command.action not in SUPPORTED_ACTIONS:
                return f"unsupported_action_{command.action}"

            if command.action in {"open_app", "close_app"} and not str(command.payload.get("app", "")).strip():
                return f"missing_app_for_{command.action}"

            if command.action == "search_web":
                site = str(command.payload.get("site", "google")).strip().lower()
                query = str(command.payload.get("query", "")).strip()
                if site not in self.WEB_SITES:
                    return "invalid_search_site"
                if site == "google" and not query:
                    return "missing_search_query"

            if command.action == "volume_control":
                if str(command.payload.get("level", "")).strip().lower() not in {"up", "down", "mute", "unmute"}:
                    return "invalid_volume_level"

            if command.action == "create_reminder":
                if not str(command.payload.get("task", "")).strip() or not str(command.payload.get("time_text", "")).strip():
                    return "invalid_reminder_payload"

            if command.action == "generate_document":
                if not str(command.payload.get("topic", "")).strip():
                    return "missing_document_topic"

        return None

    def _parse_document_generation_intent(self, normalized: str) -> CommandPlan | None:
        patterns = (
            r"^(?:please\s+)?make\s+(?:some\s+)?notes\s+(?:on|about)\s+(.+)$",
            r"^(?:please\s+)?create\s+(?:a\s+)?document\s+(?:on|about)\s+(.+)$",
            r"^(?:please\s+)?write\s+(?:a\s+)?report\s+(?:on|about)\s+(.+)$",
            r"^(?:please\s+)?generate\s+(?:a\s+)?(?:document|report|notes)\s+(?:on|about)\s+(.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized)
            if not match:
                continue
            topic = re.sub(r"\s+", " ", match.group(1).strip(" .?"))
            if not topic:
                return None
            return CommandPlan(
                commands=[ParsedCommand(action="generate_document", payload={"topic": topic, "open_file": True}, source="decision", priority=88)],
                raw_text=normalized,
                source="decision",
            )
        return None

    def _resolve_app_from_context(self, context: dict[str, Any], memory: dict[str, Any]) -> str:
        app_name = str(context.get("last_app", "") or "").strip().lower()
        if app_name:
            return app_name

        app_name = self.memory_store.get_last_app()
        if app_name:
            return app_name

        usage = memory.get("habits", {}).get("time_of_day_usage", {}).get(context.get("time_of_day", "afternoon"), {})
        if usage:
            return max(usage, key=usage.get)

        return self.memory_store.suggest_next_app(context.get("time_of_day", "afternoon"))

    def _build_search_plan(self, raw_text: str, entity: str) -> CommandPlan:
        return CommandPlan(
            commands=[self._search_command_from_entity(entity, "decision")],
            raw_text=raw_text,
            source="decision",
        )

    def _search_command_from_entity(self, entity: str, source: str) -> ParsedCommand:
        cleaned = entity.strip().lower()
        if " for " in cleaned:
            site, query = cleaned.split(" for ", 1)
            if site in self.WEB_SITES:
                return ParsedCommand(action="search_web", payload={"site": site, "query": query.strip()}, source=source, priority=85)
        if cleaned in self.WEB_SITES:
            return ParsedCommand(action="search_web", payload={"site": cleaned, "query": ""}, source=source, priority=85)
        return ParsedCommand(action="search_web", payload={"site": "google", "query": cleaned}, source=source, priority=80)

    def _resolve_context_query(self, query: str, context: dict[str, Any]) -> str:
        normalized_query = self._normalize(query)
        if normalized_query not in {"it", "that", "this"}:
            return normalized_query
        context_query = self._normalize(str(context.get("last_knowledge_query") or context.get("last_query", "") or ""))
        if not context_query:
            return normalized_query
        log_event(logger, "context_query_resolved", source="decision", success=True, original=normalized_query, resolved=context_query)
        return context_query

    def _looks_non_application_phrase(self, value: str) -> bool:
        tokens = [token for token in re.split(r"\s+", (value or "").strip().lower()) if token]
        if len(tokens) < 2:
            return False
        known_app_terms = {"chrome", "spotify", "vscode", "code", "edge", "brave", "youtube", "gmail", "github", "notepad", "calculator"}
        if any(token in known_app_terms for token in tokens):
            return False
        uncommon_markers = {"quantum", "blockchain", "polymorphism", "mixtape", "concept", "theory", "history", "news"}
        return len(tokens) >= 3 or any(token in uncommon_markers for token in tokens)

    def _should_reroute_app_to_search(self, entity: str) -> bool:
        cleaned = entity.strip().lower()
        if not cleaned:
            return False
        if " for " in cleaned:
            site, _query = cleaned.split(" for ", 1)
            return site in self.WEB_SITES
        return False

    def _lookup_app_path(self, query: str) -> str | None:
        match = self._lookup_app_match(query)
        if not match:
            return None
        path = str(match.get("path", "")).strip()
        return path or None

    def _lookup_app_match(self, query: str) -> dict[str, Any] | None:
        normalized_query = self._clean_open_app_value(query)
        if not normalized_query or not self.app_registry:
            return None
        finder = getattr(self.app_registry, "find_app_match", None)
        match = finder(normalized_query) if callable(finder) else None
        if match:
            logger.info(
                "Decision engine matched app '%s' to path '%s' type='%s' confidence=%.2f",
                normalized_query,
                match.get("path", ""),
                match.get("match_type", "unknown"),
                float(match.get("confidence", 0.0) or 0.0),
            )
            return match
        path = self.app_registry.find_app(normalized_query)
        if path:
            logger.info("Decision engine matched app '%s' to path '%s'", normalized_query, path)
            return {"app": normalized_query, "path": path, "match_type": "external", "confidence": 0.8}
        logger.info("Decision engine found no registry path for '%s'", normalized_query)
        return None

    def _clean_open_app_value(self, value: str) -> str:
        cleaned = config.resolve_app_name((value or "").strip().lower())
        cleaned = re.sub(r"\b(for me|for us|please|now|hello|bro)\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _looks_like_app_open_request(self, normalized: str) -> bool:
        return normalized.startswith(("open ", "launch ", "start ", "run "))

    def _extract_open_query(self, normalized: str) -> str:
        parts = normalized.split(" ", 1)
        if len(parts) < 2:
            return ""
        return self._clean_open_app_value(parts[1])

    def _extract_raw_open_target(self, normalized: str) -> str:
        parts = normalized.split(" ", 1)
        if len(parts) < 2:
            return ""
        return re.sub(r"\s+", " ", parts[1].strip().lower())

    def _find_ambiguous_app_matches(self, query: str, limit: int = 2) -> list[str]:
        if not self.app_registry:
            return []
        registry = getattr(self.app_registry, "registry", None) or getattr(self.app_registry, "matches", {}) or {}
        cleaned = re.sub(r"[^a-z0-9\s]+", " ", (query or "").strip().lower())
        cleaned = re.sub(r"\b(for me|for us|please|now|app|application)\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return []

        scored: list[tuple[int, str]] = []
        query_tokens = set(cleaned.split())
        for app_name in registry.keys():
            normalized_name = self._clean_open_app_value(str(app_name))
            if len(normalized_name) < 3:
                continue
            name_tokens = set(normalized_name.split())
            score = 0
            if cleaned == normalized_name:
                score += 4
            if cleaned in normalized_name or normalized_name in cleaned:
                score += 3
            if query_tokens and query_tokens.issubset(name_tokens):
                score += 2
            if not score:
                continue
            confidence = min(score / 4.0, 1.0)
            if confidence < 0.6:
                continue
            scored.append((score, normalized_name))

        unique: list[str] = []
        for _score, name in sorted(scored, key=lambda item: (-item[0], len(item[1]))):
            if name not in unique:
                unique.append(name)
            if len(unique) >= limit:
                break
        return unique

    def _match_clarification_option(self, normalized: str, options: list[str]) -> str:
        if normalized in {"first one", "the first one", "first"} and options:
            return options[0]
        if normalized in {"second one", "the second one", "second"} and len(options) > 1:
            return options[1]
        for option in options:
            option_tokens = set(option.split())
            response_tokens = set(normalized.split())
            if option == normalized or option in normalized or response_tokens & option_tokens:
                return option
        return ""

    def _looks_like_question(self, normalized: str) -> bool:
        if "?" in normalized:
            return True
        return any(re.search(pattern, normalized) for pattern in self.ANSWER_PATTERNS) or self._is_follow_up(normalized)

    def _refine_followup_query(self, followup_text: str, resolved_query: str) -> str:
        normalized_followup = self._normalize(followup_text)
        clean_resolved = self._normalize(resolved_query)
        if normalized_followup.startswith(("give example", "example")):
            return f"give example for {clean_resolved}"
        if normalized_followup.startswith(("tell me more", "explain more", "more", "tell me", "explain")):
            return f"explain more about {clean_resolved}"
        return clean_resolved

    def _looks_like_search_request(self, normalized: str) -> bool:
        return normalized.startswith(("search ", "google ", "find ", "look up ", "youtube for ", "watch ", "play "))

    def _looks_like_command(self, normalized: str) -> bool:
        return normalized.startswith(
            (
                "open ",
                "close ",
                "launch ",
                "start ",
                "run ",
                "search ",
                "find ",
                "look up ",
                "play ",
                "watch ",
                "set ",
                "remind ",
                "mute",
                "unmute",
                "shutdown",
                "restart",
            )
        )

    def _topic_from_context(self, memory: dict[str, Any]) -> str:
        return self._last_topic or get_last_topic(memory)

    def _run_answer_pipeline(
        self,
        raw_text: str,
        answer_query: str,
        context: dict[str, Any],
        memory: dict[str, Any],
        notes: list[str],
    ) -> DecisionResult:
        normalized_query = self._normalize(answer_query)
        follow_up_request = self._is_follow_up(self._normalize(raw_text))
        query_type = self._classify_query_type(normalized_query, parsed_plan=None)
        is_dynamic = query_type in {QUERY_TYPES["dynamic_fact"], QUERY_TYPES["current_event"]}
        is_reasoning = query_type == QUERY_TYPES["reasoning"]
        is_static = query_type == QUERY_TYPES["static_knowledge"]
        sanity_result = validate_query_sanity(answer_query)
        if not sanity_result.is_valid:
            log_event(
                logger,
                "sanity_validation_failed",
                source="decision.answer",
                success=False,
                query=raw_text,
                reason=sanity_result.reason,
            )
            log_event(
                logger,
                "sanity_confidence_downgraded",
                source="decision.answer",
                success=True,
                query=raw_text,
                factor=sanity_result.confidence_factor,
            )
            self._update_last_query(answer_query)
            return DecisionResult(
                mode="answer",
                source="decision.sanity_guard",
                response=sanity_result.clarification or "That question looks inconsistent. Can you rephrase it?",
                notes=notes + ["sanity_validation_failed", "confidence:0.35", "confidence_low"],
            )
        if not follow_up_request and (self._should_update_last_query(normalized_query) or self._is_forced_question_query(normalized_query)):
            self._update_last_query(answer_query)
        cached_session_response = self._get_session_cache(answer_query)
        if cached_session_response:
            log_event(logger, "session_cache_hit", source="decision.session_cache", success=True, query=answer_query)
            return DecisionResult(mode="answer", source="decision.session_cache", response=cached_session_response, notes=notes + ["session_cache_hit"])

        if not follow_up_request and normalized_query not in {"yes", "no", "it"} and not is_dynamic:
            cached_answer = self.answer_memory.retrieve(answer_query, allow_dynamic=False)
            if cached_answer:
                topic = self._topic_from_context(memory) or self._normalize(answer_query)
                self._remember_answer(raw_text, cached_answer, topic)
                if not follow_up_request:
                    self._update_last_query(answer_query)
                log_event(logger, "memory_hit", source="decision.answer_memory", success=True, query=raw_text)
                self._store_session_cache(answer_query, cached_answer)
                return DecisionResult(mode="answer", source="decision.memory", response=cached_answer, notes=notes + ["memory_hit"])
            log_event(logger, "memory_miss", source="decision.answer_memory", success=False, query=raw_text)

        local_result: dict[str, Any] = {"success": False, "error": "local_skipped"}
        local_failed = False
        if is_static or is_reasoning or follow_up_request:
            local_result = try_local_answer(answer_query)
            if local_result.get("success"):
                text = str(local_result.get("text", "")).strip()
                local_confidence = float(local_result.get("confidence", 0.6) or 0.6)
                local_source = str(local_result.get("source", "local_match") or "local_match").strip() or "local_match"
                if self._is_weak_local_answer(answer_query, text, local_confidence):
                    log_event(logger, "local_confidence_too_low", source="decision.answer", success=False, query=raw_text, confidence=local_confidence)
                    local_failed = True
                else:
                    topic = self._topic_from_context(memory) or self._normalize(answer_query)
                    self._remember_answer(raw_text, text, topic)
                    if not follow_up_request:
                        self._update_last_query(answer_query)
                    if local_source != "local_generated" and self.answer_memory.store(answer_query, text, confidence=local_confidence, source="verified_local"):
                        log_event(
                            logger,
                            "memory_store",
                            source="decision.answer_memory",
                            success=True,
                            query=raw_text,
                            confidence=local_confidence,
                            answer_source="verified_local",
                        )
                    log_event(logger, "source_reliability_applied", source="decision.answer", success=True, query=raw_text, source_rank="verified_local")
                    log_event(logger, "local_success", source="decision.answer", success=True, query=raw_text)
                    self._store_session_cache(answer_query, text)
                    source_name = "decision.local_generated" if local_source == "local_generated" else "decision.local"
                    source_note = "local_generated" if local_source == "local_generated" else "local_match"
                    return DecisionResult(mode="answer", source=source_name, response=text, notes=notes + [source_note])
            else:
                local_failed = True
            local_error = str(local_result.get("error", "unknown")).strip() or "unknown"
            log_event(logger, "local_failure", source="decision.answer", success=False, query=raw_text, reason=local_error)

        if not follow_up_request and self._should_attempt_web_research(answer_query, query_type, local_failed=local_failed):
            # ==============================================================
            # Weather API shortcut — use structured API instead of scraping.
            # ==============================================================
            if self._is_weather_query(normalized_query):
                try:
                    from august.weather_service import get_weather
                    locations = self._extract_locations_from_query(normalized_query)
                    location = locations[0] if locations else "auto-detected"
                    weather = get_weather(location)
                    if weather.success:
                        log_event(logger, "weather_api_used", source="decision.answer", success=True, query=raw_text, location=location)
                        topic = self._topic_from_context(memory) or self._normalize(answer_query)
                        self._remember_answer(raw_text, weather.summary, topic)
                        if not follow_up_request:
                            self._update_last_query(answer_query)
                        self._store_session_cache(answer_query, weather.summary)
                        return DecisionResult(mode="answer", source="decision.weather_api", response=weather.summary, notes=notes + ["weather_api"])
                except Exception as exc:
                    log_event(logger, "weather_api_failed", source="decision.answer", success=False, query=raw_text, error=str(exc))

            query = self._normalize(answer_query)
            if query and not follow_up_request:
                self._last_query = query
                logger.info("last_query updated -> %s", self._last_query)
            log_event(logger, "web_research_fallback_triggered", source="decision.answer", success=True, query=raw_text, query_type=query_type, local_failed=local_failed)
            plan = CommandPlan(
                commands=[
                    ParsedCommand(
                        action="web_research",
                        payload={"query": answer_query, "query_type": query_type, "include_attribution": True},
                        source="decision",
                        priority=80,
                    )
                ],
                raw_text=raw_text,
                source="decision.web_research",
            )
            log_event(logger, "web_research_selected", source="decision.answer", success=True, query=raw_text, query_type=query_type)
            return DecisionResult(mode="action", source="decision.web_research", plan=plan, notes=notes + ["web_research"])

        ai_result = self.ai_parser.try_ai_answer(answer_query, context=context, memory=memory)
        if ai_result.get("success"):
            text = str(ai_result.get("text", "")).strip()
            ai_confidence = 0.8
            topic = self._topic_from_context(memory) or self._normalize(answer_query)
            self._remember_answer(raw_text, text, topic)
            if not follow_up_request:
                self._update_last_query(answer_query)
            if self.answer_memory.store(answer_query, text, confidence=ai_confidence, source="ai"):
                log_event(logger, "memory_store", source="decision.answer_memory", success=True, query=raw_text, confidence=ai_confidence, answer_source="ai")
            log_event(logger, "source_reliability_applied", source="decision.answer", success=True, query=raw_text, source_rank="ai")
            log_event(logger, "ai_success", source="decision.answer", success=True, query=raw_text)
            self._store_session_cache(answer_query, text)
            return DecisionResult(mode="answer", source="decision.ai", response=text, notes=notes + ["ai_success"])

        ai_error = str(ai_result.get("error", "unknown")).strip() or "unknown"
        log_event(logger, "ai_failure", source="decision.answer", success=False, query=raw_text, reason=ai_error)

        log_event(logger, "fallback_triggered", source="decision.answer", success=False, query=raw_text)
        query = self._normalize(answer_query)
        if query and not follow_up_request:
            self._last_query = query
            logger.info("last_query updated -> %s", self._last_query)
            log_event(
                logger,
                "knowledge_context_preserved_after_failure",
                source="decision.answer",
                success=True,
                query=query,
            )
        self._set_pending_clarification(
            kind="answer_vs_search",
            prompt=self.SEARCH_PROMPT,
            original_text=raw_text,
            options=["answer", "search"],
            payload={"query": query, "original_query": raw_text, "options": ["answer", "search"], "timestamp": time.time()},
        )
        return DecisionResult(mode="answer", source="decision.fallback_prompt", response=self.SEARCH_PROMPT, notes=notes + ["fallback_triggered"])

    def _should_attempt_web_research(self, query: str, query_type: str | None = None, local_failed: bool = False) -> bool:
        resolved_type = query_type or self._classify_query_type(query, parsed_plan=None)
        if resolved_type in self.WEB_RESEARCH_QUERY_TYPES:
            return True
        # Allow static_knowledge to fall back to web research when local answers
        # failed or were too weak.  This prevents hallucination for unknown terms.
        if local_failed and resolved_type == QUERY_TYPES["static_knowledge"]:
            return True
        return False

    def _get_session_cache(self, query: str) -> str:
        key = self._normalize(query)
        if not key:
            return ""
        cached = self._session_cache.get(key)
        if not cached:
            return ""
        if time.time() - float(cached.get("timestamp", 0.0) or 0.0) > self.SESSION_CACHE_TTL:
            self._session_cache.pop(key, None)
            return ""
        return str(cached.get("response", "") or "").strip()

    def _store_session_cache(self, query: str, response: str) -> None:
        key = self._normalize(query)
        clean_response = str(response or "").strip()
        if not key or not clean_response:
            return
        self._session_cache[key] = {"response": clean_response, "timestamp": time.time()}
        log_event(logger, "session_cache_store", source="decision.session_cache", success=True, query=query)

    def _is_weak_local_answer(self, query: str, answer: str, confidence: float) -> bool:
        if confidence < self.HIGH_CONFIDENCE_THRESHOLD:
            return True
        normalized_answer = self._normalize(answer)
        weak_phrases = (
            "is a concept that refers to",
            "is an important concept",
            "core idea, common use cases, and trade-offs",
            "it generally involves understanding",
            "is a concept that refers to how a specific idea",
            "a core idea used to explain how something works",
        )
        return any(phrase in normalized_answer for phrase in weak_phrases)

    def _query_type(self, query: str) -> str:
        normalized = self._normalize(query)
        if not normalized:
            return "unknown"
        if any(marker in normalized for marker in ("latest", "breaking news", "today's news", "todays news", "news")):
            return QUERY_TYPES["current_event"]
        if any(marker in normalized for marker in ("happening in", "current events", "yesterday", "today", "recent")):
            return QUERY_TYPES["current_event"]
        if any(
            marker in normalized
            for marker in (
                "chief minister",
                "prime minister",
                "president",
                "leader of opposition",
                "who won",
                "ranking",
                "stock price",
                "share price",
                "election",
                "score",
                "result",
            )
        ):
            return QUERY_TYPES["dynamic_fact"]
        return classify_query(normalized)

    def _classify_query_type(self, query: str, parsed_plan: CommandPlan | None) -> str:
        normalized = self._normalize(query)
        if not normalized:
            return QUERY_TYPES["conversation"]
        if parsed_plan and parsed_plan.commands:
            log_event(logger, "query_classified", source="decision", success=True, query=normalized, query_type=QUERY_TYPES["command"])
            return QUERY_TYPES["command"]
        if self._looks_like_command(normalized):
            log_event(logger, "query_classified", source="decision", success=True, query=normalized, query_type=QUERY_TYPES["command"])
            return QUERY_TYPES["command"]
        if self._is_conversational_query_text(normalized) or normalized in self.YES_WORDS or normalized in self.NO_WORDS:
            log_event(logger, "query_classified", source="decision", success=True, query=normalized, query_type=QUERY_TYPES["conversation"])
            return QUERY_TYPES["conversation"]

        legacy_type = self._query_type(normalized)
        if legacy_type == QUERY_TYPES["dynamic_fact"]:
            query_type = QUERY_TYPES["dynamic_fact"]
        elif legacy_type == QUERY_TYPES["current_event"]:
            query_type = QUERY_TYPES["current_event"]
        elif self._is_dynamic_fact_query(normalized):
            query_type = QUERY_TYPES["dynamic_fact"]
        elif legacy_type in {"comparison", "algorithmic"} or self._is_reasoning_query(normalized):
            query_type = QUERY_TYPES["reasoning"]
        elif legacy_type in {"conceptual", "definition"} or self._is_static_knowledge_query(normalized):
            query_type = QUERY_TYPES["static_knowledge"]
        elif legacy_type == "factual":
            query_type = QUERY_TYPES["dynamic_fact"] if self._is_dynamic_fact_query(normalized) else QUERY_TYPES["static_knowledge"]
        elif self._is_follow_up(normalized):
            query_type = QUERY_TYPES["reasoning"]
        elif self._looks_like_question(normalized):
            query_type = QUERY_TYPES["static_knowledge"]
        else:
            query_type = QUERY_TYPES["conversation"]
        log_event(logger, "query_classified", source="decision", success=True, query=normalized, query_type=query_type)
        log_event(logger, "classification_refined", source="decision", success=True, query=normalized, query_type=query_type)
        return query_type

    def _is_conversational_query_text(self, normalized: str) -> bool:
        return any(normalized == pattern or normalized.startswith(pattern) for pattern in self.CONVERSATIONAL_QUERY_PATTERNS)

    def _is_dynamic_fact_query(self, normalized: str) -> bool:
        return any(
            marker in normalized
            for marker in (
                "current ",
                "latest",
                "today",
                "yesterday",
                "news",
                "stock price",
                "share price",
                "ranking",
                "who won",
                "chief minister",
                "prime minister",
                "president",
                "leader of opposition",
                "result",
                "score",
            )
        )

    def _is_reasoning_query(self, normalized: str) -> bool:
        if any(marker in normalized for marker in ("difference between", "compare ", " vs ", " versus ", "trade-off", "tradeoff")):
            return True
        if normalized.startswith(("why ", "how does ", "how do ", "how can ")):
            return True
        return any(
            marker in normalized
            for marker in (
                "internals",
                "architecture",
                "design",
                "efficient",
                "faster",
                "slower",
                "complexity",
                "causes",
                "reason behind",
            )
        )

    def _is_static_knowledge_query(self, normalized: str) -> bool:
        if self._is_dynamic_fact_query(normalized) or self._is_reasoning_query(normalized):
            return False
        if normalized.startswith(("what is ", "what are ", "define ", "who is ", "explain ")):
            return True
        return False

    def _remember_answer(self, raw_text: str, response: str, topic: str) -> None:
        clean_topic = (topic or "").strip()
        if clean_topic:
            self._last_topic = clean_topic
        remember_answer(self.memory_store, raw_text, response, topic=clean_topic)

    def _is_fallback_reply_context(self, context: dict[str, Any]) -> bool:
        last_action = self._normalize(str(context.get("last_action", "") or ""))
        if "fallback" in last_action:
            return True
        last_answer = self._normalize(str(context.get("last_answer", "") or ""))
        return "want me to search it" in last_answer or "having trouble getting a reliable answer" in last_answer

    def get_pending_interaction(self) -> dict[str, Any] | None:
        """Expose pending clarification state using the shared context schema."""
        pending = self._pending_clarification
        if pending is None:
            return None
        return {
            "type": pending.kind,
            "original_query": pending.original_text,
            "options": list(pending.options),
            "timestamp": pending.timestamp,
        }

    def _is_weather_query(self, normalized: str) -> bool:
        """Check if the query is asking about weather."""
        weather_markers = {"weather", "temperature", "rain", "humidity", "wind", "forecast", "sunny", "cloudy"}
        return any(marker in normalized for marker in weather_markers)

    def _extract_locations_from_query(self, normalized: str) -> list[str]:
        """Extract location entities from the query text."""
        from august.entity_guard import extract_entities
        entities = extract_entities(normalized)
        locations: list[str] = []
        for city in entities.cities:
            locations.append(city.title())
        for state in entities.states:
            locations.append(state.title())
        for country in entities.countries:
            locations.append(country.title())
        return locations
