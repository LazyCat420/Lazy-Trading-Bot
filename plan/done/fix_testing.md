# Plan to Fix Test Failures

## Issue Analysis
1. **Failing Tests (DuckDB Lock):** The terminal output shows `diagnose_collectors.py` is failing with `IO Error: Could not set lock on file .../data/trading_bot_test.duckdb`. A lock is currently held by Python process PID `904586`.
2. **Hardcoded Model:** In `scripts/run_pipeline_audit.py`, we explicitly set `TEST_MODEL = "gemma3:4b"` and override `settings.LLM_MODEL` with it, completely ignoring the configured model (vLLM).

## Plan
1. **Release Database Lock:**
   - Kill process `904586` so that the `trading_bot_test.duckdb` is correctly unlocked and the scripts can establish a connection again.
   
2. **Update script to use configured model (vllm):**
   - Modify `scripts/run_pipeline_audit.py` to remove the hardcoded `gemma3:4b` logic.
   - Simply use `settings.LLM_MODEL` throughout the script.
   
3. **Verify Functionality:**
   - Re-run `python scripts/run_pipeline_audit.py --phase all` to ensure it passes without lock errors and correctly uses the desired model.
   - Run `python scripts/diagnose_collectors.py AAPL --skip-youtube` to ensure data collection works cleanly without lock errors.

4. **Final Step:**
   - Log the changes in `CHANGES.md`.
   - Move this plan to `plan/done/`.
