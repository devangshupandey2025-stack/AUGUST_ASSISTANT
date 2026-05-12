from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app_registry import AppRegistry
from ai_parser import AIParser, AnswerResult
from context_engine import ContextEngine, IST
from decision_engine import DecisionEngine
from document_generator import generate_document
from executor import Executor
from intent_parser import CommandPlan, ParsedCommand
from memory import MemoryStore
from personality_engine import PersonalityEngine


class DummyAIParser:
    def __init__(self, ai_success: bool = True):
        self.ai_success = ai_success
        self.last_ai_query = ""

    def answer(self, user_input, context=None, memory=None):
        return f"answer:{user_input}"

    def answer_with_fallback(self, user_input, context=None, memory=None):
        return AnswerResult(text=f"answer:{user_input}", source="ai", topic=str(user_input))

    def try_ai_answer(self, user_input, context=None, memory=None):
        self.last_ai_query = user_input
        if self.ai_success:
            return {"success": True, "text": f"answer:{user_input}", "error": ""}
        return {"success": False, "text": "", "error": "http_503"}


class FakeAppRegistry:
    def __init__(self, matches=None):
        self.matches = matches or {}

    def find_app(self, query: str):
        return self.matches.get(query)

    def find_app_match(self, query: str):
        path = self.matches.get(query)
        if not path:
            return None
        return {"app": query, "path": path, "match_type": "exact", "confidence": 1.0}


class DecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        memory_path = Path(self.temp_dir.name) / "memory.json"
        self.memory = MemoryStore(memory_path=memory_path)
        self.registry = FakeAppRegistry({"spotify": r"C:\Apps\Spotify.exe", "vscode": r"C:\Apps\Code.exe"})
        self.engine = DecisionEngine(ai_parser=DummyAIParser(), memory_store=self.memory, app_registry=self.registry)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_close_command_uses_last_app_from_context(self) -> None:
        result = self.engine.decide(
            raw_text="close",
            parsed_plan=None,
            context={"last_app": "chrome", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertIsNotNone(result.plan)
        self.assertEqual(result.plan.commands[0].action, "close_app")
        self.assertEqual(result.plan.commands[0].payload["app"], "chrome")

    def test_unknown_open_app_falls_back_to_search(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "quantum mixtape"}, source="rule")],
            raw_text="open quantum mixtape",
            source="rule",
        )

        result = self.engine.decide(
            raw_text="open quantum mixtape",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "open_app")
        self.assertEqual(result.plan.commands[0].payload["app"], "quantum mixtape")

    def test_open_youtube_for_song_becomes_search(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "youtube for song"}, source="ai")],
            raw_text="open youtube for song",
            source="ai",
        )

        result = self.engine.decide(
            raw_text="open youtube for song",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "night"},
            memory=self.memory.snapshot(),
        )

        command = result.plan.commands[0]
        self.assertEqual(command.action, "search_web")
        self.assertEqual(command.payload["site"], "youtube")
        self.assertEqual(command.payload["query"], "song")

    def test_unknown_direct_open_phrase_stays_open_app(self) -> None:
        result = self.engine.decide(
            raw_text="open randomtool",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIn("did you mean to open randomtool", result.response.lower())

    def test_registry_match_adds_path_to_open_app(self) -> None:
        result = self.engine.decide(
            raw_text="open spotify for me",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        command = result.plan.commands[0]
        self.assertEqual(command.action, "open_app")
        self.assertEqual(command.payload["app"], "spotify")
        self.assertEqual(command.payload["path"], r"C:\Apps\Spotify.exe")

    def test_memory_search_web_can_be_corrected_to_open_app(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": "spotify"}, source="memory")],
            raw_text="open spotify",
            source="memory",
        )

        result = self.engine.decide(
            raw_text="open spotify",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        command = result.plan.commands[0]
        self.assertEqual(command.action, "open_app")
        self.assertEqual(command.payload["app"], "spotify")
        self.assertEqual(command.payload["path"], r"C:\Apps\Spotify.exe")

    def test_answer_mode_bypasses_execution_plan(self) -> None:
        result = self.engine.decide(
            raw_text="what is the difference between lists and tuples",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIsNone(result.plan)
        self.assertIn("answer:what is the difference between lists and tuples", result.response)

    def test_conversational_question_prefers_answer_mode_over_search(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": "ai jobs"}, source="ai")],
            raw_text="do you think ai will replace jobs",
            source="ai",
        )

        result = self.engine.decide(
            raw_text="do you think ai will replace jobs",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIn("answer:do you think ai will replace jobs", result.response)

    def test_garbage_input_prompts_rephrase(self) -> None:
        result = self.engine.decide(
            raw_text="sleep sleep sleep",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.response, "That doesn't look like a valid command. Can you rephrase?")

    def test_toxic_input_is_not_searched(self) -> None:
        result = self.engine.decide(
            raw_text="fuck you",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.response, "Alright, noted.")

    def test_follow_up_uses_last_topic(self) -> None:
        first = self.engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(first.mode, "answer")

        second = self.engine.decide(
            raw_text="give example",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(second.mode, "answer")
        self.assertIn("give example for what is polymorphism", second.response)

    def test_follow_up_without_context_asks_for_clarification(self) -> None:
        result = self.engine.decide(
            raw_text="explain more",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.response, "What would you like me to explain?")

    def test_follow_up_does_not_call_ai_with_raw_text(self) -> None:
        first = self.engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(first.mode, "answer")

        second = self.engine.decide(
            raw_text="explain more",
            parsed_plan=None,
            context={"last_query": "what is polymorphism", "last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(second.mode, "answer")
        self.assertNotEqual(self.engine.ai_parser.last_ai_query, "explain more")
        self.assertIn("what is polymorphism", self.engine.ai_parser.last_ai_query)

    def test_ambiguous_open_app_asks_for_clarification_then_resolves(self) -> None:
        registry = FakeAppRegistry(
            {
                "code": r"C:\Apps\Code.exe",
                "codeblocks": r"C:\Apps\CodeBlocks.exe",
                "spotify": r"C:\Apps\Spotify.exe",
            }
        )
        engine = DecisionEngine(ai_parser=DummyAIParser(), memory_store=self.memory, app_registry=registry)

        result = engine.decide(
            raw_text="open code",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "answer")
        self.assertIn("did you mean", result.response.lower())

        follow_up = engine.decide(
            raw_text="yes",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(follow_up.mode, "action")
        self.assertEqual(follow_up.plan.commands[0].action, "open_app")

    def test_local_fallback_answers_factual_query_when_ai_fails(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="who is the prime minister of india",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIn("narendra modi", result.response.lower())

    def test_ai_failure_returns_controlled_fallback_without_crash(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        first = engine.decide(
            raw_text="explain distributed tracing internals in modern service meshes",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(first.mode, "answer")
        self.assertEqual(first.response, "I'm having trouble getting a reliable answer. Do you want me to search it?")

        second = engine.decide(
            raw_text="search it",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(second.mode, "action")
        self.assertEqual(second.plan.commands[0].action, "search_web")
        self.assertEqual(second.plan.commands[0].payload["site"], "google")

    def test_low_confidence_does_not_execute(self) -> None:
        result = self.engine.decide(
            raw_text="do something with that thing",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIsNone(result.plan)
        self.assertIn("not confident", result.response.lower())

    def test_medium_confidence_requests_clarification(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "calc"}, source="ai")],
            raw_text="open calc",
            source="ai",
        )

        result = self.engine.decide(
            raw_text="open calc",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIn("did you mean to open calc", result.response.lower())

        follow_up = self.engine.decide(
            raw_text="yes",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(follow_up.mode, "action")
        self.assertEqual(follow_up.plan.commands[0].action, "open_app")

    def test_multi_step_plan_is_preserved(self) -> None:
        parsed_plan = CommandPlan(
            commands=[
                ParsedCommand(action="open_app", payload={"app": "chrome"}, source="ai"),
                ParsedCommand(action="search_web", payload={"site": "google", "query": "python"}, source="ai"),
            ],
            raw_text="open chrome and search python",
            source="ai",
        )

        result = self.engine.decide(
            raw_text="open chrome and search python",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(len(result.plan.commands), 2)
        self.assertEqual(result.plan.commands[0].action, "open_app")
        self.assertEqual(result.plan.commands[1].action, "search_web")

    def test_pending_interaction_lock_resolves_before_plan(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "spotify"}, source="rule")],
            raw_text="open spotify",
            source="rule",
        )
        result = self.engine.decide(
            raw_text="answer",
            parsed_plan=parsed_plan,
            context={
                "last_query": "what is polymorphism",
                "pending_interaction": {
                    "type": "answer_vs_search",
                    "original_query": "what is polymorphism",
                    "options": ["answer", "search"],
                    "timestamp": datetime.now(IST).isoformat(),
                },
                "time_of_day": "afternoon",
            },
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "answer")
        self.assertIsNone(result.plan)
        self.assertIn("polymorphism", result.response.lower())

    def test_unrelated_command_bypasses_pending_interaction(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "spotify"}, source="rule")],
            raw_text="open spotify",
            source="rule",
        )
        result = self.engine.decide(
            raw_text="open spotify",
            parsed_plan=parsed_plan,
            context={
                "pending_interaction": {
                    "type": "answer_vs_search",
                    "original_query": "what is polymorphism",
                    "options": ["answer", "search"],
                    "timestamp": datetime.now(IST).isoformat(),
                },
                "time_of_day": "afternoon",
            },
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "open_app")

    def test_search_it_without_pending_uses_last_query(self) -> None:
        result = self.engine.decide(
            raw_text="search it",
            parsed_plan=None,
            context={"last_query": "who is the prime minister of india", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "search_web")
        self.assertEqual(result.plan.commands[0].payload["query"], "who is the prime minister of india")

    def test_search_for_it_resolves_context_query(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": "it"}, source="rule")],
            raw_text="search for it",
            source="rule",
        )
        result = self.engine.decide(
            raw_text="search for it",
            parsed_plan=parsed_plan,
            context={"last_query": "quantum entanglement", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].payload["query"], "quantum entanglement")

    def test_follow_up_chain_preserves_context(self) -> None:
        first = self.engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(first.mode, "answer")

        second = self.engine.decide(
            raw_text="explain more",
            parsed_plan=None,
            context={"last_query": "what is polymorphism", "last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(second.mode, "answer")
        self.assertIn("what is polymorphism", self.engine.ai_parser.last_ai_query)

        third = self.engine.decide(
            raw_text="give example",
            parsed_plan=None,
            context={"last_query": "what is polymorphism", "last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(third.mode, "answer")
        self.assertIn("what is polymorphism", third.response)

    def test_repetition_is_cleaned_before_processing(self) -> None:
        result = self.engine.decide(
            raw_text="what what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(result.mode, "answer")
        self.assertNotIn("rephrase", result.response.lower())

    def test_document_generation_intent_extracts_topic(self) -> None:
        result = self.engine.decide(
            raw_text="make notes on polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "generate_document")
        self.assertEqual(result.plan.commands[0].payload["topic"], "polymorphism")

    def test_document_generator_creates_structured_docx_from_local_knowledge(self) -> None:
        result = generate_document(
            "operating systems",
            memory_store=self.memory,
            ai_parser=DummyAIParser(ai_success=False),
            open_file=False,
            output_dir=self.temp_dir.name,
        )

        self.assertTrue(result.success)
        self.assertTrue(Path(result.filename).exists())
        self.assertFalse(result.opened)

    def test_executor_document_failure_is_controlled(self) -> None:
        executor = Executor(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory)
        result = executor.execute(ParsedCommand(action="generate_document", payload={"topic": "xqz norel topic", "open_file": False}))

        self.assertFalse(result.success)
        self.assertEqual(result.message, "I couldn't generate a reliable document. Do you want me to search instead?")


class ContextEngineTests(unittest.TestCase):
    def test_time_of_day_uses_local_clock_buckets(self) -> None:
        context = ContextEngine(history_size=5)
        local_tz = datetime.now().astimezone().tzinfo

        self.assertEqual(context.get_time_of_day(datetime(2026, 5, 3, 8, 0, tzinfo=local_tz)), "morning")
        self.assertEqual(context.get_time_of_day(datetime(2026, 5, 3, 14, 0, tzinfo=local_tz)), "afternoon")
        self.assertEqual(context.get_time_of_day(datetime(2026, 5, 3, 20, 0, tzinfo=local_tz)), "night")

    def test_startup_greeting_is_not_rewritten_to_ready_phrase(self) -> None:
        engine = PersonalityEngine()

        rendered = engine.render_for_tts("Good evening, Boss.")

        self.assertEqual(rendered, "Good evening, Boss.")
        self.assertNotIn("Ready when you are", rendered)

    def test_context_tracks_last_app_last_action_and_recent_commands(self) -> None:
        context = ContextEngine(history_size=5)
        for command_text in ("one", "two", "three", "four", "five", "six"):
            context.update_context(command_text=command_text)

        plan = CommandPlan(
            commands=[ParsedCommand(action="open_app", payload={"app": "chrome", "path": r"C:\Apps\Chrome.exe"}, source="rule")],
            raw_text="open chrome",
            source="rule",
        )
        snapshot = context.update_context(plan=plan)

        self.assertEqual(snapshot["last_app"], "chrome")
        self.assertEqual(snapshot["last_action"], "open_app")
        self.assertEqual(snapshot["recent_commands"], ["two", "three", "four", "five", "six"])

    def test_context_exposes_unified_model_fields(self) -> None:
        context = ContextEngine(history_size=5)
        snapshot = context.update_context(command_text="what is polymorphism", last_action="answer_query")
        self.assertIn("last_query", snapshot)
        self.assertIn("last_answer", snapshot)
        self.assertIn("pending_interaction", snapshot)
        self.assertIn("conversation_history", snapshot)
        self.assertIn("timestamp", snapshot)
        self.assertEqual(snapshot["last_query"], "what is polymorphism")

    def test_pending_interaction_expires_after_timeout(self) -> None:
        context = ContextEngine(history_size=5)
        old_ts = (datetime.now(IST) - timedelta(seconds=25)).isoformat()
        context.update_context(
            pending_interaction={
                "type": "answer_vs_search",
                "original_query": "what is polymorphism",
                "options": ["answer", "search"],
                "timestamp": old_ts,
            }
        )
        snapshot = context.get_context()
        self.assertIsNone(snapshot["pending_interaction"])

    def test_skip_query_update_prevents_context_pollution(self) -> None:
        context = ContextEngine(history_size=5)
        context.update_context(command_text="what is polymorphism", last_action="answer_query")
        snapshot = context.update_context(
            command_text="sleep sleep sleep",
            last_action="garbage_detected",
            response_text="That doesn't look like a valid command. Can you rephrase?",
            skip_query_update=True,
        )
        self.assertEqual(snapshot["last_query"], "what is polymorphism")

    def test_followup_is_auto_skipped_from_last_query(self) -> None:
        context = ContextEngine(history_size=5)
        context.update_context(command_text="what is polymorphism", last_action="answer_query")
        snapshot = context.update_context(
            command_text="explain more please",
            last_action="answer_query",
            response_text="expanded explanation",
        )
        self.assertEqual(snapshot["last_query"], "what is polymorphism")


class AppRegistryTests(unittest.TestCase):
    def test_program_files_scan_and_find_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            exe_path = root / "Spotify" / "Spotify.exe"
            exe_path.parent.mkdir(parents=True, exist_ok=True)
            exe_path.write_text("", encoding="utf-8")

            registry = AppRegistry(
                cache_path=root / "cache.json",
                start_menu_roots=[],
                program_roots=[root],
            )

            registry.build_registry()
            self.assertEqual(registry.find_app("spotify"), str(exe_path))

    def test_registry_inconsistency_falls_back_to_cached_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            cached_registry = {f"app{i}": fr"C:\Apps\App{i}.exe" for i in range(12)}
            cache_path.write_text(json.dumps(cached_registry), encoding="utf-8")

            registry = AppRegistry(
                cache_path=cache_path,
                start_menu_roots=[],
                program_roots=[],
            )

            built = registry.build_registry()
            self.assertEqual(len(built), len(cached_registry))
            self.assertEqual(built["app1"], r"C:\Apps\App1.exe")

    def test_partial_match_is_rejected_for_safety(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            exe_path = root / "Visual Studio Code" / "Code.exe"
            exe_path.parent.mkdir(parents=True, exist_ok=True)
            exe_path.write_text("", encoding="utf-8")

            registry = AppRegistry(
                cache_path=root / "cache.json",
                start_menu_roots=[],
                program_roots=[root],
            )
            registry.build_registry()
            self.assertIsNone(registry.find_app("vis"))


class AIParserTests(unittest.TestCase):
    def test_ai_failure_logs_retry_and_final_failure(self) -> None:
        parser = AIParser(api_key="test-key", max_retries=1, timeout_seconds=1)
        error_response = Mock()
        error_response.status_code = 429
        error = requests.HTTPError("rate limited")
        error.response = error_response

        with patch("ai_parser.requests.post", side_effect=error), patch("ai_parser.time.sleep", return_value=None), self.assertLogs("AIParser", level="INFO") as logs:
            result = parser.try_ai_answer("what is recursion", context={}, memory={})

        joined_logs = "\n".join(logs.output)
        self.assertFalse(result["success"])
        self.assertIn("ai_retry", joined_logs)
        self.assertIn("ai_final_failure", joined_logs)


if __name__ == "__main__":
    unittest.main()
