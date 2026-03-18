# Pipeline Audit Infrastructure

Debug every phase of the trading bot pipeline box-by-box using the test DB.

## Issues to Address

1. **Dual model loading** — olmo-3 + granite3.2 both in VRAM ✅ Fixed (unload before warm)
2. **LLM stops at summaries** — YouTube/Reddit data gets summarized but never fed into analysis/trading decisions
3. **No CLI visibility** — can't see what each phase actually does with the data
4. **No per-phase testing** — can't run one phase in isolation to verify it works

## Proposed Changes

### Pipeline Flow Graph

Create a Mermaid graph document showing every box in the pipeline, what data flows in/out, and which service handles it. You'll review this box-by-box.

#### [NEW] [pipeline_flow_graph.md](file:///home/braindead/github/Lazy-Trading-Bot/plan/pipeline_flow_graph.md)

---

### CLI Audit Runner

A standalone script that runs each pipeline phase individually against the test DB with maximum CLI logging. No server required — direct function calls with verbose output.

#### [NEW] [run_pipeline_audit.py](file:///home/braindead/github/Lazy-Trading-Bot/scripts/run_pipeline_audit.py)

Phases (run individually or all):
```
python scripts/run_pipeline_audit.py --phase discovery
python scripts/run_pipeline_audit.py --phase collection
python scripts/run_pipeline_audit.py --phase embedding
python scripts/run_pipeline_audit.py --phase analysis
python scripts/run_pipeline_audit.py --phase trading
python scripts/run_pipeline_audit.py --phase all       # full pipeline
```

Each phase prints:
- Input data (what it reads from DB)
- Processing steps (what it does)
- Output data (what it writes to DB)
- Timing per step
- Any errors or warnings

---

### Dual Model Loading Fix (already done)

#### [MODIFY] [autonomous_loop.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/autonomous_loop.py)
- Added `unload_all_ollama_models()` before warm-up in both `run_full_loop()` and `run_llm_only_loop()`

## Verification Plan

### Automated Tests
1. `python scripts/run_pipeline_audit.py --phase all` against test DB
2. Paste terminal output for box-by-box review
3. Verify each pipeline graph box produces expected output
