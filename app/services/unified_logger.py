"""Unified Telemetry Logger — Tracks execution of 55 pipeline tools.

Provides a unified `@track_telemetry` and `@track_class_telemetry` to trace calls.
Uses contextvars to maintain cycle_id without modifying function signatures.
Persists execution details directly into DuckDB `pipeline_telemetry`.
"""

from __future__ import annotations

import functools
import inspect
import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from app.database import get_db
from app.utils.logger import logger

# Global context var for the current execution cycle
# autonomous_loop.py can set this: cycle_token = current_cycle_id.set(new_id)
current_cycle_id: ContextVar[str | None] = ContextVar("current_cycle_id", default=None)


def _log_to_db(
    cycle_id: str,
    step_name: str,
    status: str,
    duration_ms: float,
    input_size: int,
    output_size: int,
    fail_reason: str,
) -> None:
    """Insert telemetry event into DuckDB."""
    db = get_db()
    
    # Ensure table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_telemetry (
            id VARCHAR PRIMARY KEY,
            cycle_id VARCHAR NOT NULL,
            step_name VARCHAR NOT NULL,
            status VARCHAR DEFAULT 'success',
            duration_ms DOUBLE DEFAULT 0,
            input_size INTEGER DEFAULT 0,
            output_size INTEGER DEFAULT 0,
            fail_reason VARCHAR DEFAULT '',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    event_id = str(uuid.uuid4())
    try:
        db.execute(
            """
            INSERT INTO pipeline_telemetry
                (id, cycle_id, step_name, status, duration_ms, input_size, output_size, fail_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event_id,
                cycle_id,
                step_name,
                status,
                duration_ms,
                input_size,
                output_size,
                fail_reason[:500],
            ],
        )
    except Exception as exc:
        logger.error("[Telemetry] Failed to log event %s: %s", step_name, exc)


def track_telemetry(step_name: str | None = None):
    """Decorator to track execution of a pipeline tool."""
    
    def decorator(func):
        _step_name = step_name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            cycle = current_cycle_id.get()
            
            try:
                input_size = len(str(args)) + len(str(kwargs))
            except Exception:
                input_size = 0

            started_at = time.perf_counter()
            status = "success"
            fail_reason = ""
            output_size = 0
            
            try:
                result = await func(*args, **kwargs)
                try:
                    output_size = len(str(result))
                except Exception:
                    pass
                return result
            except Exception as exc:
                status = "failed"
                fail_reason = str(exc)
                logger.error("[Telemetry] %s failed: %s", _step_name, fail_reason)
                raise
            finally:
                duration_ms = float(round((time.perf_counter() - started_at) * 1000, 2))
                cycle = current_cycle_id.get()
                if cycle:
                    _log_to_db(cycle, _step_name, status, duration_ms, input_size, output_size, fail_reason)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            cycle = current_cycle_id.get()
            
            try:
                input_size = len(str(args)) + len(str(kwargs))
            except Exception:
                input_size = 0

            started_at = time.perf_counter()
            status = "success"
            fail_reason = ""
            output_size = 0

            try:
                result = func(*args, **kwargs)
                try:
                    output_size = len(str(result))
                except Exception:
                    pass
                return result
            except Exception as exc:
                status = "failed"
                fail_reason = str(exc)
                logger.error("[Telemetry] %s failed: %s", _step_name, fail_reason)
                raise
            finally:
                duration_ms = float(round((time.perf_counter() - started_at) * 1000, 2))
                cycle = current_cycle_id.get()
                if cycle:
                    _log_to_db(cycle, _step_name, status, duration_ms, input_size, output_size, fail_reason)

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def track_class_telemetry(cls):
    """Class decorator that applies @track_telemetry to all public methods."""
    for attr_name, attr_value in vars(cls).items():
        if callable(attr_value) and not attr_name.startswith("_"):
            # Don't wrap classmethods or staticmethods implicitly if vars(cls) returns a function,
            # but in Python, vars returns function objects. We can just wrap it.
            # But wait, classmethod and staticmethod are descriptors.
            if inspect.isfunction(attr_value) or inspect.iscoroutinefunction(attr_value):
                setattr(cls, attr_name, track_telemetry(f"{cls.__name__}.{attr_name}")(attr_value))
    return cls
