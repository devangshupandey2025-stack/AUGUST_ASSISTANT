---
slug: jarvis-structural-improvements
status: awaiting-approval
intent: unclear
review_required: true
plan_path: .omo/plans/jarvis-structural-improvements.md
plan_sha256: null
review_round_id: null
pending-action: write and review .omo/plans/jarvis-structural-improvements.md
review:
  momus:
    status: pending
    workspace_root: null
    runtime_home: null
    target: .omo/plans/jarvis-structural-improvements.md
    round_id: null
    plan_sha256: null
    launch_id: null
    session: null
    result: null
  independent:
    status: pending
    workspace_root: null
    runtime_home: null
    target: .omo/plans/jarvis-structural-improvements.md
    round_id: null
    plan_sha256: null
    launch_id: null
    session: null
    result: null
approach: Six wave-based structural refactors — legacy deletion, monolith splitting, duplication consolidation, packaging/tooling, data & git hygiene, GUI decoupling — each locked by the existing unittest suite plus new targeted tests, executed in dependency order with a baseline verification wave first.
---

# Draft: jarvis-structural-improvements

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

- C1 legacy-removal | Delete dead generations (core/, legacy modules/*, jarvis_assistant.py, jarvis2.0.py, GUI_SUGGESTIONS/, test.py); live code keeps working | active | grep import topology: only core/executor.py imports modules/* (core/executor.py:4-9); nothing imports core.executor/core.listener; root listener.py:1-79 vs core/listener.py:1-63 duplicates; main.py:1-272 never touches core/
- C2 monolith-split | Split decision_engine.py (1666 lines), web_research.py (1170), ai_parser.py (726), query_understanding.py (582) into cohesive modules with unchanged public behavior | active | decision_engine.py:1391,1657 function-level imports (weather_service, entity_guard) show accreted growth; tests/test_decision_engine.py:1-1130, tests/test_web_research_stability.py:1-463, tests/test_retrieval_intelligence.py:1-301 are behavior locks
- C3 dedupe-consolidation | One weather implementation (providers/weather_service.py wins), MemoryStore exposes a proper conversation API, normalization helpers merged | active | weather_service.py (root, 182 lines, get_weather at decision_engine.py:1391) vs providers/weather_service.py (279 lines, WeatherService class, used by providers/weather_provider.py:6); conversation_memory.py:43-77 reaches into memory_store._data/_write/_lock (private internals of memory.py:19-27)
- C4 packaging-tooling | pyproject.toml (pytest+ruff), requirements split into runtime+dev, consistent package dirs, providers/utils.py renamed to kill utils shadowing, CWD-independent config paths | active | no pyproject.toml/setup.py at root (root listing); providers/__init__.py:1-16 exists but core/, modules/, utils/ have none; providers/utils.py:1-77 shadows top-level utils/ package; config.py:12 CONFIG_FILE = Path("config.json") is CWD-relative
- C5 data-git-hygiene | Runtime state (memory.json, reminders.json, app_registry_cache.json) moves under data/; .gitignore covers .pytest_cache/, app_registry_cache.json, vosk-model-small-en-us-0.15/, *.docx; stale REBASE_HEAD checked | active | .gitignore:1-28 lacks .pytest_cache/, app_registry_cache.json (1460-line machine-specific cache at root), vosk-model-small-en-us-0.15/ (~40MB download), D_Link_Net.docx/Rbc.docx/Fc_And_8_Segnet_Unit_D_Link_Net.docx; .git/REBASE_HEAD exists (8cc02b56) though git log shows rebase finished (logs/HEAD:4); .git/info/exclude:7 excludes .codegraph locally only
- C6 gui-decoupling | main.py __main__ monkey-patching of tts_engine.speak and Listener methods replaced by injected callbacks | active | main.py:246-270 monkey-patches tts.tts_engine.speak and assistant.listener.listen_for_* at runtime to bridge the GUI; gui.py:12-60 takes assistant_runner/stop_callback but not event hooks

## Open assumptions (announced defaults)
<!-- Intent is UNCLEAR: research resolves ambiguity, defaults are adopted (not asked), and each is surfaced in the plan's human TL;DR for veto. -->
<!-- assumption | adopted default | rationale | reversible? -->

- A1 package-layout | Keep flat root-level imports; do NOT move to src-layout or a jarvis/ package. The user runs from the project root (main.py), tests import from root (tests/test_decision_engine.py:12-21), and a package move would churn every import for marginal benefit at this scale | reversible (can adopt later)
- A2 tooling config | Add pyproject.toml carrying [tool.pytest.ini_options] + [tool.ruff]; split requirements.txt (runtime, current pins) and requirements-dev.txt (pytest, ruff). No other tooling added | reversible
- A3 weather direction | providers/weather_service.py is the single canonical weather implementation (provider-pattern, tested by tests/test_weather_provider.py:10-14); root weather_service.py is deleted after decision_engine.py:1391 is migrated to the canonical API via a thin get_weather adapter | reversible (git history)
- A4 memory layering | Add public MemoryStore methods (e.g. get_conversation_state/update_conversation_state) and re-point conversation_memory.py:43-77 to them; stop private-field access (_data/_write/_lock) | reversible
- A5 data location | Runtime JSON (memory.json, reminders.json, app_registry_cache.json) moves to data/ created on demand; config.py:114-119 files defaults updated; migration copies existing files | reversible
- A6 legacy deletion | Dead code is deleted via git rm (recoverable from git history). Nothing in the live graph imports it (verified: only core/executor.py references modules/*) | reversible (git history)
- A7 providers/utils rename | providers/utils.py -> providers/text_utils.py to remove shadowing of the top-level utils package; all 4 importers updated (providers/weather_provider.py:7) | reversible
- A8 split discipline | Monolith splits keep every public class/function name and signature identical (callers and tests unchanged); imports are updated, no shims left behind; each split lands with its behavior-lock tests green | reversible
- A9 GUI decoupling | Listener/TTS constructors accept optional callbacks (on_state_change, on_user_text, on_assistant_text); main.py wires them instead of monkey-patching; defaults preserve current behavior when callbacks are None | reversible

## Findings (cited - path:lines)

1. Live entry point is main.py (V4 pipeline): main.py:1-272 builds VoiceAssistant with MemoryStore/AppRegistry/ContextEngine/Scheduler/Listener/Preprocessor/IntentParser/AIParser/DecisionEngine/Executor, GUI wiring in __main__ (main.py:234-271). gui.py:1-312 is the current Tkinter UI.
2. Two dead legacy generations exist: (a) core/ package - core/executor.py:1-41, core/intent_parser.py, core/listener.py:1-63, core/tts.py:1-33 with an Intent-enum style that nothing in the live graph imports; (b) jarvis_assistant.py:1-36 (truncated "Part 1/5" stub), jarvis2.0.py:1-197 (Whisper prototype). GUI_SUGGESTIONS/gui.py:1-222 + GUI_SUGGESTIONS/main.py:1-41 is an older GUI variant. test.py:1-3 is a scratch file.
3. modules/ is mixed: calendar_module.py is LIVE (main.py:13, executor.py:20, tests/test_calendar_module.py:6); app_control.py:1-35, system_controls.py:1-53, web_actions.py:1-35, file_ops.py:1-33, reminders.py:1-82 import core.tts (legacy TTS) and are imported only by core/executor.py:4-9 (legacy executor).
4. Monoliths (line counts via grep ^ count, capped at 500; exact tails read): decision_engine.py=1666 (read offset 1690 out of range -> 1666 lines), web_research.py=1170 (read: total 1170), ai_parser.py=726 (read: total 726), query_understanding.py=582 (read: total 582). Function-level imports in decision_engine.py:1391,1657 confirm accreted structure. web_research.py:1-1170 mixes search retry (769-785), source scoring (790-843), article extraction (848-908), summarization (929-1047), confidence (1052-1087), cache (1116-1153).
5. Duplication: root weather_service.py:1-182 (get_weather, used at decision_engine.py:1391) vs providers/weather_service.py:1-279 (WeatherService class, used at providers/weather_provider.py:6 and re-exported providers/__init__.py:5). conversation_memory.py:43-77 bypasses MemoryStore's API using private _data/_write/_lock (memory.py:19-27). Normalization triplicates: preprocessor.py:1-94, query_normalizer.py:1-151, followup_utils.py:1-31 all normalize text (all live).
6. No packaging: no pyproject.toml/setup.py/pytest.ini at root (root directory listing). providers/ has __init__.py:1-16; core/, modules/, utils/ are implicit namespace packages. providers/utils.py:1-77 shadows the top-level utils package (utils/logger.py only, utils/:1-2).
7. requirements.txt:1-20 pins runtime deps only; no dev/test split; legacy files reference deps NOT in requirements (whisper, sounddevice, numpy, pyautogui, keyboard, google.generativeai at jarvis2.0.py:4-8, jarvis_assistant.py:13-18) confirming they cannot even run.
8. Config: config.py:1-145 central Config class with defaults; CONFIG_FILE = Path("config.json") CWD-relative (config.py:12); config.json, .env, credentials.json, token.json, memory.json, reminders.json all gitignored (.gitignore:14-23); app_registry_cache.json:1-1460 (machine-specific app paths) is NOT gitignored; .pytest_cache/ and vosk-model-small-en-us-0.15/ NOT gitignored (.gitignore:1-28); *.docx files at root not ignored.
9. Tests: tests/ has 6 unittest-style files: test_decision_engine.py=1130, test_weather_provider.py>=500, test_providers.py>=500, test_web_research_stability.py=463, test_retrieval_intelligence.py=301, test_calendar_module.py=63. They mock AI/registry (DummyAIParser/FakeAppRegistry at test_decision_engine.py:24-60) and use tempdirs (test_decision_engine.py:63-68). No tests for memory.py, scheduler.py, context_engine.py, intent_parser.py, app_registry.py, listener.py, tts.py, gui.py.
10. Git: single branch main; remote github.com/devangshupandey2025-stack/Porcupine_AI_Assistant.git (.git/config:8-10); last commit "Critical updates on various aspects" (logs/HEAD:10); .git/REBASE_HEAD holds 8cc02b56 (stale - rebase finished per logs/HEAD:4); ORIG_HEAD=5babb9ac; .git/info/exclude:7 excludes .codegraph locally.
11. GUI bridge: main.py:246-270 monkey-patches tts.tts_engine.speak and assistant.listener.listen_for_wake_word/listen_for_command at runtime; gui.py:12-20 accepts assistant_runner/stop_callback only.
12. tts.py:1-52 (V4, personality+config aware) vs core/tts.py:1-33 (simple). Root tts.py:48 instantiates tts_engine at import; core/tts.py:30 instantiates at import too.

## Decisions (with rationale)

- D1 Plan all six components (C1-C6): the brief is "all the structural improvements" - full scope is the default; each component is independently verifiable and backed by evidence above.
- D2 Waves ordered by risk/benefit: baseline verification first, then deletion (frees the import graph), then splits (largest maintainability win), then dedupe, packaging, data hygiene, GUI decoupling last (lowest risk, isolated).
- D3 Behavior locks: existing suite (F3 finding 9) is the regression net for C2/C3; where refactors touch untested modules (memory.py, scheduler.py, context_engine.py), add targeted unit tests in the same todo (implementation + test = one todo).
- D4 No API renaming: public class/function names survive C2/C3; only internal structure changes. No feature changes anywhere (Must NOT have).
- D5 Evidence for QA: every todo's QA runs agent-executed commands (pytest on the suite, python import smoke, ruff) with evidence files under .omo/evidence/.

## Scope IN

- C1: delete core/, modules/{app_control,system_controls,web_actions,file_ops,reminders}, jarvis_assistant.py, jarvis2.0.py, GUI_SUGGESTIONS/, test.py (keep modules/calendar_module.py)
- C2: split decision_engine.py, web_research.py, ai_parser.py, query_understanding.py into cohesive modules; keep public API stable; update all imports
- C3: single weather implementation (providers/weather_service.py); MemoryStore conversation API; normalize/dedupe where modules genuinely overlap (weather, memory layering; validation/normalization consolidation only where callers agree)
- C4: pyproject.toml ([tool.pytest.ini_options], [tool.ruff]); requirements.txt + requirements-dev.txt; add __init__.py to core/modules/utils only if needed for tests; rename providers/utils.py -> providers/text_utils.py
- C5: data/ dir for memory.json, reminders.json, app_registry_cache.json (with migration); .gitignore additions; verify git status/REBASE_HEAD staleness; remove stale git metadata only if git status is clean
- C6: replace main.py monkey-patching with constructor-injected callbacks on Listener/TTS
- Baseline + final verification waves (F1-F4), agent-executed QA with evidence

## Scope OUT (Must NOT have)

- No feature changes, no new capabilities, no AI/prompt/personality behavior changes
- No src-layout / jarvis package move (A1)
- No renaming of public classes/functions (D4)
- No deletion of modules/calendar_module.py or any live module
- No changes to .env/credentials.json/token.json contents or git history rewriting
- No dependency upgrades or downgrades (requirements pins stay; only added dev deps pytest+ruff)
- No README/docs work unless the final wave's plan compliance audit flags a missing run-instruction (then one-line note only)
- No edits to GUI_SUGGESTIONS before C1 (it is deleted as a whole)

## Open questions

- None. All forks resolved by evidence or adopted defaults (A1-A9); the user vetoes at the gate.

## Approval gate
status: awaiting-approval
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
<!-- Brief presented 2026-08-12. Next workflow action on approval: scaffold .omo/plans/jarvis-structural-improvements.md (rerun script without --draft-only), run Metis gap analysis, append todo batches, fill TL;DR last, then run the REQUIRED dual high-accuracy review (momus + independent oracle) automatically, then present the plan. -->
