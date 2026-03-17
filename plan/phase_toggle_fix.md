# Phase Toggle Fix Plan

## Problem
Dev tools phase toggles (discovery, import, collection, embedding, analysis, trading) were not affecting the pipeline when using "Run All" or "Run Full Loop". Even with phases disabled, the bot still ran all phases.

## Root Cause
- `run_shared_phases()` and `run_llm_only_loop()` in `autonomous_loop.py` did NOT check `_is_phase_enabled()` — they ran all phases unconditionally.
- `run_full_loop()` already had the toggle checks and worked correctly.
- The toggles were being SET on loop instances correctly from `main.py`, but the methods themselves ignored them.

## Fix Checklist
- [x] Add `_is_phase_enabled()` checks to `run_shared_phases()` for: discovery, import, collection, embedding
- [x] Add `_is_phase_enabled()` checks to `run_llm_only_loop()` for: import, analysis, trading
- [x] Add "▶ Run Enabled Phases" play button in DevTools panel (`terminal_app.js`)
- [x] Add 9 new tests verifying toggle enforcement in `test_shared_pipeline.py`
- [x] All 25 tests pass
