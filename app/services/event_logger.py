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
from app.services.ws_broadcaster import broadcaster
from app.utils.logger import logger

# Module-level loop_id so every event in the same autonomous-loop run
# is grouped together.  Set by `start_loop()`.
_current_loop_id: str | None = None

# Module-level bot context so every event records which bot/model produced it.
_current_bot_id: str = "default"
_current_model_name: str = ""


def set_bot_context(bot_id: str, model_name: str = "") -> None:
    """Set the bot context used by all subsequent log_event() calls."""
    global _current_bot_id, _current_model_name
    _current_bot_id = bot_id or "default"
    _current_model_name = model_name or ""


def start_loop() -> str:
    """Generate a new loop_id and return it."""
    global _current_loop_id
    _current_loop_id = uuid.uuid4().hex[:8]
    logger.info("[EventLogger] Loop started: %s", _current_loop_id)
    return _current_loop_id


def end_loop() -> None:
    """Clear the current loop_id."""
    global _current_loop_id
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
    bot_id: str | None = None,
    model_name: str | None = None,
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
    bot_id : str | None
        Override bot_id (defaults to module-level ``_current_bot_id``).
    model_name : str | None
        Override model_name (defaults to module-level ``_current_model_name``).
    """
    effective_bot_id = bot_id if bot_id is not None else _current_bot_id
    effective_model = model_name if model_name is not None else _current_model_name
    # DISABLED: pipeline_events now lives in MongoDB only (tradingbackend writes)
    # DuckDB insert removed — WebSocket broadcast below still feeds the Activity tab
    logger.debug(
        "[EventLogger] %s/%s: %s (ticker=%s, bot=%s)",
        phase, event_type, detail, ticker, effective_bot_id,
    )

    # ── Emit to Websocket Broadcaster ──
    broadcaster.broadcast_sync({
        "type": "phase_update",
        "node": phase,
        "status": status,
        "label": detail,
        "ticker": ticker,
        "data_out": metadata,
        "timestamp": datetime.now().timestamp(),
        "meta": metadata
    })
