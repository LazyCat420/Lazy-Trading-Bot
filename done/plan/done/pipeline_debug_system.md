# Pipeline Debug System — Implementation Plan (DONE)

Completed 2026-03-16. See `plan/pipeline_audit_report.md` for findings.

## What Was Built

### Part A: CLI Audit Runner (`scripts/run_pipeline_audit.py`)
- Forces `gemma3:4b` model for consistent testing
- Prints EVERY step with color-coded output and `perf_counter` benchmarking
- Fixed `content_preview` → `chunk_text` column bug
- Shows all data flowing between phases (inputs/outputs)
- Benchmark summary sorted by duration at end

### Part B: PipelineTracer (`app/services/PipelineTracer.py`)
- Singleton `tracer` records every pipeline step (phase, timing, IO, status)
- Supports nested steps via `parent_id`
- Persists traces to `pipeline_traces` DB table
- Keeps last 20 runs in memory for fast API access

### Part C: Diagnostics API & UI
- 4 new endpoints: `/diagnostics`, `/api/diagnostics/trace`, `/api/diagnostics/trace/history`, `/api/diagnostics/trace/{run_id}`
- `diagnostics.html`: node-graph visualization with color-coded phases, click-to-expand detail panels, timeline bars, auto-refresh (3s)

## Next Steps
- Run `python scripts/run_pipeline_audit.py --phase all` to exercise full pipeline with gemma3:4b
- Visit `http://localhost:8000/diagnostics` to see the node-graph view
- After gemma3:4b works, test with granite3.2:8b, granite3.3:8b, olmo-3:latest
