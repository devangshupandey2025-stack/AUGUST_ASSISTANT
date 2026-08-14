import re

from august.utils.logger import get_logger

logger = get_logger("IntentParser")

class Intent:
    OPEN_APP = "OPEN_APP"
    CLOSE_APP = "CLOSE_APP"
    SYSTEM_CONTROL = "SYSTEM_CONTROL"
    WEB_ACTION = "WEB_ACTION"
    FILE_OP = "FILE_OP"
    REMINDER = "REMINDER"
    CALENDAR = "CALENDAR"
    CREATE_EVENT = "CREATE_EVENT"
    UNKNOWN = "UNKNOWN"

class IntentParser:
    def __init__(self):
        # Order matters: specific commands before generic ones.
        self.patterns = [
            (r"^(search|google)\s+(for\s+)?(.+)$", Intent.WEB_ACTION),
            (r"^youtube(\s+for)?\s*(.*)$", Intent.WEB_ACTION),
            (r"^open\s+(youtube|google)(\s+for\s+(.+))?$", Intent.WEB_ACTION),
            (r"^(open folder|open file|show me)\s+(.+)$", Intent.FILE_OP),
            (r"(close|exit|terminate|kill|quit)\s+(the\s+)?(.+)$", Intent.CLOSE_APP),
            (r"(sleep|shutdown|restart|turn off|volume|brightness)\b.*", Intent.SYSTEM_CONTROL),
            (r"^(schedule|create|add)\s+(a\s+|an\s+)?(.+?)\s+at\s+(.+)$", Intent.CREATE_EVENT),
            (r"(remind me|set a reminder)\b.*", Intent.REMINDER),
            (r"(what is my schedule|whats my schedule|what's my schedule|calendar|events)\b.*", Intent.CALENDAR),
            (r"(open|launch|start|run)\s+(the\s+)?(.+)$", Intent.OPEN_APP)
        ]

    def parse(self, text):
        if not text:
            return Intent.UNKNOWN, None
            
        text = text.lower()
        
        for pattern_str, intent_type in self.patterns:
            match = re.search(pattern_str, text)
            if match:
                logger.info(f"Intent matched: {intent_type} for text: '{text}'")
                entity = None
                
                if intent_type in [Intent.OPEN_APP, Intent.CLOSE_APP]:
                    entity = match.group(3).strip()
                    entity = re.sub(r'\s+(app|application)$', '', entity)
                elif intent_type == Intent.WEB_ACTION:
                    if text.startswith("open youtube"):
                        query = re.sub(r"^open\s+youtube(\s+for\s+)?", "", text).strip()
                        entity = {'platform': 'youtube', 'query': query}
                    elif text.startswith("open google"):
                        query = re.sub(r"^open\s+google(\s+for\s+)?", "", text).strip()
                        entity = {'platform': 'google', 'query': query}
                    elif text.startswith("youtube"):
                        query = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
                        entity = {'platform': 'youtube', 'query': (query or "").strip()}
                    elif 'youtube' in text:
                        query = match.group(match.lastindex) if match.lastindex else ""
                        entity = {'platform': 'youtube', 'query': (query or "").strip()}
                    else:
                        entity = {'platform': 'google', 'query': match.groups()[-1] if match.groups() else ''}
                elif intent_type == Intent.FILE_OP:
                    entity = match.groups()[-1].strip()
                elif intent_type == Intent.SYSTEM_CONTROL:
                    sub_action = match.group(1).strip()
                    if 'volume' in text:
                        if 'up' in text or 'increase' in text:
                            param = "up"
                        elif 'down' in text or 'decrease' in text:
                            param = "down"
                        else:
                            param = "mute"
                        entity = {'action': 'volume', 'param': param}
                    else:
                        entity = {'action': sub_action}
                elif intent_type == Intent.CREATE_EVENT:
                    title = match.group(3).strip()
                    title = re.sub(r"^(a|an)\s+", "", title)
                    time_text = match.group(4).strip()
                    entity = {"title": title, "time_text": time_text}
                else:
                    entity = text
                
                return intent_type, entity
                
        logger.warning(f"Could not parse intent for text: '{text}'")
        return Intent.UNKNOWN, text
