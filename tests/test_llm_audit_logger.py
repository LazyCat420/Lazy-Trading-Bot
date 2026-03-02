"""Tests for LLMAuditLogger — DB insert/query round-trip."""

from __future__ import annotations

from app.database import _init_tables, get_db
from app.services.llm_audit_logger import LLMAuditLogger


class TestLLMAuditLogger:
    """Verify audit log persistence and queries."""

    def test_log_and_query_by_ticker(self):
        conn = get_db()
        _init_tables(conn)

        log_id = LLMAuditLogger.log(
            ticker="NVDA",
            agent_step="trading_decision",
            system_prompt="You are a trader.",
            user_context="NVDA is up 5%",
            raw_response='{"action":"BUY","confidence":0.85}',
            parsed_json={"action": "BUY", "confidence": 0.85},
            tokens_used=120,
            execution_time_ms=450,
            model="test-model",
        )
        assert log_id

        logs = LLMAuditLogger.get_logs_for_ticker("NVDA", limit=5)
        assert len(logs) >= 1
        entry = logs[0]
        assert entry["ticker"] == "NVDA"
        assert entry["agent_step"] == "trading_decision"
        assert entry["tokens_used"] == 120
        assert entry["execution_time_ms"] == 450
        assert entry["model"] == "test-model"
        assert entry["parsed_json"]["action"] == "BUY"

    def test_log_and_query_recent(self):
        conn = get_db()
        _init_tables(conn)

        LLMAuditLogger.log(
            ticker="AAPL",
            agent_step="test",
            raw_response="hello",
        )
        logs = LLMAuditLogger.get_recent_logs(limit=5)
        assert len(logs) >= 1

    def test_log_and_query_by_cycle(self):
        conn = get_db()
        _init_tables(conn)

        cycle = "test-cycle-123"
        LLMAuditLogger.log(
            cycle_id=cycle,
            ticker="MSFT",
            agent_step="decision",
        )
        LLMAuditLogger.log(
            cycle_id=cycle,
            ticker="GOOG",
            agent_step="decision",
        )
        logs = LLMAuditLogger.get_logs_for_cycle(cycle)
        assert len(logs) >= 2

    def test_empty_query(self):
        conn = get_db()
        _init_tables(conn)

        logs = LLMAuditLogger.get_logs_for_ticker("NONEXISTENT")
        assert logs == []
