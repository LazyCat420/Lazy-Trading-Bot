"""Pipeline Health Tracker — collects structured diagnostics during each loop run.

Produces a compact health report (reports/health_YYYY-MM-DD_HHMMSS.md) that
summarizes errors, warnings, LLM call metrics, phase timings, and a
pass/fail scorecard for easy debugging.

Usage::

    tracker = HealthTracker()
    set_active_tracker(tracker)   # module-level reference for LLM calls

    tracker.start_phase("discovery")
    # ... pipeline work ...
    tracker.end_phase("discovery")

    tracker.generate_report()     # writes the report file
    clear_active_tracker()
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from app.config import settings
from app.utils.logger import logger

# ── Module-level tracker reference (same pattern as event_logger.py) ──

_active_tracker: HealthTracker | None = None
_tracker_lock = threading.Lock()


def set_active_tracker(tracker: HealthTracker) -> None:
    """Set the module-level active health tracker."""
    global _active_tracker  # noqa: PLW0603
    with _tracker_lock:
        _active_tracker = tracker


def clear_active_tracker() -> None:
    """Clear the module-level active health tracker."""
    global _active_tracker  # noqa: PLW0603
    with _tracker_lock:
        _active_tracker = None


def get_active_tracker() -> HealthTracker | None:
    """Return the active health tracker (or None)."""
    return _active_tracker


def log_llm_call(
    *,
    context: str,
    model: str = "",
    duration_seconds: float = 0.0,
    timed_out: bool = False,
    tokens_used: int = 0,
    error: str | None = None,
) -> None:
    """Record an LLM call to the active tracker (no-op if none active)."""
    tracker = _active_tracker
    if tracker is not None:
        tracker.record_llm_call(
            context=context,
            model=model,
            duration_seconds=duration_seconds,
            timed_out=timed_out,
            tokens_used=tokens_used,
            error=error,
        )


# ── Diagnostic Log Handler ──────────────────────────────────────────


class DiagnosticHandler(logging.Handler):
    """Captures WARNING+ log records into a thread-safe list.

    Attached to the logger once; the HealthTracker reads captured records
    to build the errors/warnings section of the report without modifying
    every call site.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime(
                "%H:%M:%S"
            ),
            "level": record.levelname,
            "message": self.format(record) if self.formatter else record.getMessage(),
        }
        with self._lock:
            self._records.append(entry)

    def get_records(self) -> list[dict[str, Any]]:
        """Return a copy of all captured records."""
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        """Clear all captured records."""
        with self._lock:
            self._records.clear()


# ── Module-level diagnostic handler attached once ──

_diagnostic_handler: DiagnosticHandler | None = None


def get_diagnostic_handler() -> DiagnosticHandler:
    """Return (and lazily create + attach) the diagnostic handler."""
    global _diagnostic_handler  # noqa: PLW0603
    if _diagnostic_handler is None:
        _diagnostic_handler = DiagnosticHandler()
        fmt = logging.Formatter("%(message)s")
        _diagnostic_handler.setFormatter(fmt)
        logger.addHandler(_diagnostic_handler)
    return _diagnostic_handler


# ── Health Tracker ──────────────────────────────────────────────────


class HealthTracker:
    """Accumulates diagnostics during one autonomous loop run."""

    def __init__(self, loop_id: str = "") -> None:
        self.loop_id = loop_id
        self.started_at = datetime.now()
        self._phases: dict[str, dict[str, Any]] = {}
        self._llm_calls: list[dict[str, Any]] = []
        self._custom_checks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        # Clear and capture fresh warnings/errors for this run
        handler = get_diagnostic_handler()
        handler.clear()

    # ── Phase tracking ──

    def start_phase(self, name: str) -> None:
        """Mark a phase as started."""
        with self._lock:
            self._phases[name] = {
                "start": time.time(),
                "end": None,
                "duration": None,
                "status": "running",
            }

    def end_phase(
        self, name: str, *, status: str = "success", detail: str = ""
    ) -> None:
        """Mark a phase as finished."""
        with self._lock:
            phase = self._phases.get(name, {})
            start = phase.get("start", time.time())
            elapsed = time.time() - start
            self._phases[name] = {
                "start": start,
                "end": time.time(),
                "duration": round(elapsed, 1),
                "status": status,
                "detail": detail,
            }

    # ── LLM call tracking ──

    def record_llm_call(
        self,
        *,
        context: str,
        model: str = "",
        duration_seconds: float = 0.0,
        timed_out: bool = False,
        tokens_used: int = 0,
        error: str | None = None,
    ) -> None:
        """Record one LLM API call."""
        with self._lock:
            self._llm_calls.append({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "context": context,
                "model": model,
                "duration": round(duration_seconds, 1),
                "timed_out": timed_out,
                "tokens": tokens_used,
                "error": error,
            })

    # ── Custom health checks ──

    def record_check(
        self, name: str, *, passed: bool, detail: str = ""
    ) -> None:
        """Record a pass/fail health check."""
        with self._lock:
            self._custom_checks[name] = {
                "passed": passed,
                "detail": detail,
            }

    # ── Report generation ──

    def generate_report(self) -> str:
        """Generate a markdown health report and write to disk.

        Returns the absolute path to the report file.
        """
        now = datetime.now()
        elapsed_total = (now - self.started_at).total_seconds()

        lines: list[str] = []
        lines.append("# Pipeline Health Report")
        lines.append("")
        lines.append(
            f"**Loop ID:** `{self.loop_id}`  "
        )
        lines.append(
            f"**Started:** {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}  "
        )
        lines.append(
            f"**Duration:** {self._format_duration(elapsed_total)}"
        )
        lines.append("")

        # ── Scorecard ──
        lines.append("## Scorecard")
        lines.append("")
        lines.append("| Check | Result |")
        lines.append("|-------|--------|")

        for name, check in self._custom_checks.items():
            icon = "✅" if check["passed"] else "❌"
            detail = check.get("detail", "")
            lines.append(f"| {name} | {icon} {detail} |")

        # LLM timeout check
        timeouts = [c for c in self._llm_calls if c["timed_out"]]
        total_llm = len(self._llm_calls)
        if total_llm > 0:
            if timeouts:
                lines.append(
                    f"| LLM calls completed | ❌ {len(timeouts)}/{total_llm} "
                    f"timed out |"
                )
            else:
                lines.append(
                    f"| LLM calls completed | ✅ {total_llm}/{total_llm} OK |"
                )

        # Pipeline time check
        if elapsed_total > 1800:
            lines.append(
                f"| Pipeline time < 30 min | ❌ "
                f"{self._format_duration(elapsed_total)} |"
            )
        else:
            lines.append(
                f"| Pipeline time < 30 min | ✅ "
                f"{self._format_duration(elapsed_total)} |"
            )

        lines.append("")

        # ── Phase Timing ──
        lines.append("## Phase Timing")
        lines.append("")
        lines.append("| Phase | Duration | Status |")
        lines.append("|-------|----------|--------|")

        phase_order = [
            "discovery", "import", "collection",
            "analysis", "trading",
        ]
        for phase_name in phase_order:
            phase = self._phases.get(phase_name)
            if not phase:
                lines.append(f"| {phase_name} | — | ⏭️ skipped |")
                continue

            dur = phase.get("duration")
            status = phase.get("status", "unknown")
            detail = phase.get("detail", "")

            dur_str = self._format_duration(dur) if dur is not None else "—"

            if status == "error":
                icon = "❌"
            elif status == "running":
                icon = "🔄"
            elif dur is not None and dur > 300:
                icon = "⚠️ slow"
            else:
                icon = "✅"

            status_str = f"{icon} {detail}" if detail else icon
            lines.append(f"| {phase_name} | {dur_str} | {status_str} |")

        # Also show phases not in the standard order
        for phase_name, phase in self._phases.items():
            if phase_name not in phase_order:
                dur = phase.get("duration")
                dur_str = self._format_duration(dur) if dur is not None else "—"
                st = phase.get('status', '?')
                lines.append(f"| {phase_name} | {dur_str} | {st} |")

        lines.append("")

        # ── LLM Performance Summary ──
        if self._llm_calls:
            total_dur = sum(c["duration"] for c in self._llm_calls)
            avg_dur = total_dur / total_llm if total_llm else 0
            max_call = max(self._llm_calls, key=lambda c: c["duration"])
            total_tokens = sum(c.get("tokens", 0) for c in self._llm_calls)
            errors = [c for c in self._llm_calls if c.get("error")]

            lines.append(
                f"## LLM Performance ({total_llm} calls, "
                f"{self._format_duration(total_dur)} total)"
            )
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Total calls | {total_llm} |")
            lines.append(
                f"| Total LLM time | {self._format_duration(total_dur)} |"
            )
            lines.append(f"| Avg call duration | {avg_dur:.1f}s |")
            lines.append(
                f"| Slowest call | {max_call['duration']}s "
                f"({max_call['context'][:40]}) |"
            )
            if total_tokens:
                lines.append(f"| Total tokens | {total_tokens:,} |")
            lines.append(
                f"| Timeouts | {len(timeouts)} |"
            )
            lines.append(
                f"| Errors | {len(errors)} |"
            )
            lines.append("")

            # Show only the 5 slowest calls for debugging
            slowest = sorted(
                self._llm_calls, key=lambda c: c["duration"], reverse=True,
            )[:5]
            lines.append("### Slowest Calls")
            lines.append("")
            lines.append("| # | Context | Duration | Status |")
            lines.append("|---|---------|----------|--------|")
            for i, call in enumerate(slowest, 1):
                ctx = call["context"][:50]
                dur = f"{call['duration']}s"
                if call["timed_out"]:
                    status = "❌ TIMEOUT"
                elif call.get("error"):
                    status = f"❌ {call['error'][:30]}"
                else:
                    status = "✅"
                lines.append(f"| {i} | {ctx} | {dur} | {status} |")

            lines.append("")

        # ── Errors & Warnings ──
        handler = get_diagnostic_handler()
        records = handler.get_records()

        if records:
            lines.append(f"## Errors & Warnings ({len(records)} total)")
            lines.append("")
            lines.append("| Time | Level | Message |")
            lines.append("|------|-------|---------|")

            # Show up to 50 most recent
            for rec in records[-50:]:
                msg = rec["message"][:120].replace("|", "\\|")
                lines.append(
                    f"| {rec['timestamp']} | {rec['level']} | {msg} |"
                )

            if len(records) > 50:
                lines.append(
                    f"| ... | ... | ({len(records) - 50} more not shown) |"
                )

            lines.append("")
        else:
            lines.append("## Errors & Warnings")
            lines.append("")
            lines.append("✅ No warnings or errors recorded.")
            lines.append("")

        # ── Write to disk ──
        reports_dir = settings.BASE_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        filename = f"health_{now.strftime('%Y-%m-%d_%H%M%S')}.md"
        report_path = reports_dir / filename

        report_text = "\n".join(lines)
        report_path.write_text(report_text, encoding="utf-8")

        # Prune old health reports (keep 10)
        from app.utils.logger import prune_old_files
        prune_old_files(reports_dir, "health_*.md")

        logger.info(
            "[HealthTracker] Report written to %s", report_path,
        )

        return str(report_path)

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        """Format seconds into a human-readable string."""
        if seconds is None:
            return "—"
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
