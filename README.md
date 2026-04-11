# Porcupine_AI_Assistant

## JARVIS Voice Assistant (Python, Windows)

A modular, voice-first desktop assistant for **Windows** that listens for a wake phrase, understands spoken commands, and executes actions like opening apps, web search, reminders, volume control, and calendar summaries.

## What it does

- Wake-word based interaction (`SpeechRecognition` + fuzzy matching).
- Command planning pipeline:
  1. System intent resolver (time/date/greetings/schedule)
  2. Rule-based intent parser
  3. Gemini fallback parser (structured action JSON)
- Action execution for:
  - Open/close apps
  - Web/search actions (Google/YouTube/Gmail/GitHub/Reddit)
  - Volume controls (up/down/mute/unmute)
  - Calendar summary for today
  - Reminder creation (`in X minutes/hours/seconds`)
  - System restart/shutdown (with confirmation)
- Text-to-speech responses (`pyttsx3`).
- Persistent memory (`memory.json`) for learned command patterns and app usage habits.
- Background scheduler for reminder triggers and proactive app suggestions.

## Tech stack

- Python 3.x
- Windows APIs and utilities (`pycaw`, `AppOpener`, `taskkill`, `shutdown`)
- Google Calendar API (`google-api-python-client`, OAuth credentials)
- Gemini API (optional parser fallback)

## Project structure

```text
main.py                # App entrypoint and runtime loop
listener.py            # Wake word + command speech recognition
preprocessor.py        # Text cleanup and normalization
system_intents.py      # Fast-path system intent rules
intent_parser.py       # Rule-based intent parser + plan objects
ai_parser.py           # Gemini-powered parser fallback
executor.py            # Executes parsed actions
scheduler.py           # Reminder polling + proactive suggestions
memory.py              # Persistent memory and pattern learning
tts.py                 # Text-to-speech engine wrapper
config.py              # Config loader and typed access helpers
modules/calendar_module.py  # Google Calendar integration
utils/logger.py        # Structured logging utilities
config.json            # Main runtime config
memory.json            # Persistent assistant memory (generated/updated)
reminders.json         # Reminder storage (generated/updated)
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Ensure your microphone and default Windows audio output are configured.
4. Configure `config.json` (wake phrase, app aliases/paths, scheduler settings, etc.).

## Configuration notes

- **Wake phrase**: `wake_phrase`
- **Speech tuning**: `speech` block (`wake_fuzzy_threshold`, timeouts, language)
- **TTS**: `tts` block (`rate`, `preferred_voice`)
- **App launch/close mapping**:
  - `app_aliases`
  - `paths.apps`
  - `process_names`
- **Data files**: `files.memory`, `files.reminders`
- **Scheduler**: `scheduler.poll_seconds`, `scheduler.idle_suggestion_seconds`

## Gemini (optional)

`ai_parser.py` uses Gemini as a fallback parser when system/rule parsing fails.

Configure either:
- `gemini.api_key` in `config.json`, or
- `GEMINI_API_KEY` environment variable

If no key is configured, the assistant still works using safe fallback behavior.

## Google Calendar setup (optional)

Calendar integration reads credentials from `credentials.google_calendar_api` in `config.json` (default: `credentials.json`).

1. Create OAuth desktop credentials in Google Cloud.
2. Save credentials JSON as `credentials.json` (or update config path).
3. On first use, complete OAuth login in browser.
4. `token.json` will be created for future sessions.

## Run

```bash
python main.py
```

You should see: `Assistant V3 is online. Press Ctrl+C to exit.`

## Example voice commands

- “open chrome”
- “close notepad”
- “search for python decorators”
- “open youtube for lo-fi music”
- “what time is it”
- “what date is it”
- “what is my schedule”
- “remind me to drink water in 20 minutes”
- “restart” (asks for confirmation)

## Logs and persistence

- Logs are written to `logs/jarvis.log` (rotating file handler).
- Interaction history and learned patterns are stored in `memory.json`.
- Reminders are stored in `reminders.json`.

## Notes

- This codebase currently includes both active runtime modules and some older module files under `core/` and `modules/`; the primary runtime path is driven by `main.py` and its direct imports.
