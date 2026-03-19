# Pipeline Audit & Telemetry System Changes

## Phase 1-4: Pipeline Audits & Bug Fixes
- **LLM Token Calculation Fix:** Investigated and resolved issues causing the LLM service to crash or return empty responses. Fixed `TypeError` associated with passing messages as positional arguments instead of keyword-only arguments to `llm.chat()`. Let tracebacks print out for easier debugging.
- **Execution Logging Fix:** Corrected mock `TradeAction` parameters (e.g., `confidence`, `rationale`, casing) across test suites to align perfectly with the updated Pydantic schema enforcing pipeline standards.
- **RAG & Discovery Tests:** Successfully ran audit validation tests (Phase 1 to 4) on all Data Collection components to ensure tools don't fail silently or drop references.

## Phase 5: Comprehensive Telemetry System
- **Unified Logger (`unified_logger.py`):** Created the `@track_telemetry` and `@track_class_telemetry` decorators to automatically trace inputs, outputs, errors, and timing (ms) for all 55 pipeline tools without changing function signatures using `ContextVar` (`cycle_id`).
- **Automated Decoration (`apply_telemetry.py`):** Created an AST injection script to effortlessly inject `@track_class_telemetry` to the public interfaces across all 35+ services to scale the codebase.
- **DuckDB Persistence:** Generated `pipeline_telemetry` table in DuckDB to store immutable audit trails for every pipeline invocation, enabling real-time analytics.
- **Node Backend Proxy (`tradingbackend`):** Created the `pythonClient.js` method `getPipelineTelemetry` and proxy Express route `/api/pipeline/telemetry` to extract live trace logic from the Python DuckDB instance for the frontend.
- **Diagnostics UI (`TelemetryPanel.tsx`):** Designed and embedded a real-time polling React component in the main ReactFlow graph page measuring IO payload scale and tracking module lifecycles in precise milliseconds.

## Hard Pipeline Chaos Audit
*   **Data Collection Resilience**
    *   Simulated `httpx.TimeoutException` on upstream HTTP calls to verify graceful scraping degradation.
    *   Tested `sec_13f_service` and `congress_service` with corrupt XML/JSON payloads to validate parsing boundaries.
    *   Fired 50,000+ token simulated video payloads at `youtube_service` to prove context size handling.
*   **Quant Engine Math Boundary Enhancements**
    *   Replaced explicit `AllStudy` `pandas-ta` core calls with robust per-indicator loops containing automatic Nan-forward-backfills.
    *   Fixed division-by-zero segfaults caused when parsing tickers featuring `Volume = 0` or perfectly static `High = Low = Close` distributions.
*   **LLM Context Security**
    *   Routinely funneled 150,000 token inputs into `DataDistiller` to confirm functional loss-less summarization truncation limits.
    *   Shot "Ignore prior user instructions" jailbreak sequences into `AgenticExtractor` utilizing DeepSeek R5 evaluation models to verify JSON structure rigidity out-lasted unconstrained text generation.
    *   Checked parallel DuckDB multi-worker access loops across 5 concurrent `DeepAnalysisService` evaluations.
*   **Execution Stability**
    *   Injected consecutive massive mock portfolio negative valuations into DuckDB to trigger the `< 5% Circuit Breaker`, demonstrating instant trading halts.
    *   Dispatched SIGKILL / `os._exit(1)` interrupts directly into the process ID mid-DuckDB transaction to ensure WAL rollbacks cleared incomplete uncommitted database transactions unconditionally.

## Phase 7: vLLM Automatic Prefix Caching (APC) Optimization
- **Master String Construction (`brain_loop.py`):** Changed extraction logic to aggregate isolated dictionary data sources into one unified massive string instead of splitting across LLM endpoints.
- **Identical Caching Configuration (`analyst_prompts.py`):** Positioned `<STATIC_CONTEXT>` variables explicitly at the absolute top of the system prompt to allow for exactly-matching prefix caches downstream.
- **Sequential Context Ingestion (`trading_agent.py`):** Adjusted loop to submit the identical master config to each domain agent incrementally natively dropping GPU generation latency.

## Frontend Bug Fixes
- **Autobot Monitor Type Error:** Fixed an `Uncaught TypeError` in `terminal_app.js` (`(portfolio.positions || []).flatMap is not a function`) by parsing `portfolio.positions` safely to handle both legacy array formats and new dictionary (object) formats.
- **Python Service Lock Fix:** Terminated a hung, detached Python process preventing subsequent Uvicorn restarts from acquiring the DuckDB connection lock.
- **Telemetry Import Missing Error:** Fixed a runtime crash during autonomous discovery execution by adding a missing `track_class_telemetry` import statement on `symbol_filter.py` referencing `unified_logger`.

## Ticker Data Tab Endpoint Fixes
- **ChartWidget**: Changed price data source from `/api/dashboard/prices/` (empty MongoDB) to `/api/python/prices/` (DuckDB via proxy) so stock charts actually render.
- **fetchReddit**: Changed from `/api/dashboard/reddit/` to `/api/python/reddit/` and fixed response field mapping from `data.mentions` to `data.posts || data.mentions`.
- **fetchDossier**: Changed from `/api/dossiers/` (returns error on Node) to `/api/python/dossier/` (proxied to Python backend which has the actual dossier data).
