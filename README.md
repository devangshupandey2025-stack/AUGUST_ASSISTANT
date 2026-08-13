# August AI Assistant

August AI Assistant is a local, extensible voice assistant framework implemented in Python. It provides speech-to-text (STT), text-to-speech (TTS), intent parsing, context-aware decision-making, providers for external data (weather, web), modules for system actions, and a simple GUI wrapper. The codebase has been reorganized under `src/august` for packaging and clearer module boundaries.

This README covers: quickstart, architecture, installation (Windows-first), model & dependency notes, running GUI/headless, testing, development guidelines, and security recommendations.

Status
------
- Working prototype with GUI and headless main loop.
- Local models (VOSK) are used for offline STT. External LLM integration is optional.
- Repository contains local runtime artifacts and example model data — these should be relocated when publishing.

Highlights / Features
---------------------
- Wake-word + command listening loop
- Rule-based and AI-backed intent parsing
- Provider interface for external data (weather, wikipedia, etc.)
- Scheduler and reminders
- Modular structure: core, modules, providers, utils (now under `src/porcupine_ai`)

Repository layout
-----------------
- src/august/ — main package containing modules and packages
  - core/ — core runtime components (listener, tts, executor)
  - modules/ — action modules (calendar, app control, reminders)
  - providers/ — external data adapters (weather, wikipedia)
  - utils/ — helpers (logger, common utilities)
- tests/ — pytest test suite (some tests may depend on providers)
- docs/ — project documentation (issues, notes)
- requirements.txt — pinned Python dependencies

Quickstart (Windows)
---------------------
Prerequisites:
- Python 3.11+ recommended
- Git
- Optional: microphone and speakers for full functionality

1. Create and activate a virtual environment (PowerShell):

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

2. Upgrade pip and install dependencies:

   python -m pip install --upgrade pip
   pip install -r requirements.txt

3. Download required models (VOSK example):
- Do NOT commit large models to git. Recommended: download into `models/vosk-model-small-en-us-0.15`.
- Example (PowerShell):
  - mkdir models
  - Invoke-WebRequest -Uri "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -OutFile models\vosk.zip
  - Expand-Archive models\vosk.zip -DestinationPath models

4. Create or export required environment variables (example):

   $env:GEMINI_API_KEY = '<your_api_key>'

5. Run the GUI (from repo root):

   python main.py

   The GUI patches the TTS and listener to show live logs and state.

Headless run
------------
To run without the GUI (e.g., for server or testing), import and invoke VoiceAssistant.run() from an alternate runner script that avoids GUI monkey-patching. Example:

   from src.august.main import VoiceAssistant
   assistant = VoiceAssistant()
   assistant.run()

Note: main.py currently inserts `src/august` on sys.path so legacy bare imports keep working. Long-term prefer explicit package imports (e.g., `from august.listener import Listener`).

Testing
-------
Run pytest from repository root:

   pip install -r requirements.txt
   pytest -q

Some tests hit provider code; for reliable CI, mock external calls or set network credentials via env vars.

Development notes
-----------------
- Code was reorganized into `src/porcupine_ai` for packaging. The repo still supports running `main.py` from root due to a bootstrap sys.path insertion.
- Recommended next steps:
  - Convert imports to explicit package imports and add a `pyproject.toml` for packaging.
  - Add pre-commit hooks (ruff/black/isort) and CI (GitHub Actions) to run tests and linters on PRs.
  - Remove `.venv`, `venv`, and any large model directories from git and add them to `.gitignore`.

Security & secrets
------------------
- There are files at repository root (e.g., credentials.json, token.json, memory.json) that look like local runtime state and may contain secrets. Do not commit credentials. If any secrets were committed, rotate them immediately.
- Use environment variables or a secrets manager for API keys. Add `SECURITY.md` describing reporting and handling.

Model & large-file policy
-------------------------
- Do not commit large ML models or virtualenvs into the repo. Instead:
  - Provide scripts to download models into a `models/` directory.
  - Add `models/`, `.venv/`, and `venv/` to `.gitignore`.
  - If large files are already in history, use `git filter-repo` or BFG to purge them after coordinating with collaborators.

CI / Packaging recommendations
-----------------------------
- Add GitHub Actions workflow to run tests and static analysis (ruff/flake8, mypy). Example jobs: test, lint, build.
- Add `pyproject.toml` for metadata and optional Poetry or pipx workflows.

Troubleshooting
---------------
- Push failures on Windows due to credential helper: configure Git Credential Manager (manager-core) or push with a PAT. See https://aka.ms/gcm/credstores
- If imports fail after re-org: ensure `PYTHONPATH` includes repo root or run `python main.py` from repo root (main.py bootstraps src path).

Contributing
------------
- Fork → feature branch → open PR. Keep changes small and add tests for behavior changes.
- Add a CONTRIBUTING.md to describe PR process, code style, and pre-commit hooks.

License
-------
- No license file is included in this repository. Add a license (MIT, Apache-2.0, or other) to clarify usage rights.

Contact & next steps
--------------------
If you want, the next automation steps I can take:
- Restore or create a README.md (this file) — done.
- Add CONTRIBUTING.md, SECURITY.md, and a sample GitHub Actions workflow.
- Replace sys.path bootstrap with explicit package imports and update tests.
- Remove virtualenv and model directories from git and add a download script for models.

Acknowledgements
----------------
- Generated/organized by a development assistant. Sensitive files were not included in this README.

