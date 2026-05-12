from __future__ import annotations

from ai_parser import AIParser
from app_registry import AppRegistry
from context_engine import ContextEngine
from decision_engine import DecisionEngine
from executor import Executor
from followup_utils import is_follow_up_query
from garbage_detector import detect_garbage_input
from intent_parser import CommandPlan, IntentParser
from listener import Listener
from memory import MemoryStore
from modules.calendar_module import fetch_todays_events
from preprocessor import Preprocessor
from scheduler import AssistantScheduler
from system_intents import SystemIntentResolver
from tts import speak
from utils.logger import get_logger, log_event

logger = get_logger("Main")


class VoiceAssistant:
    def __init__(self) -> None:
        self.memory = MemoryStore()
        self.app_registry = AppRegistry()
        self.app_registry.build_registry()
        self.context_engine = ContextEngine()
        self.scheduler = AssistantScheduler(self.memory, self.context_engine, speak)
        self.listener = Listener()
        self.preprocessor = Preprocessor()
        self.system_intents = SystemIntentResolver()
        self.intent_parser = IntentParser(memory_store=self.memory)
        self.ai_parser = AIParser()
        self.decision_engine = DecisionEngine(ai_parser=self.ai_parser, memory_store=self.memory, app_registry=self.app_registry)
        self.executor = Executor(
            reminder_handler=self.scheduler.add_reminder_from_command,
            app_registry=self.app_registry,
            ai_parser=self.ai_parser,
            memory_store=self.memory,
            context_provider=self.context_engine.get_context,
        )
        self.pending_confirmation_plan: CommandPlan | None = None

    def _is_garbage_input(self, text: str) -> bool:
        normalized = self.preprocessor.clean(text) or ""
        if not normalized:
            return True
        if is_follow_up_query(normalized):
            return False
        has_known_intent = any(
            (
                normalized.startswith(("open ", "close ", "launch ", "start ", "run ", "search ", "find ", "look up ", "watch ", "play ", "set ", "remind ")),
                normalized.startswith(("what ", "who ", "why ", "how ", "which ", "define ", "explain ", "tell me about", "can you explain")),
                normalized in {"open", "close", "search", "play", "watch", "run", "start", "launch", "mute", "unmute", "shutdown", "restart"},
            )
        )
        verdict = detect_garbage_input(normalized, has_known_intent=has_known_intent)
        return bool(verdict.get("is_garbage"))

    def _run_startup_routine(self) -> None:
        logger.info("Running startup routine")
        user_name = self.memory.get_user_name()
        speak(self.context_engine.build_adaptive_greeting(user_name))

        try:
            schedule = fetch_todays_events()
            if schedule:
                speak(f"Here is your schedule for today. {schedule}")
        except Exception as exc:
            logger.exception("Startup calendar fetch failed: %s", exc)
            speak("I could not retrieve your calendar details right now.")

        suggestions = self.memory.suggest_frequent_apps(self.context_engine.get_time_of_day())
        if suggestions:
            speak(f"You often use {suggestions[0]} around this time.")

    def _resolve_plan(self, user_command: str) -> CommandPlan | None:
        cleaned_text = self.preprocessor.clean(user_command)
        if not cleaned_text:
            return None
        if is_follow_up_query(cleaned_text):
            return None
        if self._is_garbage_input(cleaned_text):
            return None

        plan = self.system_intents.resolve(cleaned_text)
        if plan is not None:
            log_event(logger, "planner_stage", source="system", success=True, text=cleaned_text)
            return plan

        plan = self.intent_parser.parse(cleaned_text)
        if plan is not None:
            log_event(logger, "planner_stage", source="rule", success=True, text=cleaned_text)
            return plan

        logger.info("Falling back to Gemini for command: %s", cleaned_text)
        plan = self.ai_parser.parse(cleaned_text, context=self.context_engine.get_context())
        if plan is not None:
            log_event(logger, "planner_stage", source=plan.source, success=True, text=cleaned_text)
        return plan

    def _handle_confirmation(self, user_command: str) -> bool:
        if not self.pending_confirmation_plan:
            return False

        normalized = user_command.strip().lower()
        if normalized in {"yes", "yes please", "confirm", "do it", "okay", "ok"}:
            result = self.executor.execute_plan(self.pending_confirmation_plan)
            speak(result.message)
            self.context_engine.update_context(plan=self.pending_confirmation_plan)
            self.memory.record_interaction(
                raw_text=f"confirmation: {self.pending_confirmation_plan.raw_text}",
                plan=self.pending_confirmation_plan,
                response_message=result.message,
                context=self.context_engine.get_context(),
            )
            self.pending_confirmation_plan = None
            return True

        if normalized in {"no", "cancel", "stop", "don't", "do not"}:
            speak("Cancelled.")
            self.pending_confirmation_plan = None
            return True

        speak("Please say yes to confirm or no to cancel.")
        return True

    def run(self) -> None:
        logger.info("Assistant is online")
        print("Assistant V4 is online. Press Ctrl+C to exit.")
        self.scheduler.start()

        try:
            self._run_startup_routine()
        except Exception as exc:
            logger.exception("Startup routine failed: %s", exc)
            speak("I ran into an issue during startup.")

        while True:
            try:
                if not self.listener.listen_for_wake_word():
                    continue

                speak("Yes?")
                user_command = self.listener.listen_for_command()
                if not user_command:
                    speak("I did not catch that.")
                    continue

                normalized_for_state = self.preprocessor.clean(user_command) or user_command
                is_follow_up = is_follow_up_query(normalized_for_state)
                if is_follow_up:
                    log_event(logger, "followup_detected", source="main", success=True, query=normalized_for_state)

                current_context = self.context_engine.get_context()
                if self._handle_confirmation(normalized_for_state):
                    continue

                has_pending_interaction = bool(current_context.get("pending_interaction"))
                if not is_follow_up and not has_pending_interaction and self._is_garbage_input(normalized_for_state):
                    garbage_response = "That doesn't look like a valid command. Can you rephrase?"
                    log_event(logger, "garbage_detected", source="main", success=True, query=normalized_for_state)
                    speak(garbage_response)
                    self.context_engine.update_context(
                        command_text=normalized_for_state,
                        last_action="garbage_detected",
                        response_text=garbage_response,
                        skip_query_update=True,
                    )
                    self.memory.record_interaction(
                        raw_text=normalized_for_state,
                        plan=None,
                        response_message=garbage_response,
                        context=self.context_engine.get_context(),
                    )
                    continue

                self.context_engine.update_context(command_text=normalized_for_state, skip_query_update=is_follow_up)
                current_context = self.context_engine.get_context()
                parsed_plan = None if is_follow_up or has_pending_interaction else self._resolve_plan(user_command)
                decision = self.decision_engine.decide(
                    raw_text=normalized_for_state,
                    parsed_plan=parsed_plan,
                    context=current_context,
                    memory=self.memory.snapshot(),
                )

                if decision.mode == "answer":
                    speak(decision.response)
                    pending_interaction = self.decision_engine.get_pending_interaction() or {}
                    self.context_engine.update_context(
                        last_action="answer_query",
                        response_text=decision.response,
                        pending_interaction=pending_interaction,
                    )
                    self.memory.record_interaction(
                        raw_text=normalized_for_state,
                        plan=decision.plan,
                        response_message=decision.response,
                        context=self.context_engine.get_context(),
                    )
                    continue

                plan = decision.plan
                if plan is None:
                    speak("I could not understand that command.")
                    continue

                if plan.requires_confirmation():
                    self.pending_confirmation_plan = plan
                    speak("This action could affect your system. Say yes to confirm or no to cancel.")
                    continue

                result = self.executor.execute_plan(plan)
                speak(result.message)
                self.context_engine.update_context(plan=plan)
                self.memory.record_interaction(
                    raw_text=normalized_for_state,
                    plan=plan,
                    response_message=result.message,
                    context=self.context_engine.get_context(),
                )
            except KeyboardInterrupt:
                logger.info("Assistant interrupted by user")
                self.scheduler.stop()
                speak("Powering down.")
                break
            except Exception as exc:
                logger.exception("Unhandled main loop error: %s", exc)
                speak("I ran into an unexpected error.")


if __name__ == "__main__":
    import sys
    from gui import JarvisGUI
    import tts

    assistant = VoiceAssistant()

    def on_stop():
        sys.exit(0)

    app = JarvisGUI(assistant_runner=assistant.run, stop_callback=on_stop)

    # Monkey patch TTS engine to intercept all speech for UI updates
    original_engine_speak = tts.tts_engine.speak
    def gui_engine_speak(text):
        app.update_state("speaking")
        app.append_log(f"Jarvis: {text}\n\n")
        original_engine_speak(text)
        app.update_state("idle")
    tts.tts_engine.speak = gui_engine_speak

    # Monkey patch Listener to capture wake word and command states
    original_listen_wake = assistant.listener.listen_for_wake_word
    def gui_listen_wake(*args, **kwargs):
        app.update_state("idle")
        return original_listen_wake(*args, **kwargs)
    assistant.listener.listen_for_wake_word = gui_listen_wake

    original_listen_command = assistant.listener.listen_for_command
    def gui_listen_command(*args, **kwargs):
        app.update_state("listening")
        cmd = original_listen_command(*args, **kwargs)
        if cmd:
            app.append_log(f"You: {cmd}\n")
        app.update_state("thinking")
        return cmd
    assistant.listener.listen_for_command = gui_listen_command

    app.mainloop()
