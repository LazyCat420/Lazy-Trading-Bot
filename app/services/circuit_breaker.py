"""Circuit Breaker — daily drawdown kill switch.

If the portfolio drops ≥ MAX_DAILY_DRAWDOWN_PCT within 24 hours,
halt all new BUY/SELL orders until manually reset.

Pure math — no LLM calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.utils.logger import logger

# ── Configuration ────────────────────────────────────────────────
MAX_DAILY_DRAWDOWN_PCT = 5.0  # Trip at 5% daily loss


def _get_conn():
    """Lazy import to avoid circular dependency at module load."""
    from app.database import get_db

    return get_db()


class CircuitBreaker:
    """Daily drawdown circuit breaker.

    Checks portfolio_snapshots over the last 24h.  If the portfolio
    value has dropped by ≥ MAX_DAILY_DRAWDOWN_PCT from its 24h high,
    the breaker trips and blocks all new trades until manually reset.
    """

    @staticmethod
    def is_tripped(bot_id: str = "default") -> tuple[bool, str]:
        """Check if the circuit breaker is currently tripped.

        Returns (is_tripped, reason).
        """
        conn = _get_conn()

        # 1. Check persisted trip state first
        try:
            row = conn.execute(
                "SELECT is_tripped, reason FROM circuit_breaker_state "
                "WHERE bot_id = ?",
                [bot_id],
            ).fetchone()
            if row and row[0]:
                return True, row[1] or "circuit breaker tripped"
        except Exception as exc:
            logger.debug("[CircuitBreaker] State query failed: %s", exc)

        # 2. Compute 24h drawdown from portfolio_snapshots
        try:
            cutoff = datetime.now() - timedelta(hours=24)
            rows = conn.execute(
                "SELECT total_portfolio_value, timestamp "
                "FROM portfolio_snapshots "
                "WHERE bot_id = ? AND timestamp >= ? "
                "ORDER BY timestamp ASC",
                [bot_id, cutoff],
            ).fetchall()

            if len(rows) < 2:
                # Not enough data to compute drawdown
                return False, ""

            # Peak value in the window
            peak = max(r[0] for r in rows if r[0] and r[0] > 0)
            # Latest value
            current = rows[-1][0]

            if peak <= 0:
                return False, ""

            drawdown_pct = ((peak - current) / peak) * 100

            if drawdown_pct >= MAX_DAILY_DRAWDOWN_PCT:
                reason = (
                    f"24h drawdown {drawdown_pct:.1f}% "
                    f"(peak=${peak:,.0f} → current=${current:,.0f}) "
                    f"exceeds {MAX_DAILY_DRAWDOWN_PCT}% limit"
                )
                logger.warning("[CircuitBreaker] TRIPPED: %s", reason)
                CircuitBreaker._trip(bot_id, reason)
                return True, reason

        except Exception as exc:
            logger.warning("[CircuitBreaker] Drawdown check failed: %s", exc)

        return False, ""

    @staticmethod
    def _trip(bot_id: str, reason: str) -> None:
        """Persist the tripped state."""
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM circuit_breaker_state WHERE bot_id = ?",
                [bot_id],
            )
            conn.execute(
                "INSERT INTO circuit_breaker_state "
                "(bot_id, is_tripped, tripped_at, reason) "
                "VALUES (?, TRUE, ?, ?)",
                [bot_id, datetime.now(), reason],
            )
        except Exception as exc:
            logger.warning("[CircuitBreaker] Failed to persist trip: %s", exc)

    @staticmethod
    def reset(bot_id: str = "default") -> dict:
        """Manually reset the circuit breaker.

        Returns status dict.
        """
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM circuit_breaker_state WHERE bot_id = ?",
                [bot_id],
            )
            conn.execute(
                "INSERT INTO circuit_breaker_state "
                "(bot_id, is_tripped, reset_at) "
                "VALUES (?, FALSE, ?)",
                [bot_id, datetime.now()],
            )
            logger.info("[CircuitBreaker] Reset for bot_id=%s", bot_id)
            return {"status": "reset", "bot_id": bot_id}
        except Exception as exc:
            logger.warning("[CircuitBreaker] Reset failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    @staticmethod
    def get_status(bot_id: str = "default") -> dict:
        """Get circuit breaker status for API/UI."""
        tripped, reason = CircuitBreaker.is_tripped(bot_id)
        conn = _get_conn()

        last_reset = None
        tripped_at = None
        try:
            row = conn.execute(
                "SELECT tripped_at, reset_at FROM circuit_breaker_state "
                "WHERE bot_id = ?",
                [bot_id],
            ).fetchone()
            if row:
                tripped_at = str(row[0]) if row[0] else None
                last_reset = str(row[1]) if row[1] else None
        except Exception:
            pass

        return {
            "bot_id": bot_id,
            "is_tripped": tripped,
            "reason": reason,
            "tripped_at": tripped_at,
            "last_reset": last_reset,
            "threshold_pct": MAX_DAILY_DRAWDOWN_PCT,
        }
