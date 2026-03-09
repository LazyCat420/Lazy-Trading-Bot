# Context-Efficient Tool Calling — Anthropic-Style Optimizations

Based on [this video](https://youtu.be/R7OCrqyGMeY) covering Anthropic's tool-calling
optimizations. These build on top of the multi-turn research tools we already have.

## Problem

We currently inject **all 8 research tool descriptions** (~800 tokens) into every
system prompt, even if the LLM never uses them. On a Jetson with limited VRAM and
context, this wastes budget that could be used for actual analysis data.

---

## Phase 1: Tool Search Tool (High Priority)

**Goal:** Replace static tool injection with a meta-tool that loads tool descriptions on-demand.

### What Changes

| File | Change |
|---|---|
| `app/services/research_tools.py` | Add `TOOL_SEARCH_INDEX` — a dict mapping keywords/categories to tool names. Add a `search_tools()` function the LLM can call. |
| `app/services/trading_agent.py` | Replace full `RESEARCH_TOOL_DESCRIPTIONS` in system prompt with a single `search_tools` tool description (~100 tokens vs ~800). LLM calls `search_tools("insider trading")` → gets back only matching tool names + descriptions. |
| `app/services/portfolio_strategist.py` | Same — replace full descriptions with `search_tools` meta-tool. |

### How It Works

```
Turn 1: LLM sees context + ONE meta-tool: search_tools
Turn 2: LLM calls search_tools("technical indicators")
Turn 3: LLM receives: get_technicals_detail, get_price_history (with descriptions)
Turn 4: LLM calls get_technicals_detail({"ticker": "NVDA"})
Turn 5: LLM decides BUY/SELL/HOLD
```

### Token Savings

- **Before:** ~800 tokens for all 8 tool descriptions in every prompt
- **After:** ~100 tokens for `search_tools` description + ~200 tokens for 1-2 loaded tools
- **Savings:** ~60-85% reduction in tool description tokens

### Search Index Design

```python
TOOL_CATEGORIES = {
    "technicals": ["get_technicals_detail", "get_price_history"],
    "fundamentals": ["compare_financials", "fetch_sec_filings"],
    "sentiment": ["search_news", "search_reddit_sentiment"],
    "insider": ["check_insider_activity"],
    "earnings": ["get_earnings_calendar"],
    "news": ["search_news"],
    "institutional": ["fetch_sec_filings", "check_insider_activity"],
    "price": ["get_price_history", "get_technicals_detail"],
}
```

---

## Phase 2: Context Editing (Medium Priority)

**Goal:** Automatically trim old tool call results from the conversation as context fills up.

### What Changes

| File | Change |
|---|---|
| `app/services/trading_agent.py` | Before each LLM call, check total conversation token count. If over 75% budget, trim oldest tool results (keep tool name + summary, drop raw data). |

### How It Works

```
conversation = [system, user_context, tool_call_1, tool_result_1, tool_call_2, tool_result_2]
                                       ↓ trim if over budget ↓
conversation = [system, user_context, "Previously called get_technicals: RSI=65, MACD bullish", tool_call_2, tool_result_2]
```

### Rules
- Never trim the system prompt or original user context
- Never trim the most recent tool result
- Replace trimmed results with a 1-line summary
- Only activates when conversation exceeds 75% of context budget

---

## Phase 3: Memory Tool (Low Priority — Future)

**Goal:** Let the LLM save key findings to a scratchpad that persists across trimming.

### What Changes

| File | Change |
|---|---|
| `app/services/research_tools.py` | Add `save_finding` and `recall_findings` tools |
| `app/services/trading_agent.py` | Maintain a `findings: list[str]` per decision, inject into context |

### How It Works

```
Turn 2: LLM calls save_finding({"note": "NVDA insider buying up 300% in Q4"})
Turn 5: Context gets trimmed, but findings persist
Turn 6: LLM still sees: "FINDINGS: NVDA insider buying up 300% in Q4"
```

> [!NOTE]
> This is lowest priority because our max 4 tool calls rarely fills context.
> Only becomes critical if we increase `_MAX_RESEARCH_TURNS` significantly.

---

## Implementation Order

1. **Phase 1** (Tool Search Tool) — Biggest bang for buck on Jetson. Saves context immediately.
2. **Phase 2** (Context Editing) — Safety net for long research chains.
3. **Phase 3** (Memory Tool) — Only if we increase research depth beyond 4 turns.

## Verification

- Token counting before/after Phase 1 to measure actual savings
- Run the bot with `search_tools` and verify it still makes good decisions
- Compare audit logs: tool usage patterns, confidence scores, decision quality
