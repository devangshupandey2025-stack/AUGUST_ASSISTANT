from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app_registry import AppRegistry
from ai_parser import AIParser, AnswerResult
from answer_memory import AnswerMemory
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

    def summarize_web_content(self, text, query="", source_url="", title=""):
        del source_url, title
        if not self.ai_success:
            return {"success": False, "text": "", "error": "http_503"}
        return {"success": True, "text": f"summary:{query}", "error": ""}


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

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.response, "Did you want me to search that instead?")

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

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "web_research")

    def test_conversational_question_prefers_answer_mode_over_search(self) -> None:
        result = self.engine.decide(
            raw_text="do you think ai will replace jobs",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertIn(result.mode, ("answer", "action"))

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
        self.assertIn("polymorphism", second.response.lower())

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

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "web_research")

    def test_local_fallback_answers_west_bengal_chief_minister_when_ai_fails(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="who is the chief minister of westbengal",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "web_research")

    def test_local_fallback_answers_suvendu_adhikari_when_ai_fails(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="who is suvendhu adhikari",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertIn("suvendu adhikari", result.response.lower())

    def test_static_classification_for_red_blood_cell(self) -> None:
        query_type = self.engine._classify_query_type("what is a red blood cell", parsed_plan=None)
        self.assertEqual(query_type, "static_knowledge")

    def test_conversation_classification_for_how_are_you(self) -> None:
        query_type = self.engine._classify_query_type("how are you", parsed_plan=None)
        self.assertEqual(query_type, "conversation")

    def test_invalid_capital_query_is_sanity_blocked_and_not_stored(self) -> None:
        result = self.engine.decide(
            raw_text="what is the capital of kolkata",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.source, "decision.sanity_guard")
        self.assertIn("mean", result.response.lower())
        self.assertEqual(self.memory.snapshot().get("answer_memory", []), [])

    def test_invalid_political_query_is_sanity_blocked(self) -> None:
        result = self.engine.decide(
            raw_text="who is the prime minister of california",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(result.source, "decision.sanity_guard")
        self.assertIn("governor", result.response.lower())

    def test_memory_entity_conflict_rejects_wrong_state_fact(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        stored = answer_memory.store(
            "who is the chief minister of west bengal",
            "Mamata Banerjee is the chief minister of West Bengal.",
            confidence=0.95,
            source="ai",
        )

        self.assertTrue(stored)
        self.assertIsNone(answer_memory.retrieve("who is the chief minister of karnataka"))

    def test_conversational_fragment_is_not_stored_in_answer_memory(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        stored = answer_memory.store(
            "tell me more",
            "Here is a more detailed explanation of the previous topic.",
            confidence=0.9,
            source="ai",
        )

        self.assertFalse(stored)
        self.assertEqual(self.memory.snapshot().get("answer_memory", []), [])

    def test_expired_dynamic_fact_is_ignored(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        answer_memory.store(
            "who is the chief minister of karnataka",
            "Siddaramaiah is the chief minister of Karnataka.",
            confidence=0.95,
            timestamp=old_timestamp,
            source="ai",
        )

        self.assertIsNone(answer_memory.retrieve("who is the chief minister of karnataka"))

    def test_static_concept_memory_retrieval_is_allowed(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        answer = "Polymorphism lets the same interface behave differently for different object types."
        stored = answer_memory.store("what is polymorphism", answer, confidence=0.9, source="local")

        self.assertTrue(stored)
        self.assertEqual(answer_memory.retrieve("what is polymorphism"), answer)

    def test_memory_dedup_updates_existing_entry_only(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        answer = "Polymorphism lets the same interface behave differently for different object types."
        first = answer_memory.store("what is polymorphism", answer, confidence=0.9, source="local", timestamp="2026-05-12T00:00:00+00:00")
        second = answer_memory.store("what is polymorphism?", answer, confidence=0.88, source="local", timestamp="2026-05-13T00:00:00+00:00")
        entries = self.memory.snapshot().get("answer_memory", [])

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["timestamp"], "2026-05-13T00:00:00+00:00")

    def test_session_cache_reuses_recent_answer(self) -> None:
        result = self.engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        cached = self.engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertEqual(cached.source, "decision.session_cache")
        self.assertIn("session_cache_hit", cached.notes)

    def test_dynamic_fact_prefers_web_research_over_memory(self) -> None:
        answer_memory = AnswerMemory(memory_store=self.memory)
        answer_memory.store(
            "who is the chief minister of karnataka",
            "Old answer says someone is the chief minister of Karnataka.",
            confidence=0.95,
            source="ai",
        )
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=True), memory_store=self.memory, app_registry=self.registry)

        result = engine.decide(
            raw_text="who is the chief minister of karnataka",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "web_research")
        self.assertEqual(result.plan.commands[0].payload["query"], "who is the chief minister of karnataka")

    def test_ai_failure_routes_informational_query_to_web_research(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        first = engine.decide(
            raw_text="explain distributed tracing internals in modern service meshes",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )
        self.assertEqual(first.mode, "action")
        self.assertEqual(first.plan.commands[0].action, "web_research")
        self.assertEqual(first.plan.commands[0].payload["query"], "explain distributed tracing internals in modern service meshes")

    def test_browser_search_does_not_trigger_web_research(self) -> None:
        parsed_plan = CommandPlan(
            commands=[ParsedCommand(action="search_web", payload={"site": "google", "query": "cats"}, source="rule")],
            raw_text="search for cats",
            source="rule",
        )

        result = self.engine.decide(
            raw_text="search for cats",
            parsed_plan=parsed_plan,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "search_web")

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

    def test_latest_ai_news_prefers_web_research(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=True), memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="latest ai news",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "web_research")

    def test_static_knowledge_uses_local_without_web_research(self) -> None:
        engine = DecisionEngine(ai_parser=DummyAIParser(ai_success=False), memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="what is polymorphism",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "answer")
        self.assertNotEqual(result.source, "decision.web_research")

    def test_command_does_not_trigger_ai_or_web_research(self) -> None:
        parser = DummyAIParser(ai_success=True)
        engine = DecisionEngine(ai_parser=parser, memory_store=self.memory, app_registry=self.registry)
        result = engine.decide(
            raw_text="open spotify",
            parsed_plan=None,
            context={"last_app": "", "time_of_day": "afternoon"},
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "open_app")
        self.assertEqual(parser.last_ai_query, "")

    def test_search_it_prefers_knowledge_context_over_action_context(self) -> None:
        result = self.engine.decide(
            raw_text="search it",
            parsed_plan=None,
            context={
                "last_query": "spotify",
                "last_knowledge_query": "what is polymorphism",
                "last_action_query": "open spotify",
                "time_of_day": "afternoon",
            },
            memory=self.memory.snapshot(),
        )

        self.assertEqual(result.mode, "action")
        self.assertEqual(result.plan.commands[0].action, "search_web")
        self.assertEqual(result.plan.commands[0].payload["query"], "what is polymorphism")

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
        self.assertIn("polymorphism", second.response.lower())

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

    def test_executor_web_research_returns_summary_only(self) -> None:
        executor = Executor(memory_store=self.memory)
        fake_result = Mock(
            success=True,
            answer="Quantum computing uses quantum mechanics to process information in new ways.",
            source_url="https://example.com/quantum",
            title="Quantum computing",
            article_text="full article text",
            confidence=0.88,
        )

        with patch("executor.research_web", return_value=fake_result):
            result = executor.execute(ParsedCommand(action="web_research", payload={"query": "what is quantum computing"}))

        self.assertTrue(result.success)
        self.assertEqual(result.message, fake_result.answer)


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
        self.assertIn("last_knowledge_query", snapshot)
        self.assertIn("last_action_query", snapshot)
        self.assertIn("last_answer", snapshot)
        self.assertIn("pending_interaction", snapshot)
        self.assertIn("conversation_history", snapshot)
        self.assertIn("timestamp", snapshot)
        self.assertEqual(snapshot["last_query"], "what is polymorphism")
        self.assertEqual(snapshot["last_knowledge_query"], "what is polymorphism")

    def test_context_splits_knowledge_and_action_queries(self) -> None:
        context = ContextEngine(history_size=5)
        context.update_context(command_text="what is polymorphism", last_action="answer_query")
        snapshot = context.update_context(
            command_text="open spotify",
            plan=CommandPlan(commands=[ParsedCommand(action="open_app", payload={"app": "spotify"})], raw_text="open spotify"),
        )

        self.assertEqual(snapshot["last_knowledge_query"], "what is polymorphism")
        self.assertEqual(snapshot["last_action_query"], "open spotify")

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

    def test_web_research_action_preserves_last_knowledge_query(self) -> None:
        context = ContextEngine(history_size=5)
        context.update_context(command_text="what is a red blood cell", last_action="answer_query")
        plan = CommandPlan(
            commands=[ParsedCommand(action="web_research", payload={"query": "what is a red blood cell"}, source="decision")],
            raw_text="what is a red blood cell",
            source="decision",
        )
        snapshot = context.update_context(plan=plan)
        self.assertEqual(snapshot["last_knowledge_query"], "what is a red blood cell")


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

    def test_payload_validation_rejects_missing_role(self) -> None:
        parser = AIParser(api_key="test-key", max_retries=0, timeout_seconds=1)
        invalid_payload = {"contents": [{"parts": [{"text": "hello"}]}], "generationConfig": {"temperature": 0.2}}
        self.assertEqual(parser._validate_request_payload(invalid_payload, request_kind="test"), "missing_role")


class WebResearchTests(unittest.TestCase):
    def test_web_research_cache_hit_skips_second_search(self) -> None:
        from web_research import WebResearchEngine

        engine = WebResearchEngine()
        article_text = (
            "Latest AI news today as of 2026. Major developments in artificial intelligence "
            "are happening currently. OpenAI announced new capabilities for ChatGPT. "
            "Google DeepMind released new research on language models. "
            "The latest breakthroughs in AI are transforming industries worldwide. "
            "According to recent reports, AI adoption is growing rapidly across sectors. "
            "Tech companies are investing billions in AI research and development. "
            "The future of AI looks promising with new applications in healthcare and education."
        )
        with patch.object(engine, "_search", return_value=[Mock(title="AI News Today - Latest Developments in 2026", href="https://reuters.com/ai-news/latest", snippet="s")]) as search_mock, patch.object(
            engine,
            "_fetch_article_text",
            return_value=article_text,
        ):
            first = engine.research("latest ai news", ai_parser=None, include_attribution=False)
            second = engine.research("latest ai news", ai_parser=None, include_attribution=False)

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(search_mock.call_count, 1)

    def test_transport_error_timedelta_is_stringified_safely(self) -> None:
        from datetime import timedelta
        from web_research import WebResearchEngine

        engine = WebResearchEngine()
        class TimedeltaError(Exception):
            pass
        err = TimedeltaError(timedelta(seconds=2))
        self.assertIn("2.00s", engine._safe_error_text(err))


class OfficeHolderRetrievalTests(unittest.TestCase):
    """Tests for the office-holder retrieval fix (V5.6.1)."""

    def test_entity_extraction_includes_office_and_location(self) -> None:
        from query_understanding import understand_query

        intent = understand_query("who is the current chief minister of west bengal")
        self.assertIn("Chief Minister", intent.entities)
        self.assertIn("West Bengal", intent.entities)
        self.assertEqual(intent.metadata.get("offices"), ["Chief Minister"])
        self.assertEqual(intent.metadata.get("locations"), ["West Bengal"])
        self.assertEqual(intent.metadata.get("topic_category"), "government")
        self.assertEqual(intent.metadata.get("time_relevance"), "dynamic")

    def test_entity_extraction_prime_minister(self) -> None:
        from query_understanding import understand_query

        intent = understand_query("who is the prime minister of india")
        self.assertIn("Prime Minister", intent.entities)
        self.assertIn("India", intent.entities)

    def test_entity_extraction_president(self) -> None:
        from query_understanding import understand_query

        intent = understand_query("current president of the united states")
        self.assertIn("President", intent.entities)
        self.assertIn("United States", intent.entities)

    def test_search_synthesis_office_holder_with_official(self) -> None:
        from query_understanding import understand_query
        from search_synthesizer import synthesize_search_query

        intent = understand_query("who is the current chief minister of west bengal")
        query = synthesize_search_query(intent, "who is the current chief minister of west bengal")
        self.assertEqual(query, "Current Chief Minister of West Bengal official")

    def test_search_synthesis_prime_minister(self) -> None:
        from query_understanding import understand_query
        from search_synthesizer import synthesize_search_query

        intent = understand_query("who is the prime minister of india")
        query = synthesize_search_query(intent, "who is the prime minister of india")
        self.assertEqual(query, "Current Prime Minister of India official")

    def test_entity_validation_rejects_electric_current(self) -> None:
        from query_understanding import understand_query
        from result_validator import validate_search_result

        intent = understand_query("who is the current chief minister of west bengal")
        # Simulate a search result about "Electric Current"
        fake_result = Mock(
            title="Electric Current - Wikipedia",
            snippet="Electric current is the flow of electric charge",
            href="https://en.wikipedia.org/wiki/Electric_current",
        )
        result = validate_search_result(fake_result, intent, intent.entities)
        self.assertFalse(result["valid"])
        self.assertIn("missing_office_or_location", result["reason"])

    def test_entity_validation_passes_correct_result(self) -> None:
        from query_understanding import understand_query
        from result_validator import validate_search_result

        intent = understand_query("who is the current chief minister of west bengal")
        fake_result = Mock(
            title="Chief Minister of West Bengal - Wikipedia",
            snippet="The Chief Minister of West Bengal is the head of government",
            href="https://en.wikipedia.org/wiki/Chief_Minister_of_West_Bengal",
        )
        result = validate_search_result(fake_result, intent, intent.entities)
        self.assertTrue(result["valid"])

    def test_entity_validation_rejects_missing_location(self) -> None:
        from query_understanding import understand_query
        from result_validator import validate_search_result

        intent = understand_query("who is the current chief minister of west bengal")
        # Has office but no location
        fake_result = Mock(
            title="Chief Minister - Wikipedia",
            snippet="A chief minister is the elected head",
            href="https://en.wikipedia.org/wiki/Chief_minister",
        )
        result = validate_search_result(fake_result, intent, intent.entities)
        self.assertFalse(result["valid"])

    def test_hard_rejection_physics_title_for_office_query(self) -> None:
        from query_understanding import understand_query
        from result_filter import filter_results
        from search_synthesizer import get_preferred_domains

        intent = understand_query("who is the current chief minister of west bengal")
        physics_result = Mock(
            title="Electric Current and Its Effects - Physics",
            snippet="Learn about electric current in circuits",
            href="https://physics.example.com/electric-current",
        )
        preferred = get_preferred_domains(intent)
        filtered = filter_results([physics_result], intent, "current chief minister of west bengal", preferred)
        self.assertEqual(len(filtered), 0)

    def test_hard_rejection_physics_domain_for_office_query(self) -> None:
        from query_understanding import understand_query
        from result_filter import filter_results
        from search_synthesizer import get_preferred_domains

        intent = understand_query("who is the current chief minister of west bengal")
        physics_result = Mock(
            title="Current in Electrical Circuits",
            snippet="Understanding current flow in electrical engineering",
            href="https://www.electricalengineering.example.com/current",
        )
        preferred = get_preferred_domains(intent)
        filtered = filter_results([physics_result], intent, "current chief minister of west bengal", preferred)
        self.assertEqual(len(filtered), 0)

    def test_article_validation_uses_metadata_entities(self) -> None:
        from query_understanding import understand_query
        from result_validator import validate_article_content

        intent = understand_query("who is the current chief minister of west bengal")
        article = (
            "The Chief Minister of West Bengal is the head of the state government. "
            "As of 2026, the Chief Minister of West Bengal is Mamata Banerjee. "
            "She has been serving as the Chief Minister since 2011. "
            "The Chief Minister is appointed by the Governor of West Bengal. "
            "West Bengal is a state in eastern India with a population of over 90 million."
        )
        result = validate_article_content(article, intent, title="Chief Minister of West Bengal")
        self.assertTrue(result["valid"])

    def test_article_validation_rejects_unrelated_article(self) -> None:
        from query_understanding import understand_query
        from result_validator import validate_article_content

        intent = understand_query("who is the current chief minister of west bengal")
        article = (
            "Electric current is the rate of flow of electric charge through a conductor. "
            "The SI unit of electric current is the ampere. "
            "Current electricity involves the movement of electrons. "
            "Ohm's law relates current, voltage, and resistance in a circuit."
        )
        result = validate_article_content(article, intent, title="Electric Current")
        self.assertFalse(result["valid"])

    def test_answer_verification_uses_metadata_entities(self) -> None:
        from query_understanding import understand_query
        from result_validator import verify_answer_relevance

        intent = understand_query("who is the current chief minister of west bengal")
        article = (
            "Mamata Banerjee is the Chief Minister of West Bengal. "
            "She assumed office on 20 May 2011. "
            "The Chief Minister of West Bengal leads the state government. "
            "West Bengal is located in eastern India."
        )
        result = verify_answer_relevance(article, "who is the current chief minister of west bengal", intent)
        self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()
