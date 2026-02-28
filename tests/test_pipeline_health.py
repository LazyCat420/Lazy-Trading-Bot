"""Tests for app.services.pipeline_health — HealthTracker + DiagnosticHandler."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.pipeline_health import (
    DiagnosticHandler,
    HealthTracker,
    clear_active_tracker,
    get_active_tracker,
    log_llm_call,
    set_active_tracker,
)


# ── DiagnosticHandler ──────────────────────────────────────────────


class TestDiagnosticHandler:
    def test_captures_warnings_and_errors(self) -> None:
        handler = DiagnosticHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))

        test_logger = logging.getLogger("test_diag")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        test_logger.info("should be ignored")
        test_logger.warning("something bad")
        test_logger.error("something worse")

        records = handler.get_records()
        assert len(records) == 2
        assert records[0]["level"] == "WARNING"
        assert "something bad" in records[0]["message"]
        assert records[1]["level"] == "ERROR"

        # Clean up
        test_logger.removeHandler(handler)

    def test_clear(self) -> None:
        handler = DiagnosticHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))

        test_logger = logging.getLogger("test_diag_clear")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        test_logger.warning("hello")
        assert len(handler.get_records()) == 1

        handler.clear()
        assert len(handler.get_records()) == 0

        test_logger.removeHandler(handler)


# ── HealthTracker ──────────────────────────────────────────────────


class TestHealthTracker:
    def test_phase_tracking(self) -> None:
        tracker = HealthTracker(loop_id="test123")
        tracker.start_phase("discovery")
        tracker.end_phase("discovery", status="success")

        phases = tracker._phases
        assert "discovery" in phases
        assert phases["discovery"]["status"] == "success"
        assert phases["discovery"]["duration"] is not None
        assert phases["discovery"]["duration"] >= 0

    def test_llm_call_tracking(self) -> None:
        tracker = HealthTracker(loop_id="test123")
        tracker.record_llm_call(
            context="MSFT scorecard",
            model="granite-turbo",
            duration_seconds=4.5,
            tokens_used=120,
        )
        tracker.record_llm_call(
            context="Reddit filter",
            model="granite-turbo",
            duration_seconds=300.0,
            timed_out=True,
        )

        assert len(tracker._llm_calls) == 2
        assert tracker._llm_calls[0]["timed_out"] is False
        assert tracker._llm_calls[0]["tokens"] == 120
        assert tracker._llm_calls[1]["timed_out"] is True
        assert tracker._llm_calls[1]["duration"] == 300.0

    def test_custom_checks(self) -> None:
        tracker = HealthTracker(loop_id="test123")
        tracker.record_check("Discovery found tickers", passed=True, detail="5 tickers")
        tracker.record_check("Strategist placed trades", passed=False, detail="0 orders")

        checks = tracker._custom_checks
        assert checks["Discovery found tickers"]["passed"] is True
        assert checks["Strategist placed trades"]["passed"] is False

    def test_generate_report(self, tmp_path: Path) -> None:
        """Report writes valid markdown to disk."""
        with patch("app.services.pipeline_health.settings") as mock_settings:
            mock_settings.BASE_DIR = tmp_path

            tracker = HealthTracker(loop_id="rpt_test")

            # Simulate a phase
            tracker.start_phase("discovery")
            tracker.end_phase("discovery", status="success")

            # Simulate LLM calls
            tracker.record_llm_call(
                context="AAPL scorecard",
                model="test-model",
                duration_seconds=2.1,
            )
            tracker.record_llm_call(
                context="Reddit filter",
                model="test-model",
                duration_seconds=300.0,
                timed_out=True,
            )

            # Simulate checks
            tracker.record_check("Discovery found tickers", passed=True, detail="3")
            tracker.record_check("Strategist placed trades", passed=False, detail="0")

            report_path = tracker.generate_report()

            assert Path(report_path).exists()
            content = Path(report_path).read_text(encoding="utf-8")

            # Verify report structure
            assert "# Pipeline Health Report" in content
            assert "rpt_test" in content
            assert "## Scorecard" in content
            assert "## Phase Timing" in content
            assert "## LLM Calls" in content
            assert "discovery" in content
            assert "✅" in content
            assert "❌" in content
            assert "TIMEOUT" in content

    def test_format_duration(self) -> None:
        assert HealthTracker._format_duration(5.3) == "5.3s"
        assert HealthTracker._format_duration(90.0) == "1m 30s"
        assert HealthTracker._format_duration(None) == "—"


# ── Module-level tracker management ──


class TestTrackerManagement:
    def setup_method(self) -> None:
        clear_active_tracker()

    def test_set_and_get_tracker(self) -> None:
        tracker = HealthTracker(loop_id="mgmt_test")
        set_active_tracker(tracker)
        assert get_active_tracker() is tracker

    def test_clear_tracker(self) -> None:
        tracker = HealthTracker(loop_id="mgmt_test")
        set_active_tracker(tracker)
        clear_active_tracker()
        assert get_active_tracker() is None

    def test_log_llm_call_no_tracker(self) -> None:
        """log_llm_call should be a no-op when no tracker is active."""
        clear_active_tracker()
        # Should not raise
        log_llm_call(context="test", model="test", duration_seconds=1.0)

    def test_log_llm_call_with_tracker(self) -> None:
        tracker = HealthTracker(loop_id="mgmt_test")
        set_active_tracker(tracker)
        log_llm_call(context="test_ctx", model="test_model", duration_seconds=3.5)

        assert len(tracker._llm_calls) == 1
        assert tracker._llm_calls[0]["context"] == "test_ctx"

        clear_active_tracker()
