# Pipeline Rebuild — Self-Contained Diagnostics (Round 1)

## What Was Done

**Local Conversation Tracking (replaces Prism):**
- Created `ConversationTracker.py` — tracks every LLM call as a conversation with provider, tokens, tok/s
- Added `llm_conversations` table to DuckDB
- Wired into `LLMService.chat()` — every call auto-creates a conversation record

**Provider-Aware Audit Logging:**
- Added `provider` and `conversation_id` columns to `llm_audit_logs`
- Every logged LLM call now shows whether it used vLLM or Prism/Ollama

**Prism Dependencies Removed:**
- Removed `WorkflowTracker.post_workflow()` from `autonomous_loop.py` (both run_full_loop and run_llm_only_loop)
- Removed per-ticker Prism workflow posting from `trading_pipeline_service.py`
- Removed `PrismBridge.forward_to_prism()` from `llm_service.py`
- All workflows now saved locally via `workflow_assembler.save_workflow()`

**New API Endpoints:**
- `GET /api/conversations` — list with provider/model filters
- `GET /api/conversations/active` — currently generating conversations
- `GET /api/conversations/summary` — aggregate stats by provider
- `GET /api/conversations/{id}` — full detail with linked audit logs
- Updated `/api/llm/live` to include provider field

## Files Changed

| File | Type |
|------|------|
| `app/services/ConversationTracker.py` | NEW |
| `app/database.py` | Modified (new table + migrations) |
| `app/services/llm_audit_logger.py` | Modified (new fields) |
| `app/services/llm_service.py` | Modified (conversation tracking + remove PrismBridge) |
| `app/services/autonomous_loop.py` | Modified (local workflow, remove Prism) |
| `app/services/trading_pipeline_service.py` | Modified (remove Prism workflow) |
| `app/main.py` | Modified (new API endpoints + provider in live feed) |

## Tested

- DB boots clean with all migrations
- `ConversationTracker` full lifecycle works (start → add → end → query)
- Provider tracking confirmed: shows "vllm" correctly
- Summary aggregation works (total convos, tokens, tok/s by provider)
- All module imports pass
