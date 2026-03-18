# Prism / Iris Workflow Audit Report

> Audited: 2026-03-15 · Scope: `autonomous_loop.py`, `WorkflowService.py`, `llm_service.py`, `deep_analysis_service.py`, `trading_agent.py`, `trading_pipeline_service.py`

---

## Executive Summary

The original plan correctly identifies that **non-LLM phases pollute Prism workflows** and that **dict dumps replace real prompts**. However, it contains one major factual error: `deep_analysis_service.py` makes **zero LLM calls** — it's pure math. The only real LLM calls happen in `trading_agent.py`. Additionally, the plan overlooks a key fact: **Prism already sees every LLM call** via the chat proxy, so the problem is narrower than described.

---

## Finding 1: Non-LLM Phases Pollute Workflows ✅ CONFIRMED

**Original claim**: The loop posts every phase to Prism — including `collection`, `embedding`, `import`, `discovery` — as if they were LLM calls.

**Evidence**: Both `run_full_loop()` and `run_llm_only_loop()` use identical logic:

```python
# autonomous_loop.py L291 (full loop) and L613 (LLM-only loop)
for phase_name, phase_data in report.get("phases", {}).items():
    tracker.add_step(
        model=self.model_name or settings.LLM_MODEL,
        label=phase_name.replace("_", " ").title(),
        system_prompt=f"Phase: {phase_name}",
        user_input=str(phase_data)[:500],
        output=str(phase_data)[:500],
        duration=phase_data.get("seconds", 0) if isinstance(phase_data, dict) else 0,
    )
```

**Impact**: In `run_full_loop()`, the phases dict contains: `discovery`, `import`, `collection`, `embedding`, `analysis`, `trading`. All six get posted to Prism as fake LLM steps.

In `run_llm_only_loop()`, the phases dict contains: `import`, `analysis`, `trading`. The `import` phase is still non-LLM.

> **Ticket 1 is correct.** Strip `collection`, `embedding`, `import`, and `discovery` from the tracker loop.

---

## Finding 2: Dict Dumps Replace Real Prompts ✅ CONFIRMED

**Original claim**: `tracker.add_step()` receives `str(phase_data)[:500]` which is a Python dict cast to a string, not real prompts.

**Evidence**: The `system_prompt` field is always `f"Phase: {phase_name}"` — a static label. The `user_input` and `output` fields are both `str(phase_data)[:500]`, which produces strings like:

```
{'analyzed': 8, 'total': 8, 'results': [{'ticker': 'NVDA', 'conviction': 0.65, 'si
```

This is a Python dict repr, not an LLM prompt or response.

> **Ticket 2's diagnosis is correct** — dict dumps are being posted. However, the fix is wrong (see Finding 3 below).

---

## Finding 3: `deep_analysis_service.py` Has ZERO LLM Calls ⚠️ PLAN IS WRONG

**Original claim**: "In `deep_analysis_service.py`, after each LLM call completes, call `tracker.add_step()`..."

**Evidence**: The file's own docstring on line 9 explicitly states:

```python
"""
Now uses zero LLM calls — pure math + pure Python pre-analysis.
"""
```

The entire `analyze_ticker()` method is:
1. `QuantSignalEngine.compute()` — pure math (RSI, MACD, Sharpe, etc.)
2. `DataDistiller.distill_*()` — pure Python string formatting
3. `_compute_conviction()` — arithmetic conviction score
4. `_store_dossier()` — DuckDB write

There is **no** `LLMService.chat()` call, no `await _llm.chat()`, no Ollama interaction whatsoever. Searching the file for `tracker`, `llm`, `chat`, `LLMService` returns zero results.

> **⚠️ Ticket 2 must be revised.** You cannot add `tracker.add_step()` to `deep_analysis_service.py` because there are no LLM calls to track. The deep analysis phase should be stripped from Prism workflows entirely — it is data processing, not LLM work.

---

## Finding 4: Prism Already Sees Every LLM Call Via Chat Proxy ⚠️ MISSING FROM PLAN

**Discovery**: The plan never mentions this, but `LLMService._send_prism_request()` (llm_service.py L353-661) routes **all** LLM requests through Prism's `/chat?stream=false` endpoint. Every call:

1. Generates a unique `conversationId` (L429)
2. Includes `conversationMeta` with title, system prompt, model, temperature (L440-453)
3. Includes `userMessage` with the full user content (L462-465)

This means **Prism/Iris already has complete visibility into every LLM call** at the conversation level — including the real system prompt, the real user context, and the real response. The `audit_ticker` and `audit_step` metadata are used to title these conversations (e.g., `"NVDA — trading_decision_turn_0"`).

> The Workflow API (`POST /workflows`) is a **separate, higher-level concept** from the per-call chat proxy. The workflow is meant to group related conversations into a visual pipeline graph. **The individual LLM calls are NOT missing from Prism — they're just not linked to a workflow.**

---

## Finding 5: `WorkflowTracker` Is Never Passed Into Any Service ✅ CONFIRMED

**Original claim**: "Pass the `WorkflowTracker` instance **into** these services so they can record steps directly."

**Evidence**: Searched for `tracker` in all three service files:

| File | Occurrences of `tracker` |
|------|--------------------------|
| `deep_analysis_service.py` | 0 |
| `trading_agent.py` | 0 |
| `trading_pipeline_service.py` | 0 |

The `WorkflowTracker` is only instantiated at autonomous_loop.py L287 and L609, used locally, and never passed to any other service.

---

## Finding 6: One Workflow Per Loop vs Per Ticker ✅ CONFIRMED

**Original claim**: One giant workflow covers all tickers, making it impossible to drill into a specific stock.

**Evidence**: The tracker is created once per loop run:

```python
# L287-289
tracker = WorkflowTracker(
    title=f"Full Pipeline — {self.bot_id} ({self.model_name})",
    source="lazy-trading-bot",
)
```

All phase results (regardless of how many tickers were processed) are flattened into this single workflow. In Iris, you see one blob titled `"Full Pipeline — default (granite3.2:8b)"` with no way to drill into individual tickers.

> **Ticket 3 is correct.** One workflow per ticker would massively improve Iris usability.

---

## Revised Ticket Plan

### Ticket 1: Strip Non-LLM Phases ✅ No changes needed from original plan

**File**: `autonomous_loop.py`

In both `run_full_loop()` (L291) and `run_llm_only_loop()` (L613), filter the for-loop to only include `trading` — the sole phase that actually invokes the LLM. The `analysis` phase (deep analysis) is **pure math** and should be excluded.

```python
LLM_PHASES = {"trading"}  # Only phases that call the LLM

for phase_name, phase_data in report.get("phases", {}).items():
    if phase_name not in LLM_PHASES:
        continue
    tracker.add_step(...)
```

### Ticket 2: Post Real LLM I/O ⚠️ REVISED — Only `trading_agent.py` needs changes

Since `deep_analysis_service.py` makes zero LLM calls, only `trading_agent.py` needs to be wired up:

**File**: `trading_agent.py`

Pass a `WorkflowTracker` instance into `TradingAgent.decide()`. After the LLM returns a trade decision (L466-486), call `tracker.add_step()` with the **actual** system prompt, user prompt, and raw LLM response that are already available as local variables:

- `system_prompt` → the `_SYSTEM_PROMPT` template (L158-252)
- `user_input` → the `user_prompt` from `_build_prompt()` (L282)
- `output` → the `final_raw` LLM response text (L467)

**File**: `trading_pipeline_service.py`

Accept and forward the `WorkflowTracker` from `autonomous_loop.py` → `_process_ticker()` → `TradingAgent.decide()`.

### Ticket 3: One Workflow Per Ticker ✅ No changes needed from original plan

**File**: `autonomous_loop.py` + `trading_pipeline_service.py`

Create one `WorkflowTracker` per ticker inside `_process_ticker()` and post it after that ticker's LLM decision completes. Title: `"$NVDA — Trade Decision"`.

### Optional Ticket 4: Link Chat Conversations to Workflows

Since Prism already has every LLM call as a conversation (via the chat proxy), the `conversationId` generated at llm_service.py L429 could be captured and linked to the per-ticker workflow via `WorkflowTracker._link_conversations()`. This would let you click a workflow step in Iris and jump directly to the full conversation with real prompts.

---

## Acceptance Criteria (Revised)

1. ✅ Prism/Iris workflows contain **only** trading LLM steps — no collection, embedding, discovery, import, or deep analysis.
2. ✅ Each ticker appears as its own workflow with the real user prompt and raw LLM response.
3. ✅ The `analysis` (deep analysis) phase is **excluded** from workflows because it makes zero LLM calls.
4. ✅ The bot's activity log (DuckDB/WebSocket) continues unchanged.
5. ✅ If the LLM is never called for a ticker, no workflow appears.
6. 🆕 Conversation IDs from `_send_prism_request` are linked to their parent workflow for drill-through.
