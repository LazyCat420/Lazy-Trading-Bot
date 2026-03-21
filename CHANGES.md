# CHANGES.md — 2026-03-19

## 🗄️ Database Separation: Phase 1 (DuckDB Financial + MongoDB Bot)

### `event_logger.py`
- **DISABLED**: DuckDB INSERT for `pipeline_events` — MongoDB is now source of truth
- WebSocket broadcast preserved (frontend Activity tab still works)

### `decision_logger.py`
- **DISABLED**: DuckDB INSERT for `trade_decisions` and `trade_executions`
- MongoDB (via tradingbackend) is now source of truth for trading data
- Function signatures unchanged — callers unaffected

### `full_pipeline_audit.py`
- Added `safe_connect()` — copies DuckDB to /tmp when server holds lock
- `--baseline` now runs full 14-check audit + saves row-count snapshot
- Fixed telemetry status check: matches both `'ok'` and `'success'`
- ETF-aware watchlist check: skips financial_history/balance_sheet/cash_flows for ETFs
- Telemetry name matching: extracts method from `ClassName.method` format
- Split expected tools into collection (must appear) vs execution (only after trades)

---

## 🔴 Critical: Portfolio $0 Bug Fix

### `tradingbackend/src/services/portfolioService.js`
- **BUG**: `getSummary()` returned `{cash_balance, total_portfolio_value, positions_count}` but `tradingAgent.js` used `{cash, equity, positions}` — all resolved to `undefined` → $0
- **FIX**: Returns both canonical names (`cash`, `equity`, `positions`) and verbose names for backward compat
- Added per-bot `starting_balance` lookup from bot document, falls back to global `risk_params`
- Added diagnostic logging showing cash/equity/positions/starting for each bot

### `tradingbackend/src/services/botRegistry.js`
- **BUG**: `registerBotIfNotExists()` matched only by `model_name` — different settings shared the same bot/portfolio
- **FIX**: New `_settingsFingerprint()` hashes `model_name|temperature|context_length|top_p|max_tokens` via MD5
- `registerBot()` now saves `settings_fingerprint` and `starting_balance` on the bot document
- Different LLM configs → different bots → separate portfolios (A/B testing)

### `tradingbackend/src/services/autonomousLoop.js`
- Passes full LLM config (temperature, context_size, top_p, max_tokens, starting_balance) to `registerBotIfNotExists()`
- Bot fingerprint now meaningful for A/B testing

---

## 📊 Enhanced Activity Logging

### `tradingbackend/src/services/autonomousLoop.js`
- **Discovery**: logs per-source breakdown + top discovered tickers with scores
- **Import**: logs each ticker verdict (ADD/SKIP with reason), emits per-ticker WebSocket events
- **Analysis**: logs per-ticker conviction score + signal
- **Trading**: logs each decision (action, confidence, execution status)
- All phases emit granular events via `_emitEvent()` for WebSocket + DuckDB persistence

### `Lazy-Trading-Bot/app/services/pipeline_service.py`
- Added `log_event()` import from `event_logger`
- Added `_emit_step_event()` helper in `run()` method
- Emits pipeline events after all 14 data collection steps:
  - yfinance steps 1-9 (price_history, fundamentals, financial_history, balance_sheet, cashflow, analyst_data, insider_activity, earnings_calendar)
  - Step 4: technicals
  - Steps 11-12: news + youtube
  - Step 14a-c: SEC 13F, congressional trades, RSS news
- Events include status, row counts, and error details

---

## 🧪 New: Full Pipeline Audit Script

### `Lazy-Trading-Bot/tests/full_pipeline_audit.py` (NEW)
- 14 checks across 6 categories:
  - **Part 1**: DuckDB table census — row counts for 34 tables, critical table checks
  - **Part 2**: Data quality — price freshness, fundamentals completeness, technicals, news, youtube
  - **Part 3**: Pipeline telemetry — tool coverage, failure rates, slow steps (>30s)
  - **Part 4**: Pipeline events — phase coverage, recent activity (24h)
  - **Part 5**: Cross-table consistency — watchlist tickers vs 7 core data tables
  - **Part 6**: Tool coverage matrix — 23 tool→table mappings verification
- Supports `--json` flag for programmatic JSON report generation
- Run with: `source venv/bin/activate && python tests/full_pipeline_audit.py [--json]`
## 🩺 Phase Diagnostics

### `tradingbackend/tests/phase_diagnostic.js` (NEW)
- Created a MongoDB-aware test for the pipeline with `--current` and `--after` modes.
- Validates bot registries, discovery, collection, analysis, and trading decisions by checking MongoDB collections.

### Pipeline Fixes
- **`botRegistry.js`**: Reinstated legacy fallback query by `model_name` and backfill logic for missing bot fingerprints, fixing the issue of empty trading loops.
- **Collection Limits**: Reset collection limits from 1 back up to 3 to resume reasonable debug-scale pipeline throughput.
- **Bot Registry Cleanup**: Removed orphan `bot_53a43347` from MongoDB.

---

## 🛠️ Test Scripts & Model Configuration Fixes

### `app/user_config/llm_config.json`
- **BUG**: `db_profile` was hardcoded to `test`, forcing the main web server to lock the test database (`trading_bot_test.duckdb`). This caused all test scripts to fail with DuckDB IO lock errors.
- **FIX**: Changed `db_profile` to `main` so the main server connects to the standard database, freeing the test database for diagnostic scripts.

### `scripts/run_pipeline_audit.py`
- **BUG**: The model was hardcoded to `TEST_MODEL = "gemma3:4b"`, completely overriding the configuration (e.g. `vllm`) during the test audits.
- **FIX**: Removed the forced `TEST_MODEL` override and updated all references to use `settings.LLM_MODEL` directly. The audit script will now correctly use the configured model.

---

## 🧠 Context Chunking & Database Clarification Checklist
- [x] Analyzed how Python handles LLM context generation in `trading_agent.py` and `brain_loop.py`.
- [x] Identified that `AnalystAgent.run_all_domains` was feeding one massive "Master String" (containing all domains like Technical, Fundamental, Sentiment, Risk, etc.) into the context window for *every* individual analysis pass, causing bloat and heavy timeouts.
- [x] Replaced `master_data` in `AnalystAgent.run_all_domains` with a `domain_data_map` dictionary so it now chunks the specific data relevant ONLY to each domain analyst (e.g. Technical analyst only sees Technicals data).
- [x] Confirmed the MongoDB vs DuckDB architecture: All extensive financial calculations/snapshots are successfully stored in `duckdb` via Python collectors to maintain performance. All stateful trading events and decisions are natively broadcasted from Python via WebSockets to the Node.js `tradingbackend`, which successfully saves them to MongoDB.
- [x] Ran the full pipeline audit using `run_pipeline_audit.py --phase all` to verify the new chunked processing architecture generates the proper memos smoothly using the standard vLLM instance.

---

## 🧠 V3 Multi-Layered Brain Architecture (Seed-Driven Investigation)

### New Files
- **`app/services/signal_ranker.py`** — Pure-Python anomaly scorer. Scans domain data for extreme values (RSI >75/<25, D/E >1.5, earnings imminent, insider selling, etc.) and outputs ranked `Seed` objects with suggested tools.
- **`app/services/investigation_agent.py`** — ReAct-style iterative research agent. For each seed: calls 2-3 tools, feeds results to LLM, LLM writes finding + picks next tools, repeats up to 5 iterations, then synthesizes all findings into a structured memo.
- **`app/services/investigation_prompts.py`** — Prompt templates for the investigation loop and synthesis phase.

### Modified Files
- **`app/services/trading_agent.py`** — Rewired from V2 (dump-all-data-to-5-analysts) to V3 (SignalRanker → InvestigationAgent → ThesisConstructor → DecisionAgent). Phase 0 scans data for anomalies, Phase 1 investigates each seed with targeted tool calls.
- **`app/services/brain_loop.py`** — Added `isinstance(memo, dict)` type validation to all LLM parse blocks (AnalystAgent, ThesisConstructor, ContradictionPass, DecisionAgent) to prevent crashes when reasoning models return raw text instead of JSON.

### Architecture Change
```
OLD: [All data] → [5 domain analysts get everything] → [Thesis] → [Decision]
NEW: [SignalRanker scans for anomalies] → [Top 3 seeds] → [ReAct investigation per seed]
     → [Each seed: tool call → finding → next tool → finding] → [Thesis] → [Decision]
```

### Verified Results (AAPL test run)
- Seed 1: `debt_concern` (score=0.95) → D/E=102.63, institutional=65.265% → NEUTRAL conf=0.45
- Seed 2: `news_catalyst` (score=0.80) → Legal victory + RSI 35.28 + Stochastic 9.06 → BULLISH conf=0.65
- Seed 3: `congressional_trade` (score=0.60) → Pelosi purchased $1-5M AAPL → BULLISH conf=0.65
- Total: 6 tool calls, 3 LLM calls, 19 lemmas generated

### Bugfix
- Fixed `NameError: name 'citation_results' is not defined` — the old citation validation was removed during V3 wiring but the metadata reference remained. Replaced with investigation metadata (seed categories, tool/LLM call counts).

### Single Model Enforcement
- Changed `LLM_PROVIDER` default from `"ollama"` to `"vllm"` in `app/config.py` — all LLM calls now go through vLLM by default
- Fixed JSON retry fallback in `llm_service.py` line 408 — was hardcoded to `_send_ollama_request` even when provider is vLLM. Now dispatches to the correct provider method
- Verified: single model `Kbenkhaled/Qwen3.5-35B-A3B-quantized.w4a16` used for everything (peer discovery, investigation, thesis, decision)

### Pipeline Performance Fixes
- **Technicals skip-if-fresh** (`app/services/technical_service.py`): Added check to skip re-computing 10K+ row technicals if already done today — was causing 6-minute DuckDB write timeouts
- **Parallel ticker enrichment** (`tradingbackend/src/services/autonomousLoop.js`): Changed sequential one-by-one ticker processing to parallel batches of 3 using `Promise.allSettled` — overlaps local CPU scraping with remote GPU LLM work

### LLM Pipeline Fix — Zero Queries Bug
**Root cause:** Node.js tradingbackend defaults were hardcoded to `ollama`/`gemma3:27b` in 5 locations. Python `llm_config.json` was never read by Node.js.
- `config.js` → `DEFAULT_LLM_PROVIDER` changed from `ollama` → `vllm`, `DEFAULT_LLM_MODEL` → `Kbenkhaled/Qwen3.5-35B-A3B-quantized.w4a16`
- `llmHelper.js` → All 5 fallback paths changed from `ollama` → `vllm`
- `prismClient.js` → `prompt()` default provider changed from `ollama` → `vllm`
- `autonomousLoop.js` → Added **LLM preflight check**: sends test prompt through Prism→vLLM before starting. Aborts loop immediately if LLM unreachable (prevents 30min silent failures)

