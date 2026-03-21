# Plan: Fix LLM Data Chunking & Confirm DB Persistence

## The Problem
1. **Timeouts & Huge Data Lists**: The `TradingAgent` is currently concatenating all 5 analytical domains into one massive "Master String" and feeding it to the LLM 5 times. This bloats the context window and slows down generation, causing timeouts.
2. **Database Persistence**: The user needs confirmation that Python relies on DuckDB for financial processing and Node uses MongoDB for recording decisions/events.

## The Fix
1. Modify `app/services/brain_loop.py` to stop using `master_data`. Update `AnalystAgent.run_all_domains` to accept the broken-down `domain_data` dictionary.
2. For each domain (Technical, Fundamental, Sentiment, Risk, Smart Money), extract ONLY its respective chunk and feed that specific piece to the LLM. 
3. Modify `app/services/trading_agent.py` to pass the `domain_data` dictionary rather than the bloated concatenated string. Keep the `master_string` *only* to feed into the `validate_memo_citations` function so it can verify the LLM isn't hallucinating quotes.

## Execution
Once this plan is applied, we will run the `run_pipeline_audit.py` test again and verify that the timeouts are resolved and the data processing completes successfully with smaller inputs.
