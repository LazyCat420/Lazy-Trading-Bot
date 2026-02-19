"""Pipeline Event Logger — persistent audit trail for all bot activity.

Provides a single `log_event()` function that any pipeline stage can call
to record what happened.  Events are stored in the `pipeline_events` DuckDB
table and served via ``GET /api/pipeline/events``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.database import get_db
from app.utils.logger import logger


# Module-level loop_id so every event in the same autonomous-loop run
# is grouped together.  Set by `start_loop()`.
_current_loop_id: str | None = None


def start_loop() -> str:
    """Generate a new loop_id and return it."""
    global _current_loop_id  # noqa: PLW0603
    _current_loop_id = uuid.uuid4().hex[:8]
    logger.info("[EventLogger] Loop started: %s", _current_loop_id)
    return _current_loop_id


def end_loop() -> None:
    """Clear the current loop_id."""
    global _current_loop_id  # noqa: PLW0603
    _current_loop_id = None


def get_loop_id() -> str | None:
    """Return the current loop_id (or None if no loop is active)."""
    return _current_loop_id


def log_event(
    phase: str,
    event_type: str,
    detail: str,
    *,
    ticker: str | None = None,
    metadata: dict | None = None,
    status: str = "success",
) -> None:
    """Write one event row to pipeline_events.

    Parameters
    ----------
    phase : str
        Pipeline phase — ``discovery``, ``collection``, ``analysis``,
        ``import``, ``trading``, or ``system``.
    event_type : str
        Short event name, e.g. ``ticker_discovered``,
        ``price_history_collected``, ``dossier_synthesized``.
    detail : str
        Human-readable summary shown in the Activity Log.
    ticker : str | None
        Ticker symbol (``None`` for system-level events).
    metadata : dict | None
        Arbitrary JSON blob with counts / specifics.
    status : str
        ``success`` | ``error`` | ``warning`` | ``skipped``.
    """
    try:
        db = get_db()
        db.execute(
            """
            INSERT INTO pipeline_events
                (id, timestamp, phase, event_type, ticker,
                 detail, metadata, loop_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex,
                datetime.now().isoformat(),
                phase,
                event_type,
                ticker,
                detail,
                json.dumps(metadata or {}),
                _current_loop_id,
                status,
            ],
        )
    except Exception as exc:
        # Never let logging failures break the pipeline
        logger.warning("[EventLogger] Failed to log event: %s", exc)
