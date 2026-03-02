"""LLM Audit Logger — persists every LLM prompt/response to DuckDB.

Every call through LLMService.chat() is logged non-blockingly to the
`llm_audit_logs` table for full prompt/response traceability.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime

from app.utils.logger import logger


def _get_conn():
    """Lazy import to avoid circular dependency at module load."""
    from app.database import get_db

    return get_db()


class LLMAuditLogger:
    """Non-blocking audit logger for LLM interactions."""

    @staticmethod
    def log(
        *,
        cycle_id: str = "",
        ticker: str = "",
        agent_step: str = "",
        system_prompt: str = "",
        user_context: str = "",
        raw_response: str = "",
        parsed_json: dict | None = None,
        tokens_used: int = 0,
        execution_time_ms: int = 0,
        model: str = "",
    ) -> str:
        """Insert a single audit row. Returns the log ID."""
        log_id = str(uuid.uuid4())
        try:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO llm_audit_logs (
                    id, cycle_id, ticker, agent_step,
                    system_prompt, user_context, raw_response,
                    parsed_json, tokens_used, execution_time_ms,
                    model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    log_id,
                    cycle_id,
                    ticker,
                    agent_step,
                    system_prompt[:10_000],     # Cap to prevent DB bloat
                    user_context[:50_000],      # Large context is fine
                    raw_response[:50_000],
                    json.dumps(parsed_json) if parsed_json else None,
                    tokens_used,
                    execution_time_ms,
                    model,
                    datetime.now(),
                ],
            )
            logger.debug(
                "[LLMAudit] Logged %s: %s/%s (%dms, %d tokens)",
                log_id[:8], ticker or "global", agent_step, execution_time_ms, tokens_used,
            )
        except Exception as exc:
            # Never let audit logging crash the trading pipeline
            logger.warning("[LLMAudit] Failed to log: %s", exc)
        return log_id

    @staticmethod
    def get_logs_for_ticker(ticker: str, limit: int = 20) -> list[dict]:
        """Fetch recent audit logs for a specific ticker."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT id, cycle_id, ticker, agent_step,
                       system_prompt, user_context, raw_response,
                       parsed_json, tokens_used, execution_time_ms,
                       model, created_at
                FROM llm_audit_logs
                WHERE ticker = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [ticker, limit],
            ).fetchall()
            columns = [
                "id", "cycle_id", "ticker", "agent_step",
                "system_prompt", "user_context", "raw_response",
                "parsed_json", "tokens_used", "execution_time_ms",
                "model", "created_at",
            ]
            results = []
            for row in rows:
                d = dict(zip(columns, row, strict=False))
                # Parse JSON back to dict if present
                if d["parsed_json"]:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        d["parsed_json"] = json.loads(d["parsed_json"])
                d["created_at"] = str(d["created_at"])
                results.append(d)
            return results
        except Exception as exc:
            logger.warning("[LLMAudit] Query failed: %s", exc)
            return []

    @staticmethod
    def get_logs_for_cycle(cycle_id: str) -> list[dict]:
        """Fetch all audit logs for a specific trading cycle."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT id, cycle_id, ticker, agent_step,
                       system_prompt, user_context, raw_response,
                       parsed_json, tokens_used, execution_time_ms,
                       model, created_at
                FROM llm_audit_logs
                WHERE cycle_id = ?
                ORDER BY created_at ASC
                """,
                [cycle_id],
            ).fetchall()
            columns = [
                "id", "cycle_id", "ticker", "agent_step",
                "system_prompt", "user_context", "raw_response",
                "parsed_json", "tokens_used", "execution_time_ms",
                "model", "created_at",
            ]
            results = []
            for row in rows:
                d = dict(zip(columns, row, strict=False))
                if d["parsed_json"]:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        d["parsed_json"] = json.loads(d["parsed_json"])
                d["created_at"] = str(d["created_at"])
                results.append(d)
            return results
        except Exception as exc:
            logger.warning("[LLMAudit] Cycle query failed: %s", exc)
            return []

    @staticmethod
    def get_recent_logs(limit: int = 50) -> list[dict]:
        """Fetch the most recent audit logs across all tickers."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT id, cycle_id, ticker, agent_step,
                       tokens_used, execution_time_ms, model, created_at
                FROM llm_audit_logs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
            columns = [
                "id", "cycle_id", "ticker", "agent_step",
                "tokens_used", "execution_time_ms", "model", "created_at",
            ]
            return [
                {**dict(zip(columns, row, strict=False)), "created_at": str(row[-1])}
                for row in rows
            ]
        except Exception as exc:
            logger.warning("[LLMAudit] Recent query failed: %s", exc)
            return []
