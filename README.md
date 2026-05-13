# Porcupine_AI_Assistant

## JARVIS Voice Assistant (Python, Windows)

A modular, voice-first desktop assistant for **Windows** that listens for a wake phrase, understands spoken commands, and executes actions like opening apps, web search, reminders, volume control, and calendar summaries.

## What it does

- Wake-word based interaction (`SpeechRecognition` + fuzzy matching).
- Command planning pipeline:
  1. System intent resolver (time/date/greetings/schedule)
  2. Rule-based intent parser
  3. Gemini fallback parser (structured action JSON)
  4. Decision engine that validates/corrects plans, resolves context-aware commands (like "open it"/"close it"), and routes informational questions to answer mode.
- Version 4.3 conversation management:
  - Unified context model (`last_query`, `last_answer`, `last_action`, `last_app`, `pending_interaction`, `conversation_history`, `timestamp`)
  - Pending interaction lock for multi-turn clarification/answer-vs-search flows
  - Follow-up continuity for prompts like "give example", "explain more", and short replies
  - Pending interaction timeout handling (20 seconds) and smart reset behavior
- Version 4.3.1 interaction fixes:
  - Interaction-first resolution at the start of decision handling
  - Runtime follow-up handling for `search it` / `search` using `last_query`
  - Pending interaction resolution for `yes`, `no`, `answer`, `search`, `tell me`, and `explain`
  - Repetition cleanup for inputs like `what what is polymorphism`
  - Guardrails to avoid corrupting `last_query` with short control replies
- Version 4.4 personality layer:
  - Calm, concise, slightly witty speaking style via `personality_engine.py`
  - Response variation for acknowledgements and search confirmations
  - Micro-responses for common action confirmations
  - Humanized answer phrasing and optional light follow-up hooks
  - TTS chunking with short pauses for more natural delivery
- Version 4.4.1 personality refinements:
  - Casual handling for `how are you`, `how r u`, and `what's up` with friendly direct replies
  - Response-type mapping (`factual`, `conceptual`, `action`, `casual`, `schedule`, `failure`) for tone consistency
  - Hooks restricted to conceptual explanations only
  - Startup greeting rotation and schedule phrasing cleanup for natural delivery
  - Reduced template overuse to avoid repetitive phrasing
- Version 4.4.2 quality and safety refinements:
  - Mixed-intent casual detection now prioritizes casual responses (example: `how are you automatic trading`)
  - Answer-memory safety filters block weak/casual/fallback-style entries from being stored
  - Weak confirmations like `yes` now resolve safely: fallback prompts map to search, unrelated `yes` is ignored cleanly
  - Wake phrase text normalization hardens command cleanup by rejecting noisy wake-phrase-injected inputs
  - Unclear-input responses are normalized to a single clean fallback line
  - Schedule phrasing now uses singular/plural grammar correctly (for example, `You've got 1 event today: ...`)
- Version 4.4.3 context-priority fixes:
  - `last_query` now updates via priority rules (blocks trivial/casual inputs and accepts meaningful queries)
  - Forced `last_query` capture for valid informational question classes (comparison, conceptual, algorithmic, factual)
  - Fallback search-offer prompts now persist the active query so follow-ups resolve correctly
  - `search it` now always resolves from the latest valid contextual query
  - Added explicit logging when `last_query` changes for easier runtime tracing
- Version 4.5.1 HUD overlay UI update:
  - Tkinter interface is now frameless and always-on-top for a floating desktop HUD
  - Window is compact (`500x500`) and anchored to the bottom-right corner
  - Windows color-key transparency is enabled so the black background is invisible
  - Background framework grid is removed to keep only the animated central HUD visual
- Version 4.5.2 compact overlay refinement:
  - HUD window is reduced to `320x320` and pinned tighter to the bottom-right corner
  - Core HUD drawing now scales with window size (`radius = min(w, h) * 0.25`) for clarity at small dimensions
  - Visual density is reduced by removing side panels/waveform clutter and keeping a minimal circular core with rotating segments
  - Stroke styling is tightened for a cleaner, non-intrusive overlay appearance
- Version 4.6 precision and transport reliability patch:
  - Web research transport failures now fail safely with graceful fallback handling and recovery logs
  - Query classification was refined so static knowledge, reasoning, dynamic facts, and conversational input route more accurately
  - Knowledge context is preserved for valid knowledge queries even when research/summarization fails
  - Added lightweight sanity validation to block malformed factual queries from polluting long-term knowledge memory
  - Conversational prompts are isolated from permanent knowledge storage
- Action execution for:
  - Open/close apps
  - Web/search actions (Google/YouTube/Gmail/GitHub/Reddit)
  - Volume controls (up/down/mute/unmute)
  - Calendar summary for today
- Reminder creation (`in X minutes/hours/seconds`)
- System restart/shutdown (with confirmation)
- Informational Q&A responses for prompts like "what is..." and "difference between..."
  - Uses a robust local knowledge engine and answer pipeline for reliability:
    1. Answer memory retrieval
    2. Local fallback knowledge/templates (classification + confidence-gated)
    3. Gemini AI answer attempt
    4. Search prompt: "I'm not getting a clean answer. Want me to search it?"
  - Uses smart answer memory (`answer_memory.py`) with normalized similarity matching, confidence gating, and expiry.
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
answer_fallback.py     # Local knowledge engine with query classification and templates
answer_memory.py       # Safe answer memory store/retrieval with similarity scoring
personality_engine.py  # Personality profile, response variation, and humanized phrasing
decision_engine.py     # Plan correction/validation + answer-vs-action routing
context_engine.py      # Session context state (recent commands, last app/action, time slot)
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

You should see: `Assistant V4 is online. Press Ctrl+C to exit.`

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
  - Answer pipeline events include: `ai_success`, `ai_failure`, `local_success`, `local_match`, `local_generated`, `local_failure`, `fallback_triggered`.
  - Answer memory events include: `memory_hit`, `memory_miss`, `memory_store`.
- Interaction history and learned patterns are stored in `memory.json`.
- Reminders are stored in `reminders.json`.

## Tests

Run the decision/context engine tests:

```bash
python -m unittest tests.test_decision_engine
```

## Notes

- This codebase currently includes both active runtime modules and some older module files under `core/` and `modules/`; the primary runtime path is driven by `main.py` and its direct imports.
