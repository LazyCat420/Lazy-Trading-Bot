# Remove Calibration System — Direct Context Length Setting

## Done

Removed the VRAM calibration/empirical audit system from settings. The user now directly sets
context length, no clamping, no background calibration polling, no proven_max_ctx.

### Files Changed
- **DELETED** `app/services/calibration_tracker.py`
- **MODIFIED** `app/main.py` — removed calibration endpoint, background calibrate, VRAM clamping
- **MODIFIED** `app/services/llm_service.py` — removed CalibrationTracker, audit system
- **MODIFIED** `app/static/terminal_app.js` — removed calibration UI
- **MODIFIED** `tests/test_vram_oom.py` — replaced audit tests with simple load tests

### Tests: 4/4 passed
