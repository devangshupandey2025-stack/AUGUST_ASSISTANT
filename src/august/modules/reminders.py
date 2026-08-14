import json
import os
import threading
import time

from august.config import config
from august.core.tts import speak
from august.utils.logger import get_logger

logger = get_logger("Reminders")

class ReminderSystem:
    def __init__(self):
        self.reminders_file = config.get("files", {}).get("reminders", "reminders.json")
        self.reminders = self._load_reminders()
        self.running = True
        self.thread = threading.Thread(target=self._check_reminders_loop, daemon=True)
        self.thread.start()
        logger.info("Reminder system initialized and running.")
        
    def _load_reminders(self):
        if not os.path.exists(self.reminders_file):
            return []
        try:
            with open(self.reminders_file, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
            
    def _save_reminders(self):
        with open(self.reminders_file, 'w') as f:
            json.dump(self.reminders, f, indent=4)
            
    def add_reminder(self, task, timestamp):
        # timestamp as epoch
        self.reminders.append({
            "task": task,
            "time": timestamp,
            "triggered": False
        })
        self._save_reminders()
        logger.info(f"Reminder added: {task} at {timestamp}")
        
    def set_reminder_from_text(self, text):
        """Very basic NLP for extracting time 'in x minutes/hours'"""
        import re
        speak("I will try to set that reminder.")
        match = re.search(r"in\s+(\d+)\s+(minute|minutes|hour|hours|second|seconds)", text)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            seconds = value
            if 'minute' in unit: 
                seconds = value * 60
            elif 'hour' in unit: 
                seconds = value * 3600
            
            task = text.replace("remind me to", "").replace("set a reminder to", "").strip()
            task = re.sub(r"in\s+(\d+)\s+(minute|minutes|hour|hours|second|seconds)", "", task).strip()
            if not task:
                task = "Task"
                
            self.add_reminder(task, time.time() + seconds)
            speak(f"Reminder set for {value} {unit} from now.")
        else:
            speak("I didn't catch the time for the reminder. Please use the format 'in 10 minutes'.")
            
    def _check_reminders_loop(self):
        while self.running:
            now = time.time()
            changed = False
            for rem in self.reminders:
                if not rem['triggered'] and now >= rem['time']:
                    # Trigger the reminder
                    logger.info(f"Triggering reminder: {rem['task']}")
                    speak(f"Reminder: {rem['task']}") # Optional sound before this
                    rem['triggered'] = True
                    changed = True
                    
            if changed:
                self._save_reminders()
            time.sleep(10)

reminder_system = ReminderSystem()
