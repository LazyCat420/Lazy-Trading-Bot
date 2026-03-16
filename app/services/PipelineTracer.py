"""PipelineTracer — records every pipeline step with timing, IO, and status.

Used by both the CLI audit runner and the autonomous loop to create
a trace of what happened during a pipeline run. The trace feeds the
diagnostics UI at /diagnostics.

Usage:
    from app.services.PipelineTracer import tracer
    step = tracer.start_run("full_loop", model="gemma3:4b")
    sid = tracer.begin("embedding", inputs={"transcripts": 5})
    tracer.end(sid, outputs={"chunks": 22}, status="ok")
    tracer.finish_run()
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.logger import logger


@dataclass
class StepRecord:
    """One step in a pipeline trace."""
    step_id: str
    phase: str
    step_name: str
    status: str = "running"  # running | ok | error | warning | skipped
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_ms: float = 0.0
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    error: str = ""
    children: list["StepRecord"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "phase": self.phase,
            "step_name": self.step_name,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class RunTrace:
    """A complete pipeline run trace."""
    run_id: str
    run_type: str  # full_loop | llm_only | audit
    model: str = ""
    ticker: str = ""
    started_at: str = ""
    ended_at: str = ""
    total_ms: float = 0.0
    status: str = "running"
    steps: list[StepRecord] = field(default_factory=list)
    _step_map: dict[str, StepRecord] = field(default_factory=dict, repr=False)
    _t0: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "run_type": self.run_type,
            "model": self.model,
            "ticker": self.ticker,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total_ms": self.total_ms,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }


class PipelineTracer:
    """Singleton tracer — records pipeline steps for diagnostics UI."""

    def __init__(self) -> None:
        self._current_run: RunTrace | None = None
        self._history: list[RunTrace] = []  # Last N runs
        self._max_history = 20

    @property
    def current(self) -> RunTrace | None:
        return self._current_run

    def start_run(
        self, run_type: str, *, model: str = "", ticker: str = "",
    ) -> str:
        """Begin a new pipeline run. Returns run_id."""
        run_id = str(uuid.uuid4())[:8]
        self._current_run = RunTrace(
            run_id=run_id,
            run_type=run_type,
            model=model,
            ticker=ticker,
            started_at=datetime.now().isoformat(),
            _t0=time.perf_counter(),
        )
        logger.info(
            "[Tracer] ▶ Run started: %s (type=%s, model=%s)",
            run_id, run_type, model,
        )
        return run_id

    def begin(
        self, phase: str, step_name: str = "", *,
        inputs: dict | None = None,
        parent_id: str | None = None,
    ) -> str:
        """Begin a step within the current run. Returns step_id."""
        if not self._current_run:
            self.start_run("auto")

        step_id = str(uuid.uuid4())[:8]
        step = StepRecord(
            step_id=step_id,
            phase=phase,
            step_name=step_name or phase,
            started_at=time.perf_counter(),
            inputs=_safe_json(inputs) if inputs else {},
        )

        self._current_run._step_map[step_id] = step

        if parent_id and parent_id in self._current_run._step_map:
            self._current_run._step_map[parent_id].children.append(step)
        else:
            self._current_run.steps.append(step)

        logger.info("[Tracer] ├─ %s/%s started", phase, step_name or phase)
        return step_id

    def end(
        self, step_id: str, *,
        outputs: dict | None = None,
        status: str = "ok",
        error: str = "",
    ) -> float:
        """End a step. Returns duration_ms."""
        if not self._current_run:
            return 0.0

        step = self._current_run._step_map.get(step_id)
        if not step:
            logger.warning("[Tracer] Step %s not found", step_id)
            return 0.0

        step.ended_at = time.perf_counter()
        step.duration_ms = round((step.ended_at - step.started_at) * 1000, 1)
        step.status = status
        step.outputs = _safe_json(outputs) if outputs else {}
        step.error = error

        icon = {"ok": "✅", "error": "❌", "warning": "⚠️", "skipped": "⏭️"}.get(status, "")
        logger.info(
            "[Tracer] ├─ %s/%s %s (%.1fms)",
            step.phase, step.step_name, icon, step.duration_ms,
        )
        return step.duration_ms

    def finish_run(self, status: str = "ok") -> dict:
        """Finish the current run. Returns trace dict."""
        if not self._current_run:
            return {}

        run = self._current_run
        run.ended_at = datetime.now().isoformat()
        run.total_ms = round((time.perf_counter() - run._t0) * 1000, 1)
        run.status = status

        # Persist to history
        self._history.append(run)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Also persist to DB
        try:
            self._persist_to_db(run)
        except Exception as exc:
            logger.warning("[Tracer] Failed to persist trace: %s", exc)

        logger.info(
            "[Tracer] ▶ Run finished: %s (%.1fms, status=%s)",
            run.run_id, run.total_ms, status,
        )

        result = run.to_dict()
        self._current_run = None
        return result

    def get_latest(self) -> dict | None:
        """Get the most recent trace (current or last completed)."""
        if self._current_run:
            return self._current_run.to_dict()
        if self._history:
            return self._history[-1].to_dict()
        return None

    def get_history(self, limit: int = 10) -> list[dict]:
        """Get recent trace history."""
        traces = self._history[-limit:]
        result = [t.to_dict() for t in reversed(traces)]
        if self._current_run:
            result.insert(0, self._current_run.to_dict())
        return result

    def _persist_to_db(self, run: RunTrace) -> None:
        """Save trace to pipeline_traces table."""
        from app.database import get_db

        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_traces (
                run_id VARCHAR PRIMARY KEY,
                run_type VARCHAR,
                model VARCHAR,
                ticker VARCHAR,
                started_at VARCHAR,
                ended_at VARCHAR,
                total_ms DOUBLE,
                status VARCHAR,
                trace_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute(
            "INSERT OR REPLACE INTO pipeline_traces "
            "(run_id, run_type, model, ticker, started_at, ended_at, "
            "total_ms, status, trace_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run.run_id, run.run_type, run.model, run.ticker,
                run.started_at, run.ended_at, run.total_ms, run.status,
                json.dumps(run.to_dict(), default=str),
            ],
        )
        db.commit()


def _safe_json(obj: Any) -> dict:
    """Convert to JSON-safe dict (truncate large values)."""
    if not obj:
        return {}
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            sv = str(v)
            if len(sv) > 500:
                result[k] = sv[:500] + "…"
            else:
                result[k] = v
        return result
    return {"value": str(obj)[:500]}


# Singleton instance
tracer = PipelineTracer()
