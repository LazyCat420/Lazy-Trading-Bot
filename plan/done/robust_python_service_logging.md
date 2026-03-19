# Plan: Robust Python Service Logging

## Problem
The autonomous loop logs "Python service online — enriching data from all 55 services" but provides zero detail about which services ran, succeeded, or failed. Makes debugging impossible.

## Root cause
Two JS callsites suppress all per-service detail:

1. **`autonomousLoop.js:_doCollection()`** — calls `pythonClient.analyzeTicker(ticker, 'data')` which triggers the Python `PipelineService.run()` with 14+ steps. Only logs "Python collected: TICKER" or "skipped".
2. **`deepAnalysis.js:_fetchPythonData()`** — calls 8 pythonClient methods in parallel. Only logs "Got X/8 data sources from Python for TICKER".

## Fix

### 1. `deepAnalysis.js` `_fetchPythonData()` — log each of 8 sources individually
- Time each call
- Log pass/fail with ms elapsed
- Log data quality (row counts, article counts, etc.)
- Print a summary table

### 2. `autonomousLoop.js` `_doCollection()` — log Python result details
- Parse the `pipeline_status` returned by `/api/analyze`
- Log each step's status (ok/error) with details (rows, articles, etc.)
- Print a summary line: "X/Y steps ok, Z errors"

### 3. `pythonClient.js` — add timing to `_get()` and `_post()`
- Log start+finish for each request with elapsed ms
- Use logger.info for success, logger.warn for failures (already does this)
