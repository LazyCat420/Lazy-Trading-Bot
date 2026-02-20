"""Report Generator — pre-market and end-of-day summary reports.

Generates structured JSON reports stored in the `reports` DuckDB table.
Consumed by the frontend for display in the scheduler panel.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

from app.database import get_db
from app.utils.logger import logger
from app.utils.market_hours import now_et


class ReportGenerator:
    """Generate pre-market and EOD reports from trading data."""

    def generate_pre_market(self, loop_result: dict | None = None) -> dict:
        """Generate a pre-market briefing after the full loop runs.

        Summarizes: new discoveries, watchlist changes, signals, orders placed.
        """
        db = get_db()
        today = date.today()
        now = now_et()

        # Discoveries today
        discoveries = db.execute(
            "SELECT ticker, source, discovery_score "
            "FROM discovered_tickers "
            "WHERE discovered_at >= CURRENT_DATE "
            "ORDER BY discovery_score DESC LIMIT 10"
        ).fetchall()

        # Watchlist state
        watchlist = db.execute(
            "SELECT ticker, status, confidence_score "
            "FROM watchlist WHERE status = 'active' "
            "ORDER BY confidence_score DESC"
        ).fetchall()

        # Orders placed today
        orders = db.execute(
            "SELECT ticker, side, qty, price, signal, conviction "
            "FROM orders "
            "WHERE created_at >= CURRENT_DATE "
            "ORDER BY created_at DESC"
        ).fetchall()

        # Portfolio snapshot
        portfolio = db.execute(
            "SELECT cash_balance, total_portfolio_value, total_positions_value "
            "FROM portfolio_snapshots "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchall()

        report = {
            "generated_at": now.isoformat(),
            "report_date": str(today),
            "discoveries": [
                {"ticker": r[0], "source": r[1], "mentions": r[2]}
                for r in discoveries
            ],
            "watchlist": [
                {"ticker": r[0], "status": r[1], "confidence": r[2]}
                for r in watchlist
            ],
            "orders_today": [
                {
                    "ticker": r[0],
                    "side": r[1],
                    "qty": r[2],
                    "price": r[3],
                    "signal": r[4],
                    "conviction": r[5],
                }
                for r in orders
            ],
            "portfolio": {
                "cash": portfolio[0][0] if portfolio else 0,
                "total_value": portfolio[0][1] if portfolio else 0,
                "positions_count": portfolio[0][2] if portfolio else 0,
            },
            "loop_result": loop_result,
        }

        # Persist to DB
        report_id = str(uuid.uuid4())[:8]
        db.execute(
            "INSERT INTO reports (id, report_type, report_date, content) "
            "VALUES (?, ?, ?, ?)",
            [report_id, "pre_market", str(today), json.dumps(report)],
        )
        db.commit()

        logger.info(
            "[ReportGenerator] Pre-market report saved (id=%s, orders=%d)",
            report_id,
            len(orders),
        )
        return report

    def generate_eod(self) -> dict:
        """Generate end-of-day report.

        Summarizes: portfolio value, today's fills, P&L, score decay.
        """
        db = get_db()
        today = date.today()
        now = now_et()

        # Portfolio snapshot
        portfolio = db.execute(
            "SELECT cash_balance, total_portfolio_value, total_positions_value "
            "FROM portfolio_snapshots "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchall()

        # All positions
        positions = db.execute(
            "SELECT ticker, qty, avg_entry_price, cost_basis "
            "FROM positions WHERE status = 'open'"
        ).fetchall()

        # Today's orders
        orders = db.execute(
            "SELECT ticker, side, qty, price, signal, conviction, status "
            "FROM orders "
            "WHERE created_at >= CURRENT_DATE "
            "ORDER BY created_at"
        ).fetchall()

        # Active triggers
        triggers = db.execute(
            "SELECT ticker, trigger_type, trigger_price "
            "FROM price_triggers WHERE status = 'active'"
        ).fetchall()

        # Apply score decay to discovery scores (0.8× daily)
        decay_result = self._apply_score_decay(db)

        report = {
            "generated_at": now.isoformat(),
            "report_date": str(today),
            "portfolio": {
                "cash": portfolio[0][0] if portfolio else 0,
                "total_value": portfolio[0][1] if portfolio else 0,
                "positions_count": portfolio[0][2] if portfolio else 0,
            },
            "open_positions": [
                {
                    "ticker": r[0],
                    "qty": r[1],
                    "avg_entry": r[2],
                    "cost_basis": r[3],
                }
                for r in positions
            ],
            "todays_orders": [
                {
                    "ticker": r[0],
                    "side": r[1],
                    "qty": r[2],
                    "price": r[3],
                    "signal": r[4],
                    "conviction": r[5],
                    "status": r[6],
                }
                for r in orders
            ],
            "active_triggers": [
                {"ticker": r[0], "type": r[1], "price": r[2]}
                for r in triggers
            ],
            "score_decay": decay_result,
        }

        # Persist to DB
        report_id = str(uuid.uuid4())[:8]
        db.execute(
            "INSERT INTO reports (id, report_type, report_date, content) "
            "VALUES (?, ?, ?, ?)",
            [report_id, "end_of_day", str(today), json.dumps(report)],
        )
        db.commit()

        logger.info(
            "[ReportGenerator] EOD report saved (id=%s, positions=%d, orders=%d)",
            report_id,
            len(positions),
            len(orders),
        )
        return report

    def get_latest(self) -> dict:
        """Get the most recent pre-market and EOD reports."""
        db = get_db()

        pre_market = db.execute(
            "SELECT content, created_at FROM reports "
            "WHERE report_type = 'pre_market' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        eod = db.execute(
            "SELECT content, created_at FROM reports "
            "WHERE report_type = 'end_of_day' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        return {
            "pre_market": json.loads(pre_market[0]) if pre_market else None,
            "pre_market_at": str(pre_market[1]) if pre_market else None,
            "end_of_day": json.loads(eod[0]) if eod else None,
            "end_of_day_at": str(eod[1]) if eod else None,
        }

    @staticmethod
    def _apply_score_decay(db: object) -> dict:
        """Apply 0.8× daily decay to all ticker_scores.

        This ensures stale tickers lose priority over time.
        """
        try:
            result = db.execute(
                "UPDATE ticker_scores SET agg_score = agg_score * 0.8 "
                "WHERE agg_score > 0.1"
            )
            affected = result.fetchone()
            count = affected[0] if affected else 0
            db.commit()
            logger.info("[ReportGenerator] Score decay applied to %s tickers", count)
            return {"decayed_count": count, "factor": 0.8}
        except Exception as e:
            logger.warning("[ReportGenerator] Score decay failed: %s", e)
            return {"decayed_count": 0, "error": str(e)}
