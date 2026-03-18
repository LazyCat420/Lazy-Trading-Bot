# Shared Pipeline Fix — Plan & Checklist

## Root Cause Analysis

### Bug: "0 tickers discovered, 0 analyzed"
**First Principles:** The `_run_all()` was restructured to run shared phases ONCE, 
but the shared phase imported tickers to `bot_id="shared"` — a non-existent bot. 
Each real bot's `WatchlistManager` queries `WHERE bot_id = ?` with its own ID, 
so it saw 0 tickers.

**Fix:** Added `_do_import()` to `run_llm_only_loop()` so each bot imports from 
the global `ticker_scores` table into its OWN watchlist before analysis.

### Bug: "Self-questions have empty answers"
**First Principals:** The `extraction_self_question` prompt asked for 
`{"questions": [...], "answers": [...]}` but:
1. Only gave 300 chars of context — not enough to answer anything
2. The prompt focused on vague "what should we watch" questions
3. The LLM correctly returned `"answers": []` because it had nothing to answer from

**Fix:** Rewrote prompt to require answered questions focused on buy/sell data, 
expanded context to 2000 chars + trading data.

## Checklist

- [x] Root cause: watchlist is bot-scoped, shared import goes to wrong bot
- [x] Fix: add `_do_import()` to `run_llm_only_loop()` 
- [x] Root cause: self-question prompt is vague and context too short
- [x] Fix: rewrite prompt to require answers focused on buy/sell decisions
- [x] Write tests before deploying (test_shared_pipeline.py — 16 tests)
- [x] All tests pass
- [x] Syntax check all modified files
- [x] Dedup guards added (quant_scorecards, ticker_dossiers)

## Files Modified
- `app/services/autonomous_loop.py` — added import step to `run_llm_only_loop()`
- `app/services/AgenticExtractor.py` — rewrote self-question prompt + expanded context
- `app/main.py` — restructured `_run_all()` (shared phases + per-bot LLM)
- `app/services/quant_engine.py` — dedup guard on scorecards
- `app/services/deep_analysis_service.py` — dedup guard on dossiers
- `tests/test_shared_pipeline.py` — 16 new tests
- `tests/test_db_duplicates.py` — 13 existing tests (fixed syntax error)
