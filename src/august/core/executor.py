from august.core.intent_parser import Intent
from august.core.tts import speak
from august.modules import app_control, file_ops, system_controls, web_actions
from august.modules.calendar_module import create_calendar_event, fetch_todays_events
from august.modules.reminders import reminder_system
from august.utils.logger import get_logger

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