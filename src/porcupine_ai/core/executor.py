from core.intent_parser import Intent
from utils.logger import get_logger
from core.tts import speak
from modules import app_control
from modules import system_controls
from modules import web_actions
from modules import file_ops
from modules.calendar_module import fetch_todays_events, create_calendar_event
from modules.reminders import reminder_system

logger = get_logger("Executor")

class Executor:
    def __init__(self):
        logger.info("Executor initialized.")
        
    def execute(self, intent_type, entity):
        logger.info(f"Executing intent: {intent_type} with entity: {entity}")
        
        if intent_type == Intent.OPEN_APP:
            app_control.open_app(entity)
        elif intent_type == Intent.CLOSE_APP:
            app_control.close_app(entity)
        elif intent_type == Intent.SYSTEM_CONTROL:
            system_controls.handle_system_command(entity)
        elif intent_type == Intent.WEB_ACTION:
            web_actions.search_web(entity)
        elif intent_type == Intent.FILE_OP:
            file_ops.open_folder(entity)
        elif intent_type == Intent.CALENDAR:
            schedule = fetch_todays_events()
            speak(schedule)
        elif intent_type == Intent.CREATE_EVENT:
            result = create_calendar_event(entity)
            speak(result)
        elif intent_type == Intent.REMINDER:
            reminder_system.set_reminder_from_text(entity)
        elif intent_type == Intent.UNKNOWN:
            speak("I'm sorry, I didn't quite catch that. Could you repeat?")
        else:
            logger.warning(f"Unhandled intent parsing: {intent_type}")