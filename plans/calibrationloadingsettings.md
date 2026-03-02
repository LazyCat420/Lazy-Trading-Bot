Here is a detailed, step-by-step plan for your dev team to fix the UI calibration state.

The core issue is that the frontend currently relies on the initial HTTP request waiting for a response to show the loading state. When the user leaves the page, the frontend loses that local state. To fix this, the backend needs to become the "source of truth" for the calibration status, and the frontend needs to fetch that status whenever the settings page mounts.

### The Fix: Backend State + Frontend Polling

#### Phase 1: Create a Backend Calibration State Manager

Since calibration is a global, infrequent event, an in-memory singleton (or a simple DuckDB status row) is perfect for tracking it.

**1. Create a tracking dictionary (e.g., in `app/services/llm_service.py` or a new `app/services/calibration_tracker.py`):**

```python
# app/services/calibration_tracker.py

class CalibrationTracker:
    _state = {
        "status": "idle", # 'idle', 'calibrating', 'success', 'error'
        "model": None,
        "current_step": "",
        "logs": [],       # Keep the last 5-10 logs for the UI
        "progress_pct": 0
    }

    @classmethod
    def get_state(cls):
        return cls._state

    @classmethod
    def set_status(cls, status, model=None):
        cls._state["status"] = status
        if model:
            cls._state["model"] = model
        if status == "idle":
            cls._state["logs"] = []
            cls._state["progress_pct"] = 0

    @classmethod
    def update_progress(cls, step_message: str, pct: int):
        cls._state["current_step"] = step_message
        cls._state["logs"].append(step_message)
        cls._state["progress_pct"] = pct
        # Keep logs capped so it doesn't leak memory
        if len(cls._state["logs"]) > 10:
            cls._state["logs"].pop(0)
```

#### Phase 2: Hook the Tracker into `verifyandwarmollamamodel`

In `app/services/llm_service.py`, the `verifyandwarmollamamodel` function currently uses `logger.info()` to print the exact steps to the terminal. Tell the devs to insert calls to `CalibrationTracker.update_progress()` right next to those log statements. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/26893963/a718800b-dc8b-4b73-8fe7-b578a8a38a66/llm_service.py)

**Example updates in `llm_service.py`:**

```python
# Before starting:
CalibrationTracker.set_status("calibrating", model=model)
CalibrationTracker.update_progress("Step 1: Verifying model exists...", 10)

# At Step 2:
CalibrationTracker.update_progress("Step 2: Querying architecture...", 25)

# At Step 4:
CalibrationTracker.update_progress("Step 4: Estimating VRAM requirements...", 50)

# During Step 8 (Profiling/Scale up):
CalibrationTracker.update_progress(f"Step 8: Profiling VRAM. Scaling UP to ctx={optimalctx}...", 80)

# On Success:
CalibrationTracker.set_status("success")
CalibrationTracker.update_progress(f"Calibration complete! Loaded at {loadctx} ctx.", 100)

# On Error/Catch block:
CalibrationTracker.set_status("error")
CalibrationTracker.update_progress(f"Failed: {str(exc)}", 100)
```

#### Phase 3: Create the Status Endpoint

Create a new FastAPI route that the frontend can call to get the real-time status.

**In `app/api/routes.py` (or wherever your settings endpoints live):**

```python
from fastapi import APIRouter
from app.services.calibration_tracker import CalibrationTracker

router = APIRouter()

@router.get("/api/settings/calibration-status")
async def get_calibration_status():
    return CalibrationTracker.get_state()
```

#### Phase 4: Update the Frontend UI (React/JS)

The frontend needs to poll this endpoint whenever the user is on the Settings page, which solves the "leave and come back" problem.

1. **On Mount (Page Load):** When the settings page loads, fire a `GET` request to `/api/settings/calibration-status`.
2. **Polling:** If the returned status is `"calibrating"`, set an interval (e.g., `setInterval`) to poll that endpoint every 1 to 2 seconds.
3. **UI Display:**
   - Render a progress bar using the `progress_pct`.
   - Render a small terminal-like scrolling box (or a simple text span) showing `current_step` or iterating through the `logs` array.
4. **Triggering Calibration:** When the user clicks "Calibrate", the frontend calls the trigger endpoint, then immediately sets the UI into the "polling" state to watch the progress.
5. **Cleanup:** Once `status === 'success'` or `'error'`, stop polling, show a green checkmark/red warning, and clear the interval.

### Acceptance Criteria for the Dev Team

- If a user clicks "Calibrate", leaves to view the "Scoreboard", and returns to "Settings" 10 seconds later, the UI must immediately show the active loading bar and current calibration step.

- The frontend terminal/progress text must exactly match what is printing in the backend Python terminal.
- If the server restarts or the process fails silently, the UI should timeout gracefully or reset to idle.
