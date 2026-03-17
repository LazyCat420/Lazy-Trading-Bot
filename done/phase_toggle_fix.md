# Phase Toggle Fix — Done

## What was fixed
1. `run_shared_phases()` now checks `_is_phase_enabled()` for each phase (discovery, import, collection, embedding)
2. `run_llm_only_loop()` now checks `_is_phase_enabled()` for each phase (import, analysis, trading)
3. Added "▶ Run Enabled Phases" play button in the Dev Tools panel so users can run the pipeline directly from dev tools with toggles respected

## Files Changed
- `app/services/autonomous_loop.py` — toggle guards added to `run_shared_phases()` and `run_llm_only_loop()`
- `app/static/terminal_app.js` — added "Run Enabled Phases" play button in DevTools panel
- `tests/test_shared_pipeline.py` — added 9 new tests verifying toggle enforcement

## Test Results
- 25/25 tests pass in `test_shared_pipeline.py`
