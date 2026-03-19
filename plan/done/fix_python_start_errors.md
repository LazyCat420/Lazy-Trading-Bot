# Python Service Lock & Import Fix Plan

## Issues Identified
1. **DuckDB Locked by Zombie Process**: When starting the `uvicorn` server, DuckDB threw an `IOException: Could not set lock` with conflicting PID `340272`. This implies the old Python service didn't cleanly release its resources when stopped.
2. **Missing Import in `symbol_filter.py`**: The autonomous loop crashed entirely during `Discovery` because `app/services/symbol_filter.py` uses the `@track_class_telemetry` decorator but fails to import it, throwing a `NameError`.

## Action Plan
1. **[x] Terminate Conflicting PID**: Execute `kill -9 340272` to forcibly end the Python process holding the DuckDB instance hostage.
2. **[ ] Add Missing Import**: Inject `from app.services.unified_logger import track_class_telemetry` into `app/services/symbol_filter.py` before it is used for the class definitions.
3. **[ ] Move to Done**: Move this plan file to `plan/done/` once executed.
