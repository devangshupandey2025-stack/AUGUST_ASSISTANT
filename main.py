from __future__ import annotations

from ai_parser import AIParser
from context_engine import ContextEngine
from executor import Executor
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
        self.context_engine = ContextEngine()
        self.scheduler = AssistantScheduler(self.memory, self.context_engine, speak)
        self.listener = Listener()
        self.preprocessor = Preprocessor()
        self.system_intents = SystemIntentResolver()
        self.intent_parser = IntentParser(memory_store=self.memory)
        self.ai_parser = AIParser()
        self.executor = Executor(reminder_handler=self.scheduler.add_reminder_from_command)
        self.pending_confirmation_plan: CommandPlan | None = None

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

        plan = self.system_intents.resolve(cleaned_text)
        if plan is not None:
            log_event(logger, "planner_stage", source="system", success=True, text=cleaned_text)
            return plan

        plan = self.intent_parser.parse(cleaned_text)
        if plan is not None:
            log_event(logger, "planner_stage", source="rule", success=True, text=cleaned_text)
            return plan

        logger.info("Falling back to Gemini for command: %s", cleaned_text)
        plan = self.ai_parser.parse(cleaned_text, context=self.context_engine.snapshot())
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
            self.memory.record_interaction(
                raw_text=f"confirmation: {self.pending_confirmation_plan.raw_text}",
                plan=self.pending_confirmation_plan,
                response_message=result.message,
                context=self.context_engine.snapshot(),
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
        print("Assistant V3 is online. Press Ctrl+C to exit.")
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
                self.context_engine.touch(normalized_for_state)
                if self._handle_confirmation(normalized_for_state):
                    continue

                plan = self._resolve_plan(user_command)
                if plan is None:
                    speak("I could not understand that command.")
                    continue

                if plan.requires_confirmation():
                    self.pending_confirmation_plan = plan
                    speak("This action could affect your system. Say yes to confirm or no to cancel.")
                    continue

                result = self.executor.execute_plan(plan)
                speak(result.message)
                self.memory.record_interaction(
                    raw_text=normalized_for_state,
                    plan=plan,
                    response_message=result.message,
                    context=self.context_engine.snapshot(),
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
    VoiceAssistant().run()
