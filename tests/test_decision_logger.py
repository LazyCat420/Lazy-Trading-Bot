"""Tests for DecisionLogger — DB insert/query round-trip."""

from __future__ import annotations

import pytest

from app.database import _init_tables, get_db
from app.models.trade_action import TradeAction
from app.services.decision_logger import DecisionLogger


@pytest.fixture(autouse=True)
def init_tables():
    """Ensure audit tables exist in the test DB."""
    conn = get_db()
    _init_tables(conn)


class TestDecisionLogger:
    """Test decision + execution persistence."""

    def test_log_decision_returns_id(self):
        action = TradeAction(
            action="BUY",
            symbol="NVDA",
            confidence=0.85,
            rationale="Test decision",
            bot_id="test_bot",
        )
        decision_id = DecisionLogger.log_decision(action, raw_llm="test raw")
        assert decision_id
        assert len(decision_id) == 36  # UUID format

    def test_log_execution_returns_id(self):
        action = TradeAction(
            action="SELL",
            symbol="AAPL",
            confidence=0.60,
            rationale="Test sell",
            bot_id="test_bot",
        )
        decision_id = DecisionLogger.log_decision(action)
        exec_id = DecisionLogger.log_execution(
            decision_id=decision_id,
            order_id="order_123",
            filled_qty=10,
            avg_price=150.0,
            status="filled",
        )
        assert exec_id
        assert len(exec_id) == 36

    def test_get_decisions_returns_list(self):
        action = TradeAction(
            action="HOLD",
            symbol="TSLA",
            confidence=0.45,
            rationale="Neutral",
            bot_id="query_bot",
        )
        DecisionLogger.log_decision(action)
        decisions = DecisionLogger.get_decisions("query_bot", limit=10)
        assert len(decisions) >= 1
        assert decisions[0]["symbol"] == "TSLA"
        assert decisions[0]["action"] == "HOLD"

    def test_get_decision_with_execution(self):
        action = TradeAction(
            action="BUY",
            symbol="GOOG",
            confidence=0.90,
            rationale="Strong buy signal",
            bot_id="detail_bot",
        )
        d_id = DecisionLogger.log_decision(action)
        DecisionLogger.log_execution(
            decision_id=d_id,
            filled_qty=5,
            avg_price=175.0,
            status="filled",
        )
        result = DecisionLogger.get_decision_with_execution(d_id)
        assert result
        assert result["decision"]["symbol"] == "GOOG"
        assert result["execution"] is not None
        assert result["execution"]["filled_qty"] == 5

    def test_update_decision_status(self):
        action = TradeAction(
            action="BUY",
            symbol="META",
            confidence=0.75,
            rationale="Test status update",
            bot_id="status_bot",
        )
        d_id = DecisionLogger.log_decision(action, status="pending")
        DecisionLogger.update_decision_status(d_id, "executed")
        result = DecisionLogger.get_decision_with_execution(d_id)
        assert result["decision"]["status"] == "executed"

    def test_missing_decision_returns_empty(self):
        result = DecisionLogger.get_decision_with_execution("nonexistent-id")
        assert result == {}
