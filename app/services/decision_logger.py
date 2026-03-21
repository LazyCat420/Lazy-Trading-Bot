"""Decision Logger — persist TradeAction decisions and execution outcomes to DuckDB.

Provides a full audit trail so every decision is queryable from the UI,
not buried in terminal logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.database import get_db
from app.models.trade_action import TradeAction
from app.utils.logger import logger


class DecisionLogger:
    """Persist trading decisions and execution outcomes."""

    @staticmethod
    def log_decision(
        action: TradeAction,
        raw_llm: str = "",
        *,
        status: str = "pending",
        rejection_reason: str = "",
    ) -> str:
        """Log a TradeAction. Returns decision_id.
        
        DISABLED: DuckDB insert removed — trade_decisions now in MongoDB only.
        """
        decision_id = str(uuid.uuid4())
        logger.info(
            "[DecisionLogger] Decision %s: %s %s (confidence=%.2f, status=%s)",
            decision_id[:8],
            action.action,
            action.symbol,
            action.confidence,
            status,
        )
        return decision_id

    @staticmethod
    def log_execution(
        decision_id: str,
        order_id: str = "",
        filled_qty: int = 0,
        avg_price: float = 0.0,
        status: str = "pending",
        broker_error: str = "",
    ) -> str:
        """Log trade execution. Returns execution_id.
        
        DISABLED: DuckDB insert removed — trade_executions now in MongoDB only.
        """
        execution_id = str(uuid.uuid4())
        logger.info(
            "[DecisionLogger] Execution %s for decision %s (status=%s, qty=%d, price=%.2f)",
            execution_id[:8],
            decision_id[:8],
            status,
            filled_qty,
            avg_price,
        )
        return execution_id

    @staticmethod
    def update_decision_status(decision_id: str, status: str) -> None:
        """Update the status of a decision (e.g. pending → executed)."""
        db = get_db()
        try:
            db.execute(
                "UPDATE trade_decisions SET status = ? WHERE id = ?",
                [status, decision_id],
            )
        except Exception as exc:
            logger.error("[DecisionLogger] Failed to update status: %s", exc)

    @staticmethod
    def get_decisions(bot_id: str, limit: int = 50) -> list[dict]:
        """Query recent decisions for a bot."""
        db = get_db()
        try:
            rows = db.execute(
                """
                SELECT id, bot_id, symbol, ts, action, confidence,
                       rationale, risk_level, status, rejection_reason
                FROM trade_decisions
                WHERE bot_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                [bot_id, limit],
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "bot_id": r[1],
                    "symbol": r[2],
                    "ts": str(r[3]),
                    "action": r[4],
                    "confidence": r[5],
                    "rationale": r[6],
                    "risk_level": r[7],
                    "status": r[8],
                    "rejection_reason": r[9],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("[DecisionLogger] Failed to get decisions: %s", exc)
            return []

    @staticmethod
    def get_decision_with_execution(decision_id: str) -> dict:
        """Join decision + execution for debugging."""
        db = get_db()
        try:
            row = db.execute(
                """
                SELECT d.id, d.bot_id, d.symbol, d.ts, d.action,
                       d.confidence, d.rationale, d.risk_level,
                       d.raw_llm_response, d.status, d.rejection_reason,
                       e.id, e.order_id, e.ts, e.filled_qty,
                       e.avg_price, e.status, e.broker_error
                FROM trade_decisions d
                LEFT JOIN trade_executions e ON e.decision_id = d.id
                WHERE d.id = ?
                """,
                [decision_id],
            ).fetchone()
            if not row:
                return {}
            return {
                "decision": {
                    "id": row[0],
                    "bot_id": row[1],
                    "symbol": row[2],
                    "ts": str(row[3]),
                    "action": row[4],
                    "confidence": row[5],
                    "rationale": row[6],
                    "risk_level": row[7],
                    "raw_llm_response": row[8],
                    "status": row[9],
                    "rejection_reason": row[10],
                },
                "execution": {
                    "id": row[11],
                    "order_id": row[12],
                    "ts": str(row[13]) if row[13] else None,
                    "filled_qty": row[14],
                    "avg_price": row[15],
                    "status": row[16],
                    "broker_error": row[17],
                } if row[11] else None,
            }
        except Exception as exc:
            logger.error("[DecisionLogger] Failed to get decision detail: %s", exc)
            return {}
