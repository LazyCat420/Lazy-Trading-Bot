"""Tests for CircuitBreaker — daily drawdown kill switch.

Uses the shared test DuckDB from conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.database import _init_tables, get_db
from app.models.trade_action import TradeAction
from app.services.circuit_breaker import CircuitBreaker, MAX_DAILY_DRAWDOWN_PCT
from app.services.decision_logger import DecisionLogger
from app.services.execution_service import ExecutionService, _recent_trades


@pytest.fixture(autouse=True)
def init_tables():
    """Ensure all tables exist."""
    conn = get_db()
    _init_tables(conn)


@pytest.fixture(autouse=True)
def clean_circuit_breaker():
    """Reset circuit breaker state between tests."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM circuit_breaker_state")
    except Exception:
        pass
    yield
    try:
        conn.execute("DELETE FROM circuit_breaker_state")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_snapshots():
    """Clean portfolio_snapshots between tests."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM portfolio_snapshots")
    except Exception:
        pass
    yield
    try:
        conn.execute("DELETE FROM portfolio_snapshots")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clear_recent_trades():
    """Reset duplicate detection between tests."""
    _recent_trades.clear()
    yield
    _recent_trades.clear()


class TestCircuitBreakerLogic:
    """Test the math and state of the circuit breaker."""

    def test_not_tripped_no_data(self):
        """No snapshots → breaker should be open."""
        tripped, reason = CircuitBreaker.is_tripped("test_bot")
        assert tripped is False
        assert reason == ""

    def test_not_tripped_small_drawdown(self):
        """2% drawdown should NOT trip (threshold is 5%)."""
        conn = get_db()
        now = datetime.now()
        # Peak at 100k, current at 98k = 2% drawdown
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(timestamp, cash_balance, total_portfolio_value, bot_id) "
            "VALUES (?, ?, ?, ?)",
            [now - timedelta(hours=12), 50000, 100000, "test_bot"],
        )
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(timestamp, cash_balance, total_portfolio_value, bot_id) "
            "VALUES (?, ?, ?, ?)",
            [now, 49000, 98000, "test_bot"],
        )
        tripped, reason = CircuitBreaker.is_tripped("test_bot")
        assert tripped is False

    def test_tripped_large_drawdown(self):
        """6% drawdown should trip the breaker."""
        conn = get_db()
        now = datetime.now()
        # Peak at 100k, current at 94k = 6% drawdown
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(timestamp, cash_balance, total_portfolio_value, bot_id) "
            "VALUES (?, ?, ?, ?)",
            [now - timedelta(hours=12), 50000, 100000, "test_bot"],
        )
        conn.execute(
            "INSERT INTO portfolio_snapshots "
            "(timestamp, cash_balance, total_portfolio_value, bot_id) "
            "VALUES (?, ?, ?, ?)",
            [now, 47000, 94000, "test_bot"],
        )
        tripped, reason = CircuitBreaker.is_tripped("test_bot")
        assert tripped is True
        assert "6.0%" in reason
        assert "exceeds" in reason

    def test_reset_clears_trip(self):
        """After reset, breaker should be open even with large drawdown persisted."""
        conn = get_db()
        # Persist a tripped state
        conn.execute(
            "INSERT INTO circuit_breaker_state "
            "(bot_id, is_tripped, tripped_at, reason) "
            "VALUES (?, TRUE, ?, ?)",
            ["test_bot", datetime.now(), "test trip"],
        )
        tripped, _ = CircuitBreaker.is_tripped("test_bot")
        assert tripped is True

        # Reset
        result = CircuitBreaker.reset("test_bot")
        assert result["status"] == "reset"

        tripped2, _ = CircuitBreaker.is_tripped("test_bot")
        assert tripped2 is False

    def test_get_status_returns_dict(self):
        """Status endpoint should return a complete dict."""
        status = CircuitBreaker.get_status("test_bot")
        assert "bot_id" in status
        assert "is_tripped" in status
        assert "threshold_pct" in status
        assert status["threshold_pct"] == MAX_DAILY_DRAWDOWN_PCT


class TestCircuitBreakerInExecution:
    """Test that ExecutionService respects the circuit breaker."""

    @pytest.mark.asyncio
    async def test_execution_blocked_when_tripped(self):
        """If circuit breaker is tripped, execution should return circuit_breaker status."""
        conn = get_db()
        # Persist a tripped state
        conn.execute(
            "INSERT INTO circuit_breaker_state "
            "(bot_id, is_tripped, tripped_at, reason) "
            "VALUES (?, TRUE, ?, ?)",
            ["test_cb", datetime.now(), "test drawdown"],
        )

        trader = MagicMock()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="NVDA", confidence=0.85,
            rationale="Test", bot_id="test_cb",
        )
        d_id = DecisionLogger.log_decision(action)
        result = await executor.execute(
            action, d_id, dry_run=True, current_price=500.0,
        )
        assert result["status"] == "circuit_breaker"
        assert "drawdown" in result["reason"]
        trader.buy.assert_not_called()

    @pytest.mark.asyncio
    async def test_execution_passes_when_not_tripped(self):
        """Normal execution should work when breaker is not tripped."""
        trader = MagicMock()
        trader.get_portfolio.return_value = {
            "cash_balance": 50000,
            "total_portfolio_value": 100000,
            "positions": [],
        }
        trader.get_orders_today_count.return_value = 0

        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="GOOG", confidence=0.85,
            rationale="Test", bot_id="test_notrip",
        )
        d_id = DecisionLogger.log_decision(action)
        result = await executor.execute(
            action, d_id, dry_run=True, current_price=150.0,
        )
        # Should get through to dry_run, not circuit_breaker
        assert result["status"] == "dry_run"
