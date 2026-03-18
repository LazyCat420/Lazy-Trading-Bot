# Refactor Prism Workflow Integration

Fix what gets posted to Prism/Iris so it only shows **real LLM activity** with **real prompts and responses**, organized **per ticker**.

## Evidence (13/13 Tests Pass)

All claims verified via [test_prism_audit_claims.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_prism_audit_claims.py):

| Claim | Test | Result |
|-------|------|--------|
| `deep_analysis_service.py` has zero LLM calls | AST scan for `.chat()` + `LLMService` imports | ✅ PASS |
| `trading_agent.py` is the sole LLM caller | AST scan confirms `LLMService` import + `.chat()` at L331 | ✅ PASS |
| `WorkflowTracker` never passed to services | Source scan of 3 service files | ✅ PASS |
| All phases posted unfiltered to Prism | Pattern match on `report.get("phases")` loop | ✅ PASS |
| Dict dumps used instead of real prompts | Pattern match on `str(phase_data)[:500]` | ✅ PASS |

## User Review Required

> [!IMPORTANT]
> **`deep_analysis_service.py` makes zero LLM calls.** Its own docstring states: *"Now uses zero LLM calls — pure math + pure Python pre-analysis."* The `analysis` phase should NOT appear in Prism workflows. The only LLM call is in `trading_agent.py` at L331.

> [!WARNING]
> The `WorkflowTracker` is currently a **fire-and-forget** mechanism at end-of-loop. This refactor moves it to per-ticker, real-time posting inside `_process_ticker()`. If Prism is down, per-ticker posting will fail silently per the existing `try/except` pattern — no pipeline disruption.

---

## Proposed Changes

### Component 1: Strip Non-LLM Phases from WorkflowTracker

#### [MODIFY] [autonomous_loop.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/autonomous_loop.py)

**Both `run_full_loop()` (L284-305) and `run_llm_only_loop()` (L606-627):**

Remove the entire `for phase_name, phase_data in report.get("phases").items()` block that blindly posts every phase. Replace with nothing — the per-ticker workflow is now posted inside `_process_ticker()` (see Component 2).

The `WorkflowTracker` import stays (it's still used, just from a different location).

---

### Component 2: Per-Ticker Workflow with Real LLM I/O

#### [MODIFY] [trading_pipeline_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_pipeline_service.py)

1. Add `from app.services.WorkflowService import WorkflowTracker` import
2. In `_process_ticker()` (L192), after the `TradingAgent.decide()` call (L211) returns `(action, raw_llm)`:
   - Create a `WorkflowTracker(title=f"${ticker} — Trade Decision")` per ticker
   - Call `tracker.add_step()` with the **real data** from the `TradingAgent.decide()` call
   - Post the workflow via `await tracker.post_workflow()`

The `decide()` method returns `(action, raw_llm)` — where `raw_llm` is the actual LLM response text. The system prompt and user prompt are currently built inside `decide()` and not returned, so we also need to expose them.

#### [MODIFY] [trading_agent.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py)

1. Change `decide()` return type from `tuple[TradeAction, str]` to `tuple[TradeAction, str, dict]`
2. Return a third element `llm_meta` dict containing:
   - `"system_prompt"`: the `system_prompt` variable (L286)
   - `"user_prompt"`: the `user_prompt` variable (L282)
   - `"turns"`: number of multi-turn iterations
   - `"tools_used"`: list of research tools called
   - `"conversation_ids"`: collected from `_llm.chat()` (requires exposing from `LLMService`)

This gives `_process_ticker()` everything it needs to build an accurate Prism workflow step.

---

### Component 3: Link Prism Chat Conversations to Workflows

#### [MODIFY] [llm_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/llm_service.py)

1. Change `chat()` return type from `str` to `tuple[str, str]` — `(content, conversation_id)`
2. Return the `conversation_id` generated at L429 alongside the response content
3. All callers that destructure `content = await _llm.chat(...)` need updating

#### [MODIFY] [trading_agent.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py)

1. Capture `conversation_id` from each `_llm.chat()` call at L331
2. Collect all conversation IDs into the `llm_meta` return dict

Then in `trading_pipeline_service.py`, pass these conversation IDs to `tracker.conversation_ids` so `_link_conversations()` connects them to the per-ticker workflow.

---

## Verification Plan

### Automated Tests

**Run command:** `source venv/bin/activate && python -m pytest tests/test_prism_audit_claims.py tests/test_trading_pipeline_service.py tests/test_workflow_service.py -v -p no:capture`

1. **Existing tests** — [test_prism_audit_claims.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_prism_audit_claims.py) (13 tests confirming the bugs exist pre-refactor)
2. **Existing tests** — [test_workflow_service.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_workflow_service.py) (tests for `WorkflowTracker.add_step()` and `post_workflow()`)
3. **Existing tests** — [test_trading_pipeline_service.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_trading_pipeline_service.py) (tests for `_build_context()`)

**New tests to add:**

4. `test_autonomous_loop_no_non_llm_phases` — Assert that the tracker loop in `run_full_loop()` no longer iterates `discovery`, `import`, `collection`, `embedding`, or `analysis` phases
5. `test_process_ticker_posts_per_ticker_workflow` — Mock `WorkflowTracker` and assert `_process_ticker()` creates one tracker per ticker with real prompt/response data
6. `test_trading_agent_returns_llm_meta` — Assert `decide()` returns the 3-tuple with `system_prompt`, `user_prompt`, `tools_used`
7. `test_llm_service_returns_conversation_id` — Assert `chat()` returns `(content, conversation_id)` tuple

### Mutation Testing

```bash
source venv/bin/activate && mutmut run --paths-to-mutate app/services/autonomous_loop.py
```

Scoped to autonomous_loop.py since that's where the tracker logic lives.
