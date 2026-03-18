# Pipeline Issues Report — First Full Run Audit

> Generated from nemotron-3-nano:latest run (5026.9s), audited by gpt-oss-safeguard:20b (scored 5.2/10)

---

## Issue 1: Trade Repair Logic (ITT Repair Attempt)

### What Happened
```
[TradingAgent] Turn 1/5 for ITT: got 522 chars
[TradingAgent] ITT decided BUY (no research tools used)
[TradeActionParser] Repair attempt 1/1 for ITT
```

### How Repair Works
File: [`trade_action_parser.py`](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trade_action_parser.py)

```
Step 1: LLMService.clean_json_response() strips markdown fences
Step 2: json.loads() → Pydantic TradeAction.model_validate()
Step 3: If validation fails → send broken JSON + schema to LLM for fixing
Step 4: Re-parse the repaired JSON
Step 5: If all repairs fail → force HOLD with confidence=0.0
```

### Root Cause
The LLM output for ITT was valid JSON with the right fields, **but** Pydantic validation failed — most likely:
- A field had the wrong type (e.g. `confidence: "high"` instead of `confidence: 0.8`)
- `action` was lowercase or had extra text (e.g. `"action": "buy"` instead of `"BUY"`)
- Missing a required field like `rationale`

### Problem
- **Only 1 repair attempt** (`max_repairs=1`) — should it be more?
- **We can't see what broke** — the parser only logs "Repair attempt 1/1" but doesn't log WHAT validation error occurred or WHAT the broken JSON looked like
- **No logging of the repair result** — did the repair succeed or fail? The log shows the repair attempt but not the outcome

### Questions to Decide
1. Should we increase `max_repairs` to 2-3?
2. Should we log the actual validation error and broken JSON for debugging?
3. Should the logic loops track repair failure rates as a performance metric?

---

TESTING UP TO HERE HAVEN"T IMPLEMENTED BELOW YET.  

## Issue 2: Multi-Turn Tool System ("max 4 tools")

### How It Works
File: [`trading_agent.py`](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py) (line 123: `_MAX_RESEARCH_TURNS = 4`)

```
Turn 1: LLM sees context → decides to call a tool OR make a decision
Turn 2: Gets tool result → decides to call another tool OR decide
Turn 3: Same...
Turn 4: Same...
Turn 5: FORCED to make a decision (no more tools allowed)
```

### Key Facts
| Question | Answer |
|----------|--------|
| **Who decides how many tools?** | The **LLM decides**. It can use 0-4 tools. If it has enough data it decides immediately (Turn 1) |
| **Are tools sequential or parallel?** | **Sequential** — one tool per turn, result fed back before next |
| **What if it uses too many?** | After 4 tool calls, it's forced: "You MUST now output your final trading decision" |
| **What happened with HON?** | Log says "max 4 tools" — that's just logging the cap, not that all 4 were used |
| **Can we see which tools were used?** | **No** — this is logged to the Python console but NOT visible on the diagnostics page |

### Available Tools (11 total)
| Tool | Purpose |
|------|---------|
| `search_tools` | **Meta-tool**: search for other tools by category |
| `fetch_sec_filings` | Hedge fund / institutional 13F holdings |
| `search_news` | Recent news headlines and summaries |
| `get_technicals_detail` | RSI, MACD, Bollinger, ADX, Ichimoku, Fibonacci |
| `check_insider_activity` | Insider + congressional trading |
| `compare_financials` | Side-by-side P/E, margins, growth |
| `get_price_history` | OHLCV data for last N days |
| `search_reddit_sentiment` | Reddit mentions, sentiment scores |
| `get_earnings_calendar` | Earnings dates, estimates, surprises |
| `save_finding` | Memory: save a key data point to scratchpad |
| `recall_findings` | Memory: recall saved findings |

### Problem
- **No visibility** into which tools were called, what they returned, or how long each took
- The LLM might be wasting tool calls (e.g. calling `search_tools` meta-tool instead of directly calling the tool it needs)
- Context trimming happens silently — old tool results get replaced with 1-line summaries
- `_MAX_RESEARCH_TURNS = 4` is hardcoded — some tickers might benefit from more research, others need less

### Questions to Decide
1. Should we log tool usage to the database for the diagnostics page?
2. Should `_MAX_RESEARCH_TURNS` be configurable per-bot or per-ticker?
3. Should we expose tool descriptions directly instead of the `search_tools` meta-tool? (saves 1 tool call)

---

## Issue 3: Cross-Audit Accuracy

### The Audit Result
Auditor: `gpt-oss-safeguard:20b` → Audited: `nemotron-3-nano:latest` → **Score: 5.2/10**

| Category | Score | Accurate? | Notes |
|----------|-------|-----------|-------|
| Data Coverage | 6/10 | ✅ Fair | Found exactly 10 tickers (threshold is >5 good, >10 great) — 6 is reasonable |
| Extraction Quality | 7/10 | ⚠️ Can't verify | Says "2 non-US or ETF tickers" — **we don't know if this is true** because the auditor doesn't have access to the actual ticker list |
| Analysis Depth | 6/10 | ✅ Fair | "Catalytic events and risk factors largely absent" — reasonable assessment |
| Trading Decisions | 4/10 | ✅ Accurate | Only 2 trades with -$0.07 P&L — weak and indecisive is fair |
| Risk Management | 2/10 | ✅ Accurate | No position sizing or risk limits visible — correct critique |
| Prompt Quality | 5/10 | ✅ Fair | "Extraction prompts somewhat verbose, trading prompt lacks specificity" |

### Fundamental Problem with the Audit
The auditor only gets **aggregate numbers** (10 tickers, 12 analyzed, 2 trades, -$0.07 P&L). It does NOT get:
- The **actual list of tickers** discovered (so it can't verify if they're valid)
- The **dossier content** (so it can't judge analysis depth from actual data)
- The **trade rationale** (so it can't evaluate if decisions were well-reasoned)
- The **actual prompts used** (so prompt quality scoring is speculation)

> **The audit is scoring based on metadata, not the actual work product.**

### Questions to Decide
1. Should the auditor receive the actual ticker list, trade rationales, and dossier samples?
2. Should we store a "run summary" that captures the actual work product for audit?
3. How much data should we feed the auditor without blowing the context window?

---

## Issue 4: Prompt Evolution Skipped ("not enough data")

### What Happened
```
🧬 No prompt evolution (not enough data)
```

### Root Cause
File: [`PromptEvolver.py`](file:///home/braindead/github/Lazy-Trading-Bot/app/services/PromptEvolver.py) line 159:

```python
if stats["transcripts_processed"] < 3:
    return None  # Skip evolution
```

The threshold checks `youtube_trading_data` table rows from the last 24 hours. If fewer than 3 YouTube transcripts were processed, evolution is skipped. **This has nothing to do with the audit report.**

### Why This Is Wrong
1. **The audit report data is completely disconnected from the evolution system.** The cross-bot auditor writes to `bot_audit_reports`, but `PromptEvolver._gather_stats()` reads from `youtube_trading_data`, `orders`, and `ticker_scores`. They never overlap.
2. **The threshold of 3 transcripts is arbitrary.** If the bot processed 2 excellent transcripts and extracted 15 tickers, it should still evolve.
3. **The audit gave 2/10 on risk management** — this critical feedback is being IGNORED by the evolution system because it doesn't read audit reports at all.

### The Feedback Loop Today
```
Bot runs → PromptEvolver reads DB stats → model self-evaluates → evolves prompts
                                          ↑ DOOM LOOP (same model checking itself)
                                          
Bot runs → CrossBotAuditor scores work → writes to bot_audit_reports
                                          ↑ DATA IS IGNORED (evolution doesn't read it)
```

### What It SHOULD Be
```
Bot runs → CrossBotAuditor scores → PromptEvolver reads AUDIT scores → evolves
                                     (external feedback, not self-eval)
```

### Questions to Decide
1. Should PromptEvolver use cross-audit scores instead of (or in addition to) self-eval?
2. Should the evolution threshold be based on audit scores instead of transcript count?
3. Should evolution be triggered by low audit scores rather than arbitrary thresholds?

---

## Summary: Priority Order

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 4 | **Prompt evolution ignores audit data** | 🔴 Critical | Medium — wire audit scores into PromptEvolver |
| 3 | **Audit lacks actual work product data** | 🟠 High | Medium — enrich audit context with ticker list, rationales |
| 2 | **No tool usage visibility** | 🟡 Medium | Small — log tool calls to DB, show on diagnostics |
| 1 | **Repair logging** | 🟢 Low | Small — add debug logging for validation errors |
