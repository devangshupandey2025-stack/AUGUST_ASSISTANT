# Project Structure

```text
JARVIS/
├─ .gitignore
├─ README.md
├─ main.py
├─ requirements.txt
├─ updates.md
├─ docs/
│  └─ issues.md
├─ _tentative_deletions/
│  └─ GUI_SUGGESTIONS/
│     ├─ gui.py
│     └─ main.py
├─ src/
│  └─ august/
│     ├─ __init__.py
│     ├─ acronym_resolver.py
│     ├─ ai_parser.py
│     ├─ answer_fallback.py
│     ├─ answer_memory.py
│     ├─ app_registry.py
│     ├─ config.py
│     ├─ consensus.py
│     ├─ context_engine.py
│     ├─ conversation_memory.py
│     ├─ decision_engine.py
│     ├─ document_generator.py
│     ├─ entity_guard.py
│     ├─ executor.py
│     ├─ followup_utils.py
│     ├─ garbage_detector.py
│     ├─ gui.py
│     ├─ intent_parser.py
│     ├─ jarvis2.0.py
│     ├─ jarvis_assistant.py
│     ├─ knowledge_governor.py
│     ├─ listener.py
│     ├─ memory.py
│     ├─ personality_engine.py
│     ├─ preprocessor.py
│     ├─ query_normalizer.py
│     ├─ query_understanding.py
│     ├─ result_filter.py
│     ├─ result_validator.py
│     ├─ retrieval_confidence.py
│     ├─ sanity_validator.py
│     ├─ scheduler.py
│     ├─ search_synthesizer.py
│     ├─ system_intents.py
│     ├─ test.py
│     ├─ test_gui.py
│     ├─ tts.py
│     ├─ weather_service.py
│     ├─ web_research.py
│     ├─ core/
│     │  ├─ __init__.py
│     │  ├─ executor.py
│     │  ├─ intent_parser.py
│     │  ├─ listener.py
│     │  └─ tts.py
│     ├─ modules/
│     │  ├─ __init__.py
│     │  ├─ app_control.py
│     │  ├─ calendar_module.py
│     │  ├─ file_ops.py
│     │  ├─ reminders.py
│     │  ├─ system_controls.py
│     │  └─ web_actions.py
│     ├─ providers/
│     │  ├─ __init__.py
│     │  ├─ base_provider.py
│     │  ├─ provider_result.py
│     │  ├─ provider_router.py
│     │  ├─ utils.py
│     │  ├─ weather_provider.py
│     │  ├─ weather_service.py
│     │  └─ wikipedia_provider.py
│     └─ utils/
│        ├─ __init__.py
│        └─ logger.py
├─ tests/
│  ├─ test_calendar_module.py
│  ├─ test_decision_engine.py
│  ├─ test_providers.py
│  ├─ test_retrieval_intelligence.py
│  ├─ test_weather_provider.py
│  └─ test_web_research_stability.py
└─ vosk-model-small-en-us-0.15/
   ├─ README
   ├─ am/
   │  └─ final.mdl
   ├─ conf/
   │  ├─ mfcc.conf
   │  └─ model.conf
   ├─ graph/
   │  ├─ Gr.fst
   │  ├─ HCLr.fst
   │  ├─ disambig_tid.int
   │  └─ phones/
   │     └─ word_boundary.int
   └─ ivector/
      ├─ final.dubm
      ├─ final.ie
      ├─ final.mat
      ├─ global_cmvn.stats
      ├─ online_cmvn.conf
      └─ splice.conf
```

`app_registry_cache.json` and `TEST_OUTPUTS/` are generated runtime/test artifacts and are intentionally excluded from source tracking.
