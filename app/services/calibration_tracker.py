"""In-memory singleton for tracking VRAM calibration status.

The frontend polls GET /api/settings/calibration-status to read this
state, so the UI can survive page navigations during calibration.
"""

from __future__ import annotations

import time


class CalibrationTracker:
    """Thread-safe calibration state singleton.

    Status values: 'idle', 'calibrating', 'success', 'error'
    """

    _state: dict = {
        "status": "idle",
        "model": None,
        "current_step": "",
        "logs": [],
        "progress_pct": 0,
        "started_at": None,
        "proven_max_ctx": 0,
    }

    @classmethod
    def get_state(cls) -> dict:
        """Return a copy of the current calibration state."""
        return {**cls._state, "logs": list(cls._state["logs"])}

    @classmethod
    def set_status(
        cls,
        status: str,
        model: str | None = None,
        proven_max_ctx: int = 0,
    ) -> None:
        """Transition calibration status."""
        cls._state["status"] = status
        if model:
            cls._state["model"] = model
        if proven_max_ctx > 0:
            cls._state["proven_max_ctx"] = proven_max_ctx
        if status == "calibrating":
            cls._state["started_at"] = time.time()
        if status in ("idle", "success", "error"):
            cls._state["started_at"] = None
        if status == "idle":
            cls._state["logs"] = []
            cls._state["progress_pct"] = 0
            cls._state["current_step"] = ""
            cls._state["model"] = None
            cls._state["proven_max_ctx"] = 0

    @classmethod
    def update_progress(cls, step_message: str, pct: int) -> None:
        """Push a progress update visible to the frontend."""
        cls._state["current_step"] = step_message
        cls._state["progress_pct"] = pct
        cls._state["logs"].append(step_message)
        # Cap logs to prevent memory leak
        if len(cls._state["logs"]) > 10:
            cls._state["logs"].pop(0)

    @classmethod
    def is_calibrating(cls) -> bool:
        """Quick check if calibration is currently in progress."""
        return cls._state["status"] == "calibrating"

    @classmethod
    def reset(cls) -> None:
        """Force-reset to idle (e.g. on timeout or server recovery)."""
        cls.set_status("idle")
