# Phase 2 Cleanup — Leftover Items from Refactor

## Status: AUDIT / MINOR FIXES

The Phase 1-2 refactor (plans 01–05) is **95% complete.** The structural refactoring is done:

| Planned Action | Status |
|---|---|
| Delete `app/agents/` folder (6 files) | ✅ Done |
| Delete `app/engine/` folder (12 files) | ✅ Done |
| Move `app/collectors/` → `app/services/` (11 files) | ✅ Done |
| Delete `app/models/agent_reports.py` | ✅ Done |
| Delete `app/models/decision.py` | ✅ Done |
| Move `quant_signals.py` → `services/quant_engine.py` | ✅ Done |
| Move `data_distiller.py` → `services/data_distiller.py` | ✅ Done |
| Move `portfolio_strategist.py` → `services/portfolio_strategist.py` | ✅ Done |
| Move `strategist_audit.py` → `services/strategist_audit.py` | ✅ Done |
| Simplify `deep_analysis_service.py` (0 LLM calls) | ✅ Done |
| Simplify `pipeline_service.py` (agents + decision code removed) | ✅ Done |
| `symbol_filter.py` composable pipeline | ✅ Done (added post-plan) |

---

## Remaining Cleanup Items

### 1. `app/models/dossier.py` — Stale fields not cleaned up

Per plan `04_models_refactor.md`, these should have been removed:

- **`QAPair` class still exists** — only used by the deleted RAG engine. Should be deleted.
- **`TickerDossier` still has old fields:**
  - `qa_pairs: list[QAPair]` → DELETE (no more RAG)
  - `bull_case`, `bear_case`, `key_catalysts`, `conviction_score` → The plan said to remove these since "the strategist determines them, not pre-computed." However, `conviction_score` is computed by `DeepAnalysisService._compute_conviction()` and stored — so it's actually in use.
  - `executive_summary` → Still populated by `deep_analysis_service.py`

**Recommended action:**

- Delete `QAPair` class
- Delete `qa_pairs` field from `TickerDossier`
- Keep `executive_summary`, `conviction_score`, `signal_summary` (actively used)
- Delete `bull_case`, `bear_case`, `key_catalysts` **only if** nothing writes/reads them
- Update docstring (still says "4-Layer Analysis Funnel")

### 2. Verify no dead imports remain

```bash
rg "QAPair" app/
rg "agent_reports" app/
rg "from app.models.decision" app/
rg "from app.engine" app/
rg "from app.agents" app/
rg "from app.collectors" app/
```

All should return zero results.

### 3. `TickerDossier` docstring outdated

Still says "Layer 4 output — the final synthesized analysis for Phase 3." Should say something like "Analysis package — quant scorecard + distilled context."

---

## Files Affected

- **MODIFY:** `app/models/dossier.py` (remove `QAPair`, clean `TickerDossier` fields)
- **VERIFY:** No dead imports across `app/`

## Risk: Very Low

Pure schema cleanup. No logic changes. Run `ruff` + `mypy` after.
