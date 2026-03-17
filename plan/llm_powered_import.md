# LLM-Powered Import Phase Plan

## Problem
Import phase used a simple numeric threshold (discovery_score >= 3.0) to decide
which tickers to add to the watchlist. The LLM never evaluated whether stocks
had genuine investment potential.

## Fix: New Pipeline Order
Discovery → **Discovery Collection** → **LLM-Powered Import** → Collection → Embedding

1. Discovery scans social media for tickers (unchanged)
2. **NEW: Discovery Collection** — runs `PipelineService.run(ticker, mode="data")` for
   each discovered ticker to fetch financials, technicals, risk metrics, analyst data
3. **NEW: LLM Import** — LLM evaluates all candidate tickers using collected data
   and selects only those with genuine investment potential (replaces score threshold)
4. Collection refreshes data for watchlist tickers (unchanged)

## Files Changed
- `app/services/watchlist_manager.py` — added `llm_import_evaluation()` method
- `app/services/autonomous_loop.py` — added `_do_discovery_collection()`, updated
  `_do_import()` to call LLM, reordered phases in all three loop methods
