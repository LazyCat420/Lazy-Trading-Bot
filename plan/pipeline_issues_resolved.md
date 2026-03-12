# Pipeline Issues Resolved — Round 1

> Fixes for: Trade repair logging, tool usage investigation, performance metrics

---

## Fix 1: Full Debug Logging in Trade Action Parser

**File**: `app/services/trade_action_parser.py` (full rewrite)

### What Changed
| Before | After |
|--------|-------|
| Validation errors logged at `DEBUG` | Logged at `WARNING` with FULL error details |
| Broken JSON never visible | First 500 chars of broken JSON logged |
| Repair outcomes silent | ✅ or ❌ logged with action/confidence on success |
| No DB tracking | All events stored in `pipeline_events` table |
| `confidence: "high"` → crash | Auto-converts strings to floats (`"high"` → `0.8`) |

### Event Types Logged to DB
| Event | Meaning |
|-------|---------|
| `trade_parse:parse_ok` | JSON parsed successfully on first try |
| `trade_parse:parse_failed` | Initial parse failed — includes error + broken JSON preview |
| `trade_parse:repair_succeeded` | LLM repair fixed the broken JSON |
| `trade_parse:repair_failed` | LLM repair couldn't fix it |
| `trade_parse:forced_hold` | Gave up — forced HOLD with 0.0 confidence |

### Root Cause of ITT Failure
Most likely: the LLM output `confidence: "high"` (a string) instead of `confidence: 0.75` (a number). 
The new parser now auto-converts: `"low"→0.3, "medium"→0.5, "high"→0.8, "very high"→0.9`.

---

## Fix 2: System Prompt — Explicit Field Types + Tool Usage

**File**: `app/services/trading_agent.py`

### Why No Research Tools Were Used
The old system prompt said:
```
"You have up to 4 research tool calls available. Use them wisely."
```
Small models (nemotron-3-nano) interpret **"use them wisely"** as **"don't use them unless absolutely necessary"** → they skip directly to a decision.

The old prompt also used pipe syntax for field values:
```
"action": "BUY" | "SELL" | "HOLD"
"confidence": 0.0 to 1.0
```
Small models don't understand this means "pick one" — they sometimes output `"0.0 to 1.0"` literally, or `"BUY | SELL"`.

### What Changed
1. **Tool usage**: Changed from "use them wisely" to:
   ```
   Call at least 1 research tool to verify your analysis BEFORE deciding.
   Skipping research is ONLY acceptable when data is already comprehensive.
   ```

2. **Field types**: Added `STRICT FIELD RULES` section with explicit instructions:
   ```
   "action" → MUST be exactly one of: "BUY", "SELL", "HOLD" (uppercase, no other words)
   "confidence" → MUST be a decimal number between 0.0 and 1.0 (e.g. 0.75, NOT "high")
   "risk_level" → MUST be exactly one of: "LOW", "MED", "HIGH" (uppercase)
   ```

3. **Example JSON**: Shows concrete values instead of pipe syntax:
   ```json
   {"action": "BUY", "symbol": "AAPL", "confidence": 0.75, ...}
   ```

4. **Action detection**: Fixed case-insensitive matching (`parsed.get("action", "").upper()`)

---

## Fix 3: Tool Usage Logging

**File**: `app/services/trading_agent.py` (new `_log_tool_usage()` function)

### What Changed
Every trading decision now logs to `pipeline_events`:
| Event | Meaning |
|-------|---------|
| `trading_agent:tool_usage` | Decision made WITH research tools — includes tool names + count |
| `trading_agent:no_tools_used` | Decision made WITHOUT any research tools |

### Data Stored
```json
{
  "symbol": "HON",
  "tools_used": ["search_news", "get_technicals_detail"],
  "tools_count": 2,
  "turns_taken": 3
}
```

---

## Fix 4: Repair Rate Tracking in PromptEvolver

**File**: `app/services/PromptEvolver.py`

### What Changed
`_gather_stats()` now queries `pipeline_events` for repair/tool metrics:
| New Metric | Source |
|------------|--------|
| `parse_failures` | Count of `trade_parse:parse_failed` events in last 24h |
| `repair_successes` | Count of `trade_parse:repair_succeeded` events |
| `repair_failures` | Count of `trade_parse:repair_failed` events |
| `forced_holds` | Count of `trade_parse:forced_hold` events |
| `no_tools_decisions` | Count of `trading_agent:no_tools_used` events |
| `tool_decisions` | Count of `trading_agent:tool_usage` events |

The `_EVOLUTION_PROMPT` template now includes these metrics, so the model can consider:
- "If parse failures are high, simplify the output format instructions"
- "If no-tools decisions are high, make the prompt encourage tool usage"

---

## Fix 5: Diagnostics UI

**File**: `app/static/terminal_app.js`

### New Section: Pipeline Events
Added between "Database Table Sizes" and "Cross-Bot Audit Reports" on the diagnostics page:
- Scrollable list (max 300px) of recent pipeline events
- Color-coded icons: ✅ success, ❌ failure, ⚠️ no-tools, 🔧 repair
- Shows symbol, action, confidence, tools used
- Expandable error details for failures

### New API Endpoint
```
GET /api/diagnostics/pipeline-events?limit=30
```

---

## Files Modified

| File | Changes |
|------|---------|
| `trade_action_parser.py` | Full rewrite — debug logging, DB events, confidence normalization |
| `trading_agent.py` | System prompt fix, `_log_tool_usage()`, case-insensitive action matching |
| `PromptEvolver.py` | `_gather_stats()` includes repair/tool metrics, `_count_events()` helper |
| `main.py` | New `/api/diagnostics/pipeline-events` endpoint |
| `terminal_app.js` | Pipeline Events section on diagnostics page |

## Test Results

| Test | Result |
|------|--------|
| Server restart | ✅ Clean startup, no import errors |
| `GET /api/diagnostics/pipeline-events` | ✅ Returns `[]` (no events yet — expected) |
| `GET /api/health` | ✅ `ok` |

## What to Watch On Next Run

When you run all bots again, watch for:
1. **Console logs**: `[TradeActionParser] ❌ Parse FAILED` or `✅ Repair SUCCEEDED` with full error details
2. **Console logs**: `[TradingAgent] X decided BUY after 2 research tool calls: search_news, get_technicals_detail` (should see tools being used now)
3. **Diagnostics page**: Pipeline Events section should populate with color-coded events
4. **If still no tools used**: Problem is deeper than the prompt — may need to restructure how tool descriptions are presented
