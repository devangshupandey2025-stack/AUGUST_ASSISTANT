AUGUST --- Engineering Roadmap & Log-Derived Project Plan

Purpose: This document is the implementation plan for rebuilding
August into a reliable, extensible JARVIS-style assistant. It is based
on the supplied runtime logs, repository migration output, observed
failures, and the existing architecture visible in those logs.

Rule: Work phase-by-phase. Do not jump ahead to new "cool"
features while the foundation is unstable.

1. Current State --- What the Logs Prove

August is already a functioning assistant rather than a blank project.

Observed capabilities include:

Wake phrase detection using august.

Text command capture after wake detection.

Rule-based intent parsing.

DecisionEngine-based confidence and action selection.

Application discovery through AppRegistry.

Application launching.

Web search and web research.

Wikipedia/provider-based answering.

Weather handling.

Calendar startup briefing.

Scheduler/background suggestions.

TTS responses.

Context tracking.

Repeated-command learning.

Research caching.

KnowledgeGovernor-based research-memory decisions.

Source reliability scoring.

Query classification.

Document generation hooks.

Provider abstractions and provider tests.

A migration toward src/august/.

Representative logs show successful wake → command → planning → decision
→ execution → TTS flows for commands such as opening Chrome/Spotify/File
Explorer and searching the web.

The assistant has also demonstrated successful provider research and
research-memory storage for some queries.

2. Important Existing Architecture

The logs reveal these major components:

Listener
    ↓
IntentParser
    ↓
DecisionEngine
    ↓
Executor
    ↓
Action / Provider
    ↓
Context / Memory
    ↓
TTS

Supporting systems include:

AppRegistry
Scheduler
Calendar
KnowledgeGovernor
AnswerMemory
WebResearch
ResultFilter
ResultValidator
RetrievalConfidence
SearchSynthesizer
ProviderRouter
WeatherProvider
WikipediaProvider
PersonalityEngine
DocumentGenerator

The repository has already been substantially migrated toward:

src/august/

The migration touched roughly 76 files and added/modified thousands of
lines, including provider infrastructure and tests.

3. Evidence From the Logs

3.1 Core command pipeline works

A representative successful command:

Wake word detected
    ↓
Listening for user command
    ↓
Recognized command
    ↓
IntentParser matched rule
    ↓
DecisionEngine produced high confidence
    ↓
Executor executed action
    ↓
TTS responded
    ↓
Context updated

For example, open spotify has repeatedly reached successful open_app
execution with very high confidence.

This means the core loop should be preserved while refactoring, not
discarded blindly.

3.2 AppRegistry is working

The logs show AppRegistry scanning Windows Start Menu/program locations
and building roughly 1,300--1,400 application entries.

Exact application matching works with confidence 1.00 in observed
cases.

Examples include:

Chrome

Brave

Spotify

WhatsApp

File Explorer

Excel

Therefore AppRegistry should become a proper capability rather than
being replaced with ad-hoc application lookup.

3.3 The wake-word implementation is fragile

The listener has historically used Google speech recognition during
wake-word listening.

A concrete failure:

[WinError 10060]
A connection attempt failed because the connected party did not properly respond

The traceback shows the failure occurred inside:

speech_recognition.recognize_google(...)

This means the wake-word path is network-dependent and can fail before
the user even reaches command processing.

Required direction

Separate:

Wake-word detection

from:

Full command speech recognition

The wake-word detector should be local and lightweight.

4. Major Research-System Problems Found

4.1 Incorrect source retrieval

One of the clearest failures is:

User query:

what is the weather today in kolkata

The research system returned an article about Kairana, not Kolkata.

The system still generated a confident answer from the wrong location.

This is a serious correctness problem.

Required fix

Research must validate:

query entities
        ↓
retrieved source entities
        ↓
answer entities

If the source does not actually correspond to the requested
location/topic:

REJECT

Do not synthesize an answer from it.

4.2 Low-confidence research is already being blocked

KnowledgeGovernor correctly blocks some research from memory when
confidence is low.

Observed example:

confidence = 0.60
reason = low_confidence
research_memory_blocked

This behavior should be preserved.

4.3 Research memory can store verified results

For strong results, the system successfully records:

research_memory_stored
memory_category_assigned
source_reliability_applied

Example:

what is react js
confidence = 1.0
memory_type = static_knowledge

Therefore the current research-memory concept is useful, but its
boundaries need to be formalized.

4.4 Confidence decay exists

KnowledgeGovernor already applies memory confidence decay.

Examples in the logs show previously stored knowledge falling from a
base confidence around 0.88 to much lower effective confidence values.

This is a good concept.

However, confidence decay must be separated from:

source correctness

A stale but correct fact is different from a fact that was wrong when
stored.

5. Concrete Bugs Found in Logs

Bug A --- Wrong-source research

Example:

Requested: Kolkata weather
Returned: Kairana weather

Priority

CRITICAL

Fix

Implement source/entity validation before synthesis and before memory
storage.

Bug B --- Mock confidence type error

Observed:

TypeError:
'<' not supported between instances of 'Mock' and 'float'

It occurs when:

KnowledgeGovernor.should_store_research(...)

compares confidence with 0.8.

Root issue

Tests/mocks can return a Mock where production code expects a numeric
confidence.

Fix

Define a typed confidence contract:

confidence: float

Validate it at system boundaries.

Tests must return real numeric values unless the test specifically
targets invalid input.

Bug C --- DuckDuckGo transport failures

The logs show repeated:

web_transport_error
persistent failure
ddg_search_failed
DDGS unavailable

The system already attempts retries and an HTML fallback in some paths.

Required direction

Formalize provider fallback:

Primary search
    ↓ failure
Fallback transport
    ↓ failure
Alternative provider
    ↓ failure
Cached answer
    ↓ unavailable
Honest failure response

Never turn a failed search into an apparently confident answer.

Bug D --- Web search timeout/formatting error

Observed:

unsupported format string passed to datetime.timedelta.__format__

This caused a web-search failure before recovery.

Fix

Centralize duration/latency formatting.

Do not format timedelta using numeric format specifiers intended for
floats.

Bug E --- Proactive suggestion spam

The scheduler repeatedly says:

You often use spotify around this time.
Say open spotify if you want me to launch it.

The same suggestion appears repeatedly within minutes.

Priority

HIGH

Required behavior

Introduce:

suggestion cooldown
suggestion deduplication
daily/session limits
dismissal state
context awareness

A suggestion should not repeatedly interrupt the user.

Bug F --- Context can become semantically stale

The logs show:

Context updated from plan action='web_research' app='spotify'

The presence of a previous application in the same context record does
not necessarily mean Spotify is relevant to the research query.

Fix

Separate context domains:

ConversationContext
ApplicationContext
ResearchContext
TaskContext
UserContext

Do not let unrelated fields leak across domains.

6. Architecture Problem --- The System Is Growing Too Much Inside DecisionEngine

The logs show DecisionEngine handling:

intent decisions

query classification

web research selection

confidence

app resolution

action planning

answer routing

memory interaction

This is becoming a god object.

Target architecture

Input
  ↓
Conversation Manager
  ↓
Intent / Query Understanding
  ↓
Planner
  ↓
Confidence / Validation
  ↓
Action or Research Tool
  ↓
Result Validation
  ↓
Memory
  ↓
Response
  ↓
TTS

DecisionEngine should eventually become either:

a thin orchestration layer, or

be removed after its responsibilities are migrated.

Do not delete it in one shot.

7. Repository State

The logs show a substantial migration into:

src/august/

Existing migrated modules include:

decision_engine.py
document_generator.py
entity_guard.py
executor.py
followup_utils.py
garbage_detector.py
gui.py
intent_parser.py
jarvis_assistant.py
knowledge_governor.py
listener.py
memory.py
personality_engine.py
preprocessor.py
query_normalizer.py
query_understanding.py
result_filter.py
result_validator.py
retrieval_confidence.py
sanity_validator.py
scheduler.py
search_synthesizer.py
system_intents.py
tts.py
web_research.py

There is also provider infrastructure:

providers/
    base_provider.py
    provider_result.py
    provider_router.py
    utils.py
    weather_provider.py
    weather_service.py
    wikipedia_provider.py

Tests include substantial files for:

test_decision_engine.py
test_providers.py
test_weather_provider.py

This means the next stage should be consolidation and correctness,
not another blind migration.

8. Phase 0 --- Freeze and Baseline

Goal

Know exactly what currently works before refactoring.

Tasks

Create a clean Git branch.

Run the current assistant.

Record startup behavior.

Test wake word.

Test app launching.

Test web search.

Test research.

Test weather.

Test calendar.

Test TTS.

Test memory/context.

Test scheduler.

Record failures.

Gate

No architecture changes until the baseline is recorded.

9. Phase 1 --- Repository Cleanup

Goal

Create one canonical source tree.

Tasks

Complete src/august/ migration.

Remove duplicate legacy imports/files only after references are
migrated.

Remove runtime caches from source control.

Remove generated logs from Git.

Move credentials/secrets out of repository.

Add/update .gitignore.

Identify runtime state files.

Decide what belongs in persistent storage.

Important

The observed app_registry_cache.json contains machine-specific Windows
paths.

It should be treated as generated machine state, not canonical
application source.

10. Phase 2 --- Packaging and Test Foundation

Create:

pyproject.toml

Use:

src/august/
tests/

Test categories:

tests/
    unit/
    integration/
    evals/

Add:

ruff
pytest
mypy

and CI.

Gate

Every refactor must pass the baseline suite.

11. Phase 3 --- Audio Architecture

Target:

Microphone
    ↓
Local VAD
    ↓
Local wake-word detection
    ↓
Command capture
    ↓
STT

Create abstractions:

class WakeWordDetector:
    ...

class SpeechRecognizer:
    ...

class AudioCapture:
    ...

The command-processing pipeline should not know which STT engine is
being used.

12. Phase 4 --- Conversation Manager

Create:

conversation/
    manager.py
    state.py
    context.py
    turn.py

State:

IDLE
WAITING_FOR_WAKE
LISTENING
THINKING
EXECUTING
SPEAKING
ERROR

This replaces scattered state transitions.

13. Phase 5 --- Structured Understanding

Replace loosely coupled strings with typed objects.

Example:

@dataclass
class Transcript:
    text: str
    confidence: float
    source: str

@dataclass
class Intent:
    type: str
    entities: dict
    confidence: float

@dataclass
class ActionPlan:
    steps: list
    confidence: float
    source: str

Confidence must always be a real numeric value.

14. Phase 6 --- Planner

Convert:

user text

into:

structured plan

Example:

"open chrome and search for VIT placements"

→

1. open_app(chrome)
2. search_web("VIT placements")

The planner must not execute anything.

15. Phase 7 --- Tool Registry

Create a common tool interface:

class Tool:
    name: str
    description: str
    risk_level: str

    def validate(self, args):
        ...

    def execute(self, args):
        ...

Register capabilities such as:

open_app
close_app
search_web
web_research
weather
calendar
filesystem
document_generation
reminders
system_controls

16. Phase 8 --- Safety and Permissions

Risk levels:

LOW
MEDIUM
HIGH
CRITICAL

Examples:

weather          LOW
open_app         LOW
calendar_read    MEDIUM
calendar_write   MEDIUM
file_write       HIGH
file_delete      HIGH
shell            CRITICAL
shutdown         CRITICAL

High-risk actions require confirmation.

17. Phase 9 --- Research Pipeline

Build:

Query Understanding
       ↓
Query Classification
       ↓
Search
       ↓
Result Filtering
       ↓
Source Validation
       ↓
Entity Validation
       ↓
Evidence Extraction
       ↓
Answer Synthesis
       ↓
Confidence

A result must not be trusted merely because a search engine returned it.

18. Phase 10 --- Provider Architecture

Provider interface:

class Provider:
    name: str

    def supports(self, query):
        ...

    def execute(self, query):
        ...

Provider router:

Best provider
    ↓
fallback
    ↓
fallback
    ↓
failure

Provider results should have a standard structure:

ProviderResult(
    answer=...,
    sources=...,
    confidence=...,
    metadata=...
)

19. Phase 11 --- Research Memory

Keep the current KnowledgeGovernor concept, but formalize it.

Store only when:

confidence >= threshold
AND
answer is non-empty
AND
answer is not filler
AND
sources are acceptable
AND
query/source entities are consistent

Differentiate:

static knowledge
dynamic facts
user facts
conversation memory
procedural memory

Do not treat all memories equally.

20. Phase 12 --- Context

Split the current broad context into:

ConversationContext
ResearchContext
ApplicationContext
TaskContext
UserContext

Example:

ConversationContext:
    last_user_message
    last_assistant_message

ResearchContext:
    active_query
    sources
    claims
    confidence

ApplicationContext:
    current_app
    last_opened_app

TaskContext:
    active_task
    pending_confirmation

This prevents unrelated state contamination.

21. Phase 13 --- Scheduler

The scheduler already works, but proactive behavior needs guardrails.

Add:

cooldown
deduplication
priority
dismissal
max suggestions/session
max suggestions/day

Before speaking a proactive suggestion:

Is user busy?
Was this suggestion recently shown?
Was it dismissed?
Is it still relevant?
Is there a higher-priority task?

If any answer is unfavorable:

do not interrupt

22. Phase 14 --- Memory Storage

Move important persistent state away from ad-hoc JSON.

Use SQLite for:

conversations
facts
preferences
tasks
reminders
research memories
learned behaviors

Generated machine caches such as AppRegistry should remain separate.

23. Phase 15 --- TTS

Current synchronous TTS should become a queued service.

API:

tts.speak(text)
tts.stop()
tts.pause()
tts.resume()

Architecture:

Assistant
    ↓
TTS Queue
    ↓
TTS Worker
    ↓
Speaker

Add interruption/barge-in later.

24. Phase 16 --- Background Tasks

Create:

runtime/
    task_manager.py
    event_bus.py
    workers.py

Long-running work should not block the conversational loop.

Example:

research
download
document generation
calendar sync

can run asynchronously.

25. Phase 17 --- Personality

Personality must affect phrasing, not correctness.

Keep:

facts
confidence
tool results
permissions

independent from:

tone
word choice
verbosity
style

26. Phase 18 --- Proactive JARVIS Features

Only after reliability.

Possible features:

Morning briefing.

Calendar briefing.

Weather alerts.

Task reminders.

Habit-aware suggestions.

Context-aware app suggestions.

Long-running task completion notifications.

The existing Spotify suggestion proves the concept, but its spam
behavior must be fixed first.

27. Phase 19 --- Observability

Every turn should have:

conversation_id
turn_id
task_id

Track:

wake latency
STT latency
planning latency
provider latency
execution latency
TTS latency
total latency

Example:

Turn 382
 ├── STT: 1.2s
 ├── intent: 40ms
 ├── planning: 20ms
 ├── web: 680ms
 └── TTS: 1.8s

28. Phase 20 --- Evaluation Suite

Build a real benchmark.

Categories:

Wake word
STT
Intent
Entities
App control
Web research
Weather
Calendar
Reminders
Memory
Follow-ups
Ambiguity
Multi-step commands
Failures
Safety

Track:

intent accuracy
entity accuracy
task success
false execution rate
clarification rate
research correctness
source correctness
latency

Every major refactor must run the benchmark.

29. Phase 21 --- Failure Recovery

Every failure should follow:

Tool failure
    ↓
Retry?
    ├── yes → retry
    └── no
         ↓
Fallback?
    ├── yes → fallback
    └── no
         ↓
Explain honestly
         ↓
Offer next action

Never convert a failed lookup into a confident answer.

30. Target Architecture

Eventually:

AUGUST/
│
├── pyproject.toml
├── README.md
├── .gitignore
│
├── src/
│   └── august/
│       ├── app.py
│       │
│       ├── audio/
│       │   ├── capture.py
│       │   ├── vad.py
│       │   ├── wake_word.py
│       │   └── stt.py
│       │
│       ├── conversation/
│       │   ├── manager.py
│       │   ├── state.py
│       │   ├── context.py
│       │   └── turn.py
│       │
│       ├── intelligence/
│       │   ├── intent/
│       │   ├── planning/
│       │   ├── reasoning/
│       │   ├── confidence/
│       │   └── clarification/
│       │
│       ├── actions/
│       │   ├── registry.py
│       │   ├── app.py
│       │   ├── browser.py
│       │   ├── filesystem.py
│       │   ├── system.py
│       │   ├── calendar.py
│       │   └── reminders.py
│       │
│       ├── providers/
│       │   ├── router.py
│       │   ├── weather.py
│       │   ├── web.py
│       │   ├── wikipedia.py
│       │   └── calendar.py
│       │
│       ├── memory/
│       │   ├── database.py
│       │   ├── short_term.py
│       │   ├── episodic.py
│       │   ├── semantic.py
│       │   └── procedural.py
│       │
│       ├── security/
│       │   ├── permissions.py
│       │   ├── risk.py
│       │   └── confirmation.py
│       │
│       ├── speech/
│       │   ├── tts.py
│       │   └── queue.py
│       │
│       ├── runtime/
│       │   ├── task_manager.py
│       │   ├── scheduler.py
│       │   └── event_bus.py
│       │
│       ├── observability/
│       │   ├── metrics.py
│       │   └── tracing.py
│       │
│       └── personality/
│           └── engine.py
│
└── tests/
    ├── unit/
    ├── integration/
    └── evals/

31. Exact Execution Order

Follow this order:

PHASE 0  → Baseline
PHASE 1  → Repository cleanup
PHASE 2  → Packaging + tests + CI
PHASE 3  → Audio
PHASE 4  → Conversation manager
PHASE 5  → Structured understanding
PHASE 6  → Planner
PHASE 7  → Tool registry
PHASE 8  → Security
PHASE 9  → Research pipeline
PHASE 10 → Providers
PHASE 11 → Research memory
PHASE 12 → Context
PHASE 13 → Scheduler
PHASE 14 → Persistent memory
PHASE 15 → TTS
PHASE 16 → Background tasks
PHASE 17 → Personality
PHASE 18 → Proactive behavior
PHASE 19 → Observability
PHASE 20 → Evaluation
PHASE 21 → Failure recovery

32. First Milestone --- AUGUST FOUNDATION v1

Do not call the foundation complete until all of these are true:

One canonical source tree.

No duplicate legacy architecture.

Generated caches excluded from source control.

Machine-specific AppRegistry state separated from source.

Packaging works.

Unit tests work.

Integration tests exist.

CI is green.

Local wake-word architecture exists.

STT is abstracted.

Conversation state is explicit.

Intent is structured.

Entities are structured.

Confidence is typed.

Planner is separate from execution.

Tool registry exists.

Permissions exist.

Research validates source/topic consistency.

Provider fallback works.

Research memory has clear admission rules.

Context is separated by domain.

Scheduler cannot spam suggestions.

TTS is decoupled from the main pipeline.

Existing working commands still work.

33. Non-Negotiable Engineering Rules

Rule 1 --- Preserve working behavior

Do not rewrite working functionality without a test or baseline proving
equivalent behavior.

Rule 2 --- No giant rewrite

Do not replace the whole assistant in one pass.

Migrate responsibility-by-responsibility.

Rule 3 --- No execution from the planner

Planning determines what should happen.

Tools determine how it happens.

Rule 4 --- No unvalidated research

Search results are evidence, not truth.

Rule 5 --- No fake confidence

Confidence must reflect actual evidence.

Rule 6 --- No silent destructive actions

High-risk actions require confirmation.

Rule 7 --- No proactive spam

The assistant should help, not constantly interrupt.

Rule 8 --- Every phase has a gate

If the phase does not pass its tests, stop and fix it before continuing.

34. Immediate Next Step

Start with:

PHASE 0 — Baseline

Then:

PHASE 1 — Repository cleanup

Then:

PHASE 2 — Packaging + tests

Do not start implementing advanced JARVIS features yet.

The logs show that August already has a surprisingly broad feature set.
The main problem is no longer "add more features"; it is making the
existing capabilities correct, isolated, testable, recoverable, and
extensible.