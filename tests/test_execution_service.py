"""Tests for ExecutionService — safety gates, dry-run, duplicate detection.

Uses freezegun for time-dependent tests, mocks for PaperTrader,
and patches market_hours to avoid real-time gate blocking.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.database import _init_tables, get_db
from app.models.trade_action import TradeAction
from app.services.decision_logger import DecisionLogger
from app.services.execution_service import ExecutionService, _recent_trades


@pytest.fixture(autouse=True)
def init_tables():
    """Ensure audit tables exist."""
    conn = get_db()
    _init_tables(conn)


@pytest.fixture(autouse=True)
def clear_recent_trades():
    """Reset duplicate detection between tests."""
    _recent_trades.clear()
    yield
    _recent_trades.clear()


def _mock_trader(cash: float = 50_000, positions: list | None = None) -> MagicMock:
    """Create a mock PaperTrader with configurable state."""
    trader = MagicMock()
    trader.get_portfolio.return_value = {
        "cash_balance": cash,
        "total_portfolio_value": cash + 50_000,
        "positions": positions or [],
    }
    trader.get_orders_today_count.return_value = 0
    mock_order = MagicMock()
    mock_order.id = "mock-order-123"
    trader.buy.return_value = mock_order
    trader.sell.return_value = mock_order
    return trader


def _market_open_patch():
    """Context manager to patch market hours as always open."""
    return patch(
        "app.utils.market_hours.is_market_open",
        return_value=True,
    )


class TestDryRun:
    """Dry-run should log but not execute."""

    @pytest.mark.asyncio
    async def test_dry_run_buy(self):
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="NVDA", confidence=0.85,
            rationale="Test", bot_id="test",
        )
        d_id = DecisionLogger.log_decision(action)
        with _market_open_patch():
            result = await executor.execute(
                action, d_id, dry_run=True, current_price=500.0,
            )
        assert result["status"] == "dry_run"
        assert result["symbol"] == "NVDA"
        assert result["qty"] > 0
        trader.buy.assert_not_called()

    @pytest.mark.asyncio
    async def test_hold_decision_skips_execution(self):
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="HOLD", symbol="AAPL", confidence=0.50,
            rationale="Neutral", bot_id="test",
        )
        d_id = DecisionLogger.log_decision(action)
        result = await executor.execute(action, d_id, dry_run=False)
        assert result["status"] == "hold"
        trader.buy.assert_not_called()
        trader.sell.assert_not_called()


class TestSafetyGates:
    """Test individual safety gates."""

    @pytest.mark.asyncio
    async def test_no_price_returns_error(self):
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="FAKE", confidence=0.80,
            rationale="Test", bot_id="test",
        )
        d_id = DecisionLogger.log_decision(action)
        with _market_open_patch():
            result = await executor.execute(
                action, d_id, dry_run=True, current_price=0,
            )
        assert result["status"] == "error"
        assert "price" in result["reason"]

    @pytest.mark.asyncio
    async def test_sell_no_position_skipped(self):
        trader = _mock_trader(positions=[])
        executor = ExecutionService(trader)
        action = TradeAction(
            action="SELL", symbol="NVDA", confidence=0.75,
            rationale="Take profit", bot_id="test",
        )
        d_id = DecisionLogger.log_decision(action)
        with _market_open_patch():
            result = await executor.execute(
                action, d_id, dry_run=False, current_price=500.0,
            )
        assert result["status"] == "skipped"
        assert "no position" in result["reason"]

    @pytest.mark.asyncio
    async def test_market_closed_blocks_trade(self):
        """Market hours gate should block when market is closed."""
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="NVDA", confidence=0.85,
            rationale="Test", bot_id="test",
        )
        d_id = DecisionLogger.log_decision(action)
        with patch(
            "app.utils.market_hours.is_market_open",
            return_value=False,
        ):
            result = await executor.execute(
                action, d_id, dry_run=False, current_price=500.0,
            )
        assert result["status"] == "skipped"
        assert "market" in result["reason"]


class TestDuplicateDetection:
    """Same symbol+side within 5 min should be blocked."""

    @pytest.mark.asyncio
    async def test_duplicate_blocked(self):
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="NVDA", confidence=0.85,
            rationale="First buy", bot_id="test",
        )

        with _market_open_patch():
            d_id1 = DecisionLogger.log_decision(action)
            result1 = await executor.execute(
                action, d_id1, dry_run=True, current_price=500.0,
            )
            assert result1["status"] == "dry_run"

            # Second identical within 5 min should be blocked
            d_id2 = DecisionLogger.log_decision(action)
            result2 = await executor.execute(
                action, d_id2, dry_run=True, current_price=500.0,
            )
            assert result2["status"] == "skipped"
            assert "duplicate" in result2["reason"]


class TestLiveExecution:
    """Test live execution path (dry_run=False)."""

    @pytest.mark.asyncio
    async def test_live_buy_calls_trader(self):
        trader = _mock_trader()
        executor = ExecutionService(trader)
        action = TradeAction(
            action="BUY", symbol="GOOG", confidence=0.90,
            rationale="Strong buy", bot_id="live_test",
        )
        d_id = DecisionLogger.log_decision(action)
        with _market_open_patch():
            result = await executor.execute(
                action, d_id, dry_run=False, current_price=180.0,
            )
        assert result["status"] == "executed"
        trader.buy.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_sell_calls_trader(self):
        positions = [{"ticker": "AAPL", "qty": 10, "avg_entry_price": 150.0}]
        trader = _mock_trader(positions=positions)
        executor = ExecutionService(trader)
        action = TradeAction(
            action="SELL", symbol="AAPL", confidence=0.70,
            rationale="Take profit", bot_id="live_test",
        )
        d_id = DecisionLogger.log_decision(action)
        with _market_open_patch():
            result = await executor.execute(
                action, d_id, dry_run=False, current_price=170.0,
            )
        assert result["status"] == "executed"
        trader.sell.assert_called_once()
