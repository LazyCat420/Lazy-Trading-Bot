# 02 — Engine Folder Refactor

## Current State

```
app/engine/
├── __init__.py
├── aggregator.py            (114 lines) — Pools 4 agent reports into PooledAnalysis
├── data_distiller.py        (581 lines) — Pure-Python data pre-analysis (chart patterns, valuations, risk)
├── dossier_synthesizer.py   (267 lines) — Layer 4: LLM synthesizes QuantScorecard + QAPairs → TickerDossier
├── portfolio_strategist.py  (1023 lines) — LLM tool-calling agent that makes ALL trading decisions
├── quant_signals.py         (882 lines) — Layer 1: Pure math signal engine (Sharpe, Sortino, VaR, Minervini, Hurst, etc.)
├── question_generator.py    (193 lines) — Layer 2: LLM generates 5 follow-up questions from scorecard
├── rag_engine.py            (377 lines) — Layer 3: BM25 search on DuckDB text → LLM answer extraction
├── rules_engine.py          (134 lines) — LLM evaluates PooledAnalysis against user strategy → FinalDecision
├── signal_router.py         (254 lines) — Converts dossier conviction into sized trade orders (threshold-based)
├── strategist_audit.py      (226 lines) — Logs every strategist LLM turn for debugging
```

## What Each File Does

| File | LLM Calls | Role |
|------|-----------|------|
| `quant_signals.py` | 0 | Pure math: Z-scores, trend template, VCP, Hurst, Piotroski, Altman Z, momentum |
| `data_distiller.py` | 0 | Pure Python: detects crossovers, divergences, support/resistance, formats data for LLM |
| `question_generator.py` | 1 | Takes QuantScorecard → LLM generates 5 follow-up questions |
| `rag_engine.py` | 5 | For each question, searches DuckDB text data → LLM extracts answer |
| `dossier_synthesizer.py` | 1 | Takes scorecard + QAPairs → LLM produces TickerDossier (summary, bull/bear case) |
| `aggregator.py` | 0 | Packages 4 agent reports into PooledAnalysis container |
| `rules_engine.py` | 1 | Takes PooledAnalysis + user strategy → LLM decides BUY/HOLD/SELL |
| `signal_router.py` | 0 | Converts dossier conviction_score into trade order via hardcoded thresholds |
| `portfolio_strategist.py` | 1 loop (5-15 turns) | Tool-calling LLM that sees ALL dossiers + portfolio → places trades |
| `strategist_audit.py` | 0 | Logs every strategist turn for debugging |

### The Problems

1. **Two competing decision paths exist simultaneously:**
   - **Path A (Old):** 4 Agents → Aggregator → RulesEngine → FinalDecision → SignalRouter → PaperTrader
   - **Path B (New):** DeepAnalysis (Quant → Questions → RAG → Dossier) → PortfolioStrategist → PaperTrader

   Path A is the `pipeline_service.py` flow. Path B is the `autonomous_loop.py` flow. They overlap and compete.

2. **The 4-Layer Deep Analysis Funnel is overkill:**
   - Layer 1 (QuantSignals): **Valuable** — pure math, zero LLM calls
   - Layer 2 (QuestionGenerator): **Wasteful** — 1 LLM call to ask questions that the strategist could ask itself
   - Layer 3 (RAGEngine): **Wasteful** — 5 LLM calls to answer those questions by searching DuckDB
   - Layer 4 (DossierSynthesizer): **Wasteful** — 1 LLM call to synthesize everything into prose

   Total: **7 LLM calls per ticker** just to build a dossier. With 10 tickers on the watchlist, that's **70 LLM calls** before trading even starts.

3. **`signal_router.py` is dead code.** The `PortfolioStrategist` has its own position-sizing logic built into `_tool_place_buy()`. The `SignalRouter` hardcoded thresholds are never reached in the current `autonomous_loop.py` flow.

4. **`rules_engine.py` is redundant with the strategist.** Both evaluate all data and produce a trade decision. The strategist is strictly more powerful (it can request data, compare tickers, and execute trades in a loop).

5. **`aggregator.py` is trivial.** It's a 25-line class that just stores 4 optional report fields. It exists only because the old multi-agent pipeline needed a container.

---

## Proposed Refactor

### Goal: Keep what works (quant math, data distiller, strategist), delete the redundant layers (question gen, RAG, dossier synthesis, aggregator, rules engine, signal router)

### What We Keep (move to `app/services/`)

| File | Keep As | Reason |
|------|---------|--------|
| `quant_signals.py` | `app/services/quant_engine.py` | 882 lines of pure math — irreplaceable |
| `data_distiller.py` | `app/services/data_distiller.py` | Pure Python pre-analysis — saves LLM context |
| `portfolio_strategist.py` | `app/services/portfolio_strategist.py` | The ONE decision-maker — keeps tool-calling |
| `strategist_audit.py` | `app/services/strategist_audit.py` | Debugging tool — essential for diagnosing bad trades |

### What We Delete

| File | Action | Reason |
|------|--------|--------|
| `question_generator.py` | **DELETE** | The strategist can ask its own questions via `get_dossier` tool |
| `rag_engine.py` | **DELETE** | Data is already in dossiers; strategist requests what it needs |
| `dossier_synthesizer.py` | **DELETE** | Replace with a simpler data-packaging function (no LLM call needed) |
| `aggregator.py` | **DELETE** | No more multi-agent reports to pool |
| `rules_engine.py` | **DELETE** | Strategist IS the rules engine now |
| `signal_router.py` | **DELETE** | Strategist has its own position-sizing in `_tool_place_buy()` |
| `__init__.py` | **DELETE** | Folder removed entirely |

### What Changes in the PortfolioStrategist

The strategist already works. The key change is what data it receives:

**Before:** Gets prose `TickerDossier` (executive summary, bull/bear case) from the 4-layer funnel  
**After:** Gets `QuantScorecard` + `DataDistiller` output directly — structured numbers + pre-computed text analysis

The strategist's `_tool_get_dossier()` will be updated to return:

```
QUANT SCORECARD:
  Trend Template: 85/100 (STRONG)
  VCP Setup: 72/100 (MODERATE)
  RS Rating: 91/100
  Sharpe: 1.34, Sortino: 2.1, MaxDD: -12%
  Flags: [volume_surge, golden_cross_3d_ago]

DISTILLED ANALYSIS:
  [output from data_distiller.distill_price_action()]
  [output from data_distiller.distill_fundamentals()]
  [output from data_distiller.distill_risk()]
  [output from data_distiller.distill_sentiment()] ← NEW
```

This is **pure data, zero LLM calls** to prepare. The strategist LLM does the interpretation.

### New Pipeline Flow

```
OLD (7 LLM calls per ticker):
  Quant(0) → QuestionGen(1) → RAG(5) → DossierSynth(1) → Strategist(loop)

NEW (0 LLM calls per ticker, 1 strategist loop for ALL tickers):
  Quant(0) → DataDistiller(0) → Strategist(loop with tools)
```

---

## Step-by-Step Execution Order

1. Move `quant_signals.py` → `app/services/quant_engine.py` (rename for clarity)
2. Move `data_distiller.py` → `app/services/data_distiller.py`
3. Move `portfolio_strategist.py` → `app/services/portfolio_strategist.py`
4. Move `strategist_audit.py` → `app/services/strategist_audit.py`
5. Update `portfolio_strategist.py` `_tool_get_dossier()` to build data from `QuantScorecard` + `DataDistiller` directly instead of reading prose dossiers from DuckDB
6. Update `deep_analysis_service.py` to only run `QuantSignalEngine` + `DataDistiller` (remove QuestionGen, RAG, DossierSynth steps)
7. Update `autonomous_loop.py` `_do_deep_analysis()` to use simplified pipeline
8. Delete `question_generator.py`, `rag_engine.py`, `dossier_synthesizer.py`, `aggregator.py`, `rules_engine.py`, `signal_router.py`
9. Delete the `app/engine/` folder entirely
10. Update all imports across codebase
11. Delete `prompts/decision_maker.md` (rules engine prompt — no longer needed)

## Files Affected

- **DELETE:** `app/engine/` (entire folder, 12 files)
- **DELETE:** `app/prompts/decision_maker.md`
- **MOVE:** 4 files from `engine/` to `services/`
- **MODIFY:** `app/services/deep_analysis_service.py`
- **MODIFY:** `app/services/autonomous_loop.py`
- **MODIFY:** `app/services/pipeline_service.py`
- **MODIFY:** `tests/test_portfolio_strategist.py`
- **MODIFY:** `tests/test_strategist_audit.py`
- **MODIFY:** `tests/test_data_pipeline.py`
