# LLM-Powered Import Phase — Done

## What was changed
1. **New pipeline order**: Discovery → Discovery Collection → LLM Import → Collection → Embedding
2. **LLM evaluates tickers**: Instead of a score threshold, the LLM receives financial data
   (fundamentals, technicals, risk metrics, analyst consensus) and decides which stocks
   deserve a watchlist spot — with rationale for each selection/rejection
3. **Pre-import data collection**: Basic data is fetched for discovered tickers BEFORE
   the LLM evaluates them, so the model has real data to work with

## Files Changed
- `app/services/watchlist_manager.py` — added `llm_import_evaluation()`
- `app/services/autonomous_loop.py` — added `_do_discovery_collection()`, updated `_do_import()`

## Test Results
- 25/25 tests pass in `test_shared_pipeline.py`
- All method signatures and phase ordering verified via introspection
