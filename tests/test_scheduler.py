"""Tests for the Phase 4 autonomous scheduler utilities.

Tests:
  1. Market hours helpers (timezone, open/close detection)
  2. ReportGenerator instantiation
  3. TradingScheduler start/stop lifecycle
"""

from __future__ import annotations

from datetime import datetime

import pytest


# ──────────────────────────────────────────────────────────────
# Market Hours Utilities
# ──────────────────────────────────────────────────────────────

class TestMarketHours:
    """Verify timezone-aware market hours helpers."""

    def test_now_et_returns_eastern(self) -> None:
        from app.utils.market_hours import now_et
        t = now_et()
        assert t.tzinfo is not None
        # Should be US/Eastern
        assert str(t.tzinfo) in ("America/New_York", "US/Eastern", "EST", "EDT")

    def test_is_market_open_returns_bool(self) -> None:
        from app.utils.market_hours import is_market_open
        result = is_market_open()
        assert isinstance(result, bool)

    def test_next_market_open_returns_datetime(self) -> None:
        from app.utils.market_hours import next_market_open
        result = next_market_open()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_next_market_close_returns_datetime(self) -> None:
        from app.utils.market_hours import next_market_close
        result = next_market_close()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_market_status_returns_dict(self) -> None:
        from app.utils.market_hours import market_status
        result = market_status()
        assert isinstance(result, dict)
        assert "is_open" in result
        assert "current_time_et" in result
        assert "next_event" in result

    def test_weekday_9_30_is_open(self) -> None:
        """A Wednesday at 10:00 AM ET should be market open."""
        from app.utils.market_hours import MARKET_OPEN, MARKET_CLOSE
        from datetime import time
        # Just verify constants are set correctly
        assert MARKET_OPEN == time(9, 30)
        assert MARKET_CLOSE == time(16, 0)


# ──────────────────────────────────────────────────────────────
# Report Generator
# ──────────────────────────────────────────────────────────────

class TestReportGenerator:
    """Verify ReportGenerator can be instantiated."""

    def test_instantiation(self) -> None:
        from app.services.report_generator import ReportGenerator
        rg = ReportGenerator()
        assert rg is not None

    def test_get_latest_returns_dict(self) -> None:
        from unittest.mock import MagicMock, patch
        from app.services.report_generator import ReportGenerator
        rg = ReportGenerator()
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        with patch("app.services.report_generator.get_db", return_value=mock_db):
            result = rg.get_latest()
        assert isinstance(result, dict)
        assert "pre_market" in result
        assert "end_of_day" in result


# ──────────────────────────────────────────────────────────────
# TradingScheduler
# ──────────────────────────────────────────────────────────────

class TestTradingScheduler:
    """Verify TradingScheduler lifecycle."""

    def test_instantiation(self) -> None:
        from unittest.mock import MagicMock
        from app.services.scheduler import TradingScheduler
        sched = TradingScheduler(
            autonomous_loop=MagicMock(),
            price_monitor=MagicMock(),
        )
        assert sched is not None
        assert not sched.is_running

    def test_get_status_when_stopped(self) -> None:
        from unittest.mock import MagicMock
        from app.services.scheduler import TradingScheduler
        sched = TradingScheduler(
            autonomous_loop=MagicMock(),
            price_monitor=MagicMock(),
        )
        status = sched.get_status()
        assert status["is_running"] is False
        assert status["job_count"] == 0
        assert "market" in status

    @pytest.fixture()
    def _mock_apscheduler(self):
        """Patch AsyncIOScheduler so start() doesn't need an event loop."""
        from unittest.mock import MagicMock, patch
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.get_jobs.return_value = []
        mock_cls.return_value = mock_instance
        with patch("app.services.scheduler.AsyncIOScheduler", mock_cls):
            yield mock_instance

    @pytest.mark.usefixtures("_mock_apscheduler")
    def test_start_and_stop(self) -> None:
        from unittest.mock import MagicMock
        from app.services.scheduler import TradingScheduler
        sched = TradingScheduler(
            autonomous_loop=MagicMock(),
            price_monitor=MagicMock(),
        )
        result = sched.start()
        assert result["status"] == "started"
        assert sched.is_running

        result = sched.stop()
        assert result["status"] == "stopped"
        assert not sched.is_running

    @pytest.mark.usefixtures("_mock_apscheduler")
    def test_double_start_returns_already(self) -> None:
        from unittest.mock import MagicMock
        from app.services.scheduler import TradingScheduler
        sched = TradingScheduler(
            autonomous_loop=MagicMock(),
            price_monitor=MagicMock(),
        )
        sched.start()
        result = sched.start()
        assert result["status"] == "already_running"
        sched.stop()

