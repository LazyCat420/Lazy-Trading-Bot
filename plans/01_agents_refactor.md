# 01 — Agents Folder Refactor

## Current State

```
app/agents/
├── __init__.py
├── base_agent.py          (406 lines) — Abstract LLM agent with retry/rescue/fallback logic
├── technical_agent.py     (287 lines) — Formats technicals → LLM → TechnicalReport
├── fundamental_agent.py   (203 lines) — Formats fundamentals → LLM → FundamentalReport
├── sentiment_agent.py     (121 lines) — Formats news/transcripts → LLM → SentimentReport
├── risk_agent.py          (168 lines) — Formats risk metrics → LLM → RiskReport
```

## What Each File Does

| File | Role | LLM Calls | Why It Exists |
|------|------|-----------|---------------|
| `base_agent.py` | Abstract class: loads `.md` prompt, calls LLM, retries on bad JSON, rescues broken schema, builds fallback reports | 1-3 per `analyze()` | Shared plumbing for all agents |
| `technical_agent.py` | `format_context()` serializes 6 months of technicals + quant scorecard into text for the LLM | 1 (via base) | Interprets chart data |
| `fundamental_agent.py` | `format_context()` serializes financials, balance sheet, cash flow, analysts, insider data | 1 (via base) | Interprets company health |
| `sentiment_agent.py` | `format_context()` serializes news articles + YouTube transcripts with budget-aware truncation | 1 (via base) | Interprets market sentiment |
| `risk_agent.py` | `format_context()` serializes quantitative risk metrics (Sharpe, VaR, drawdown, beta) | 1 (via base) | Interprets risk profile |

### The Problem

1. **Each agent is just a `format_context()` wrapper.** They all inherit from `BaseAgent`, which does the actual LLM work. The subclasses are 70-90% data formatting code, not agent logic.
2. **4 separate LLM calls per ticker.** For each ticker on the watchlist, the pipeline fires 4 independent LLM calls (technical, fundamental, sentiment, risk), then pools them via `Aggregator`, then sends them to `RulesEngine` for a 5th LLM call. That's **5 LLM calls** before a single trade decision is made.
3. **The PortfolioStrategist makes them redundant.** The current pipeline ALSO has a `PortfolioStrategist` (in `engine/`) that gets ALL dossiers and uses tool-calling to decide trades. So the 4 agents produce reports that get pooled → rules engine → decision, but the strategist independently gets dossiers and decides trades. **Two competing decision paths.**
4. **Each agent has its own Pydantic output model** (`TechnicalReport`, `FundamentalReport`, `SentimentReport`, `RiskReport`) with complex schemas the LLM frequently fails to match, triggering rescue/fallback logic in `base_agent.py`.

### Dependencies (Who Imports These)

- `pipeline_service.py` instantiates all 4 agents and calls `agent.analyze(ticker, context)`
- `aggregator.py` pools the 4 reports into `PooledAnalysis`
- `rules_engine.py` evaluates the pooled analysis
- `prompts/` folder has matching `.md` prompt templates per agent

---

## Proposed Refactor

### Goal: Delete the 4 specialist agents. Merge their data-formatting into the data distiller. Let the PortfolioStrategist be the ONLY decision-maker

### What We Keep

- **`base_agent.py` logic** — The retry/rescue/JSON-cleaning logic is battle-tested. Extract the LLM-calling utilities into `llm_service.py` so they're reusable without the agent abstraction.
- **Data formatting code** — The `format_context()` methods contain useful serialization logic. Move this into `data_distiller.py` (already exists in `engine/`) so raw data gets pre-formatted before reaching the strategist.

### What We Delete

| File | Action | Reason |
|------|--------|--------|
| `technical_agent.py` | **DELETE** | `format_context()` → move to `data_distiller.py` |
| `fundamental_agent.py` | **DELETE** | `format_context()` → move to `data_distiller.py` |
| `sentiment_agent.py` | **DELETE** | `format_context()` → move to `data_distiller.py` |
| `risk_agent.py` | **DELETE** | `format_context()` → move to `data_distiller.py` |
| `base_agent.py` | **DELETE** | Extract JSON rescue utilities to `llm_service.py` |
| `__init__.py` | **DELETE** | Folder removed entirely |
| `prompts/technical_analysis.md` | **DELETE** | No longer needed (agent deleted) |
| `prompts/fundamental_analysis.md` | **DELETE** | No longer needed |
| `prompts/sentiment_analysis.md` | **DELETE** | No longer needed |
| `prompts/risk_assessment.md` | **DELETE** | No longer needed |

### What We Modify

| File | Change |
|------|--------|
| `engine/data_distiller.py` | Add `distill_sentiment()` and `distill_full_context()` methods that combine all the `format_context()` output into a single text blob for the strategist |
| `services/llm_service.py` | Add `clean_json_with_rescue()` (ported from `base_agent._diagnose_response` + `_try_unwrap_nested`) |
| `services/pipeline_service.py` | Remove all agent imports, remove agent execution blocks, remove `Aggregator` + `RulesEngine` calls |
| `services/deep_analysis_service.py` | No agent calls — only quant + dossier synthesis |

### New Data Flow (After Refactor)

```
OLD:  Raw Data → 4 Agents (4 LLM calls) → Aggregator → RulesEngine (1 LLM call) → Decision
                                                         ↓ (also)
                Raw Data → DeepAnalysis (3 LLM calls) → PortfolioStrategist (1 LLM loop)

NEW:  Raw Data → DataDistiller (0 LLM calls, pure Python) → PortfolioStrategist (1 LLM loop with tools)
```

**Net effect:** Goes from **8+ LLM calls per ticker** down to **1 strategist loop** that can request specific data via tool calls.

---

## Step-by-Step Execution Order

1. Port `base_agent.py` JSON rescue utilities (`_diagnose_response`, `_try_unwrap_nested`, `_build_rescue_prompt`) into `llm_service.py` as static helpers.
2. Add `distill_sentiment()` to `data_distiller.py` using logic from `sentiment_agent.format_context()`.
3. Add `distill_full_context()` to `data_distiller.py` that combines all distill outputs into one text blob.
4. Update `pipeline_service.py` to remove agent imports and calls.
5. Delete the `app/agents/` folder entirely.
6. Delete the 4 agent prompt files from `app/prompts/`.
7. Update all test files that import from `app.agents`.

## Files Affected

- **DELETE:** `app/agents/` (entire folder, 6 files)
- **DELETE:** `app/prompts/technical_analysis.md`, `fundamental_analysis.md`, `sentiment_analysis.md`, `risk_assessment.md`
- **MODIFY:** `app/engine/data_distiller.py`
- **MODIFY:** `app/services/llm_service.py`
- **MODIFY:** `app/services/pipeline_service.py`
- **MODIFY:** `tests/test_agent_unwrap.py` (or delete if fully covered)
