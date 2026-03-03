# Fix BUY Trade Execution Failures — DONE

## Root Cause
1. DuckDB positions table had stale single-column PK (ticker only) — migration check was wrong
2. Cash starvation: .27 on  portfolio caused all buys to fail

## Fixes Applied
- database.py: Migration now checks actual PK column count via key_column_usage
- paper_trader.py: INSERT changed to INSERT OR REPLACE
- portfolio_strategist.py: Cash pre-check skips LLM loop when insufficient funds
- 	rading_pipeline_service.py: Execution wrapped in try/except

## Verified
- 25/25 tests pass, 0 failures
- Ruff lint clean (only pre-existing style issues)
