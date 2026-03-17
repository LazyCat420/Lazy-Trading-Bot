# Pipeline Events and VRAM Contention Fixes Report

**Date:** March 17, 2026
**Target Codebase:** `Lazy-Trading-Bot` (HEAD: `53973319` series)

This report details the verification and resolution of two critical infrastructure bugs originally flagged in the RAG/Embedding Audit. The problems were verified to exist exactly as described in the pushback notes, and the codebase has been permanently patched.

---

## 1. Verified & Fixed: The `pipeline_events` Schema Conflict (Issue #5)

**The Verification:**
The pushback was completely accurate. The issue was not that two schemas were actively fighting at runtime, but rather a "dead letter" declaration.
- `database.py` created Schema 1 first (`id VARCHAR, timestamp, phase, event_type...`).
- Because of `IF NOT EXISTS`, the Schema 2 definition (`id INTEGER, bot_id, event_type, event_data, created_at`) silently failed to execute.
- Because `trade_action_parser.py`, `trading_agent.py`, and `main.py` relied on Schema 2's `event_data` and `created_at` columns, their SQL queries and inserts were constantly throwing silent exceptions.
- This resulted in `PromptEvolver.py` and `ImprovementFeed.py` losing all visibility into parse failures and tool usage.

**The Fixes Implemented:**
1. **Schema Consolidation:** Removed the dead Schema 2 sequence and table definition from `app/database.py` (L958-969). The database now universally operates on Schema 1.
2. **Unified API Usage:** Refactored `_log_parse_event` (in `trade_action_parser.py`) and `_log_tool_usage` (in `trading_agent.py`) to stop using raw SQL. They now use the central `log_event()` helper from `app.services.event_logger`, which safely packs the arbitrary JSON data into the `metadata` column of Schema 1.
3. **Query Corrections:**
   - Fixed `PromptEvolver.py` (L167) and `ImprovementFeed.py` (L83) to query the `timestamp` column instead of the non-existent `created_at`.
   - Fixed the `/api/diagnostics/pipeline-events` endpoint in `main.py` (L3478) to alias `metadata as event_data` and `timestamp as created_at`, restoring the API contract without breaking the frontend.

---

## 2. Verified & Fixed: The VRAM Pipeline Inversion (Issue #6 & #7)

**The Verification:**
The pushback was accurate. The VRAM conflict was isolated exclusively to `run_full_loop()` (single-bot mode), not "Run All Bots" which safely uses `run_shared_phases()`. 
In `run_full_loop()`, the `verify_and_warm_ollama_model` step ran at the very beginning of the loop, permanently loading the heavy LLM into VRAM with a 2-hour keep-alive. Later, step 7 (`_do_embedding`) required `nomic-embed-text` to load, forcing Ollama to juggle both concurrently and risking severe OOM on single-GPU hardware.

**The Fix Implemented:**
1. **Reordered Pre-Warm:** Physically moved the `unload_all_ollama_models()` and `verify_and_warm_ollama_model()` block out of the startup sequence in `run_full_loop()`.
2. **Safe Placement:** It is now positioned safely *after* the fast/embedding-heavy phases (Discovery, Import, Collection, Embedding) and immediately *before* `_do_deep_analysis()`. 
3. **Conditional Warm-Up:** Added a toggle guard so the pre-warm only executes if `analysis`, `trading`, or `import` phases are enabled.

This effectively quarantines the VRAM-hungry `nomic-embed-text` from the main LLM.

---

## Conclusion
The developer can review the attached commits/changes. The Prompt Evolver is no longer flying blind, and the single-bot loop is now VRAM-safe.
