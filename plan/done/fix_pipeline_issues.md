# Fix Pipeline Issues — Stuck Bot & Sequential Processing

## Root Causes Found

### 1. Python server never restarted — still using Ollama
Logs show `Ollama request START -> http://10.0.0.30:11434/api/chat model=ibm/granite-3.2-8b`.
We changed the config to vLLM but the Python server was never restarted to pick it up.
The PeerFetcher fails on EVERY ticker because Ollama returns 404.

**Fix:** User must restart Python dev server. Config already correct.

### 2. MU returned null — DuckDB write took 5+ minutes
MU has 10,531 price rows → 333 indicator columns = 3.5M cells being written to DuckDB.  
`Stored 10531 comprehensive technical rows for MU` took from 23:03:52 → 23:09:46 (6 minutes).
The 300s pythonClient timeout fired before the write finished.

**Fix:** Skip re-storing technicals if already computed today (same as fundamentals/news).

### 3. Bot appears stuck — actually still in Collection phase
The `/api/llm/live` lines are just the frontend polling for LLM status.
The autonomous loop was still processing tickers sequentially in Collection.
The loop DOES proceed to Import → Analysis → Trading after collection finishes.
But the user stopped it before it got there.

### 4. Scraper should be parallel with LLM
Currently the pipeline is: Discovery → Collection (serial) → Import → Analysis → Trading.
All tickers are collected one-by-one before any LLM analysis starts.
The user wants: while one ticker is being analyzed by LLM (on Jetson), the next ticker should be collecting data (on local machine). These use different resources.

**Fix:** After collecting a ticker, immediately queue its LLM analysis in parallel.

## Implementation Plan

### A. Skip technicals re-computation (prevents MU timeout)
- File: `app/services/pipeline_service.py`
- Add "already computed today" check for technicals (same pattern as fundamentals/news)
- This prevents the 6-minute DuckDB write on re-runs

### B. Parallel scraping + LLM analysis in autonomousLoop.js  
- File: `tradingbackend/src/services/autonomousLoop.js`
- Instead of sequential phases, combine Collection + Analysis:
  - For each ticker: collect data → immediately fire Python `analyzeTicker(ticker, 'full')` 
  - Use `Promise.allSettled` to run 2-3 tickers concurrently (1 collecting, 1 analyzing)
  - This overlaps local CPU work (scraping) with remote GPU work (LLM on Jetson)

### C. Verify vLLM config is active after restart
- Run a quick test after server restart to confirm no Ollama calls

## Status: TODO
