# Fix: Null Tickers + Diagnostics Live Feed + Conversations

## Issues Identified

### 1. MU and SPY returning null from Python pipeline
- The `pythonClient.analyzeTicker()` in `autonomousLoop.js` has a `LONG_TIMEOUT = 480000` (8 min)
- The Python pipeline runs data collection + peer_discovery LLM call (vLLM streaming)
- The vLLM peer_discovery step alone takes 90+ seconds streaming through Prism
- For MU/SPY, the total time exceeded 480s → JS `AbortError` → returns `null`
- **Root cause**: Timeout too short for pipeline + LLM per ticker. Need to increase to 600s (10 min)

### 2. Python logs not showing in frontend autonomous loop console
- The `_log()` method in `autonomousLoop.js` stores `{time, message}` entries
- The frontend reads `loopStatus.log` and displays it
- But the frontend expects `{time, message, level, phase}` (RunAllConsole has level/phase filtering)
- The autonomous loop console on DiagnosticsPage (lines 6517-6526) only renders `{time, message}`
- The `_log()` needs to broadcast richer logs via WebSocket and we need to broadcast individual log entries
- **Fix**: Enhance `_log()` to broadcast each log entry via pipelineService, attach log to WebSocket broadcast

### 3. Live Request Stream showing 0 tokens
- The tradingbackend proxies `/api/llm/live` from Prism's `/admin/requests`
- The mapping in `configRoutes.js:294` maps `r.inputTokens` and `r.outputTokens` but displays as `tokens_used` 
- The frontend reads `req.tokens_used` on line 6577 → but the live feed returns `inputTokens/outputTokens`, NOT `tokens_used`
- **Fix**: Map tokens correctly in the live feed proxy: `tokens_used: (r.inputTokens || 0) + (r.outputTokens || 0)`
- Also map `execution_time_ms: (r.totalTime || 0) * 1000`, `agent_step`, `ticker`, `created_at`

### 4. Conversations not populated
- Frontend calls `/api/conversations?limit=100` → tradingbackend proxies to Prism `/conversations`
- Prism conversations route exists and stores conversations in MongoDB `conversations` collection
- Python LLM service uses ConversationTracker which stores conversations in DuckDB, NOT in Prism
- The tradingbackend's LLM calls go through PrismClient which passes `conversationId` to Prism
- But the Python service's LLM calls now go through Prism too (vLLM via Prism gateway)
- Conversations SHOULD be populated by Prism when the Python service sends `conversationMeta`
- Need to verify that the Python LLM service is passing conversation metadata through Prism

## Implementation Plan

### Step A: Increase Python client timeout (pythonClient.js)
- Change `LONG_TIMEOUT` from `480000` to `600000` (10 min)

### Step B: Fix live feed token/field mapping (configRoutes.js) 
- Map Prism request fields to match what frontend expects: `tokens_used`, `execution_time_ms`, `agent_step`, `ticker`, `model`, `provider`, `created_at`

### Step C: Enhance autonomous loop log broadcasting (autonomousLoop.js)
- Broadcast each `_log()` entry via pipelineService so frontend gets real-time updates
- Add the log entry to WebSocket broadcast with `type: 'log_entry'`

### Step D: Fix llm/request/:id endpoint to proxy from Prism
- Currently returns 404 stub. Should proxy to Prism `/admin/requests/:id`

### Step E: Fix conversations proxy 
- Verify Prism conversations endpoint returns data 
- Ensure Python LLM calls send conversation metadata through Prism
