"""Paper Trader — simulated order execution with DuckDB persistence.

All state is stored in DuckDB so the portfolio survives server restarts.
Uses yfinance fast_info for live price updates when calculating P&L.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from app.config import settings
from app.database import get_db
from app.models.trading import Order, PortfolioSnapshot
from app.utils.logger import logger


class PaperTrader:
    """Simulated trading engine — buys/sells are recorded but never real."""

    def __init__(self, starting_balance: float | None = None) -> None:
        self._starting_balance = starting_balance
        self._ensure_initial_balance()

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def buy(
        self,
        ticker: str,
        qty: int,
        price: float,
        conviction_score: float = 0.0,
        signal: str = "BUY",
    ) -> Order | None:
        """Execute a simulated buy order.

        Creates/updates a position and deducts cash.
        Returns the filled Order or None if rejected.
        """
        if qty <= 0 or price <= 0:
            logger.warning("[PaperTrader] Rejected buy: invalid qty=%d price=%.2f", qty, price)
            return None

        cost = qty * price
        cash = self.get_cash_balance()

        if cost > cash:
            logger.warning(
                "[PaperTrader] Rejected buy %s: cost=$%.2f > cash=$%.2f",
                ticker, cost, cash,
            )
            return None

        db = get_db()
        now = datetime.now()
        order_id = str(uuid.uuid4())

        # Check if we already hold this ticker (dollar-cost average)
        existing = self._get_position_row(ticker)

        if existing:
            # DCA: average the entry price
            old_qty = existing["qty"]
            old_avg = existing["avg_entry_price"]
            new_qty = old_qty + qty
            new_avg = ((old_avg * old_qty) + (price * qty)) / new_qty

            db.execute(
                """
                UPDATE positions
                SET qty = ?, avg_entry_price = ?, last_updated = ?
                WHERE ticker = ?
                """,
                [new_qty, new_avg, now, ticker],
            )
            logger.info(
                "[PaperTrader] BUY %s: DCA %d+%d=%d shares @ avg $%.2f",
                ticker, old_qty, qty, new_qty, new_avg,
            )
        else:
            # New position
            db.execute(
                """
                INSERT INTO positions (ticker, qty, avg_entry_price, opened_at, last_updated)
                VALUES (?, ?, ?, ?, ?)
                """,
                [ticker, qty, price, now, now],
            )
            logger.info(
                "[PaperTrader] BUY %s: %d shares @ $%.2f ($%.2f)",
                ticker, qty, price, cost,
            )

        # Deduct cash
        self._adjust_cash(-cost)

        # Record the order
        order = Order(
            id=order_id,
            ticker=ticker,
            side="buy",
            qty=qty,
            price=price,
            status="filled",
            filled_at=now,
            created_at=now,
            conviction_score=conviction_score,
            signal=signal,
        )
        self._store_order(order)
        db.commit()

        return order

    def sell(
        self,
        ticker: str,
        qty: int,
        price: float,
        conviction_score: float = 0.0,
        signal: str = "SELL",
    ) -> Order | None:
        """Execute a simulated sell order.

        Reduces/closes a position and adds cash.
        Returns the filled Order or None if rejected.
        """
        if qty <= 0 or price <= 0:
            logger.warning("[PaperTrader] Rejected sell: invalid qty=%d price=%.2f", qty, price)
            return None

        existing = self._get_position_row(ticker)
        if not existing or existing["qty"] <= 0:
            logger.warning("[PaperTrader] Rejected sell %s: no position", ticker)
            return None

        sell_qty = min(qty, existing["qty"])
        proceeds = sell_qty * price
        realized_pnl = (price - existing["avg_entry_price"]) * sell_qty

        db = get_db()
        now = datetime.now()
        order_id = str(uuid.uuid4())

        remaining = existing["qty"] - sell_qty
        if remaining <= 0:
            # Close entire position
            db.execute("DELETE FROM positions WHERE ticker = ?", [ticker])
            logger.info(
                "[PaperTrader] SELL %s: closed %d shares @ $%.2f (P&L=$%.2f)",
                ticker, sell_qty, price, realized_pnl,
            )
        else:
            # Partial close
            db.execute(
                """
                UPDATE positions SET qty = ?, last_updated = ?
                WHERE ticker = ?
                """,
                [remaining, now, ticker],
            )
            logger.info(
                "[PaperTrader] SELL %s: %d/%d shares @ $%.2f (P&L=$%.2f, %d remaining)",
                ticker, sell_qty, existing["qty"], price, realized_pnl, remaining,
            )

        # Add proceeds to cash
        self._adjust_cash(proceeds)

        # Track realized P&L
        self._record_realized_pnl(realized_pnl)

        # Cancel any active triggers for this ticker if fully closed
        if remaining <= 0:
            db.execute(
                "UPDATE price_triggers SET status = 'cancelled' WHERE ticker = ? AND status = 'active'",
                [ticker],
            )

        # Record the order
        order = Order(
            id=order_id,
            ticker=ticker,
            side="sell",
            qty=sell_qty,
            price=price,
            status="filled",
            filled_at=now,
            created_at=now,
            conviction_score=conviction_score,
            signal=signal,
        )
        self._store_order(order)
        db.commit()

        return order

    # ------------------------------------------------------------------
    # Portfolio queries
    # ------------------------------------------------------------------

    def get_cash_balance(self) -> float:
        """Return current cash balance."""
        db = get_db()
        row = db.execute(
            "SELECT cash_balance FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            return float(row[0])
        # Fallback to configured starting balance
        return self._get_starting_balance()

    def get_positions(self) -> list[dict]:
        """Return all open positions as dicts."""
        db = get_db()
        rows = db.execute(
            "SELECT ticker, qty, avg_entry_price, stop_loss, take_profit, "
            "trailing_stop_pct, opened_at, last_updated FROM positions"
        ).fetchall()

        positions = []
        for r in rows:
            positions.append({
                "ticker": r[0],
                "qty": r[1],
                "avg_entry_price": round(r[2], 2),
                "stop_loss": round(r[3] or 0, 2),
                "take_profit": round(r[4] or 0, 2),
                "trailing_stop_pct": round(r[5] or 0, 2),
                "opened_at": str(r[6]) if r[6] else None,
                "last_updated": str(r[7]) if r[7] else None,
            })
        return positions

    def get_portfolio(self) -> dict:
        """Return full portfolio summary."""
        cash = self.get_cash_balance()
        positions = self.get_positions()

        total_positions_value = sum(
            p["qty"] * p["avg_entry_price"] for p in positions
        )
        total_value = cash + total_positions_value

        return {
            "cash_balance": round(cash, 2),
            "positions_count": len(positions),
            "total_positions_value": round(total_positions_value, 2),
            "total_portfolio_value": round(total_value, 2),
            "realized_pnl": round(self._get_realized_pnl(), 2),
            "positions": positions,
        }

    def get_orders(self, limit: int = 50) -> list[dict]:
        """Return order history."""
        db = get_db()
        rows = db.execute(
            "SELECT id, ticker, side, qty, price, order_type, status, "
            "conviction_score, signal, filled_at, created_at "
            "FROM orders ORDER BY created_at DESC LIMIT ?",
            [limit],
        ).fetchall()

        return [
            {
                "id": r[0],
                "ticker": r[1],
                "side": r[2],
                "qty": r[3],
                "price": round(r[4], 2),
                "order_type": r[5],
                "status": r[6],
                "conviction_score": round(r[7] or 0, 2),
                "signal": r[8] or "",
                "filled_at": str(r[9]) if r[9] else None,
                "created_at": str(r[10]) if r[10] else None,
            }
            for r in rows
        ]

    def get_orders_today_count(self) -> int:
        """Count how many orders were placed today."""
        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) FROM orders WHERE CAST(created_at AS DATE) = CURRENT_DATE"
        ).fetchone()
        return row[0] if row else 0

    def get_daily_pnl_pct(self) -> float:
        """Calculate today's P&L as a % of portfolio value."""
        db = get_db()
        # Get today's realized P&L from orders
        row = db.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN side = 'sell' THEN qty * price ELSE -qty * price END
            ), 0.0)
            FROM orders
            WHERE CAST(created_at AS DATE) = CURRENT_DATE
            """
        ).fetchone()
        daily_pnl = row[0] if row else 0.0

        portfolio = self.get_portfolio()
        total_value = portfolio["total_portfolio_value"]
        if total_value <= 0:
            return 0.0

        return round((daily_pnl / total_value) * 100, 2)

    def get_last_sell_date(self, ticker: str) -> date | None:
        """Get the date this ticker was last sold."""
        db = get_db()
        row = db.execute(
            "SELECT MAX(CAST(filled_at AS DATE)) FROM orders "
            "WHERE ticker = ? AND side = 'sell'",
            [ticker],
        ).fetchone()
        if row and row[0]:
            return row[0]
        return None

    def get_triggers(self) -> list[dict]:
        """Return all active price triggers."""
        db = get_db()
        rows = db.execute(
            "SELECT id, ticker, trigger_type, trigger_price, high_water_mark, "
            "trailing_pct, action, qty, status, created_at "
            "FROM price_triggers WHERE status = 'active'"
        ).fetchall()

        return [
            {
                "id": r[0],
                "ticker": r[1],
                "trigger_type": r[2],
                "trigger_price": round(r[3], 2),
                "high_water_mark": round(r[4] or 0, 2),
                "trailing_pct": round(r[5] or 0, 2),
                "action": r[6],
                "qty": r[7],
                "status": r[8],
                "created_at": str(r[9]) if r[9] else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Triggers: create
    # ------------------------------------------------------------------

    def set_triggers_for_position(
        self,
        ticker: str,
        entry_price: float,
        qty: int,
        stop_loss_pct: float = 5.0,
        take_profit_pct: float = 15.0,
        trailing_stop_pct: float = 0.0,
    ) -> list[dict]:
        """Create stop-loss and take-profit triggers for a new position."""
        db = get_db()
        triggers = []

        # Stop-loss
        sl_price = round(entry_price * (1 - stop_loss_pct / 100), 2)
        sl_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO price_triggers
                (id, ticker, trigger_type, trigger_price, action, qty, status, created_at)
            VALUES (?, ?, 'stop_loss', ?, 'sell', ?, 'active', ?)
            """,
            [sl_id, ticker, sl_price, qty, datetime.now()],
        )
        triggers.append({"id": sl_id, "type": "stop_loss", "price": sl_price})

        # Update position stop_loss field
        db.execute(
            "UPDATE positions SET stop_loss = ? WHERE ticker = ?",
            [sl_price, ticker],
        )

        # Take-profit
        tp_price = round(entry_price * (1 + take_profit_pct / 100), 2)
        tp_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO price_triggers
                (id, ticker, trigger_type, trigger_price, action, qty, status, created_at)
            VALUES (?, ?, 'take_profit', ?, 'sell', ?, 'active', ?)
            """,
            [tp_id, ticker, tp_price, qty, datetime.now()],
        )
        triggers.append({"id": tp_id, "type": "take_profit", "price": tp_price})

        # Update position take_profit field
        db.execute(
            "UPDATE positions SET take_profit = ? WHERE ticker = ?",
            [tp_price, ticker],
        )

        # Trailing stop (optional)
        if trailing_stop_pct > 0:
            ts_id = str(uuid.uuid4())
            ts_price = round(entry_price * (1 - trailing_stop_pct / 100), 2)
            db.execute(
                """
                INSERT INTO price_triggers
                    (id, ticker, trigger_type, trigger_price, high_water_mark,
                     trailing_pct, action, qty, status, created_at)
                VALUES (?, ?, 'trailing_stop', ?, ?, ?, 'sell', ?, 'active', ?)
                """,
                [ts_id, ticker, ts_price, entry_price, trailing_stop_pct, qty, datetime.now()],
            )
            triggers.append({"id": ts_id, "type": "trailing_stop", "price": ts_price})

            db.execute(
                "UPDATE positions SET trailing_stop_pct = ? WHERE ticker = ?",
                [trailing_stop_pct, ticker],
            )

        db.commit()
        logger.info(
            "[PaperTrader] Set %d triggers for %s: SL=$%.2f, TP=$%.2f",
            len(triggers), ticker, sl_price, tp_price,
        )
        return triggers

    # ------------------------------------------------------------------
    # Portfolio snapshots
    # ------------------------------------------------------------------

    def take_snapshot(self) -> PortfolioSnapshot:
        """Record a portfolio snapshot to DuckDB."""
        portfolio = self.get_portfolio()
        snap = PortfolioSnapshot(
            cash_balance=portfolio["cash_balance"],
            total_positions_value=portfolio["total_positions_value"],
            total_portfolio_value=portfolio["total_portfolio_value"],
            realized_pnl=portfolio["realized_pnl"],
            unrealized_pnl=0.0,  # Would need live prices
        )

        db = get_db()
        db.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, cash_balance, total_positions_value,
                 total_portfolio_value, realized_pnl, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                snap.timestamp,
                snap.cash_balance,
                snap.total_positions_value,
                snap.total_portfolio_value,
                snap.realized_pnl,
                snap.unrealized_pnl,
            ],
        )
        db.commit()
        logger.info(
            "[PaperTrader] Snapshot: cash=$%.2f, positions=$%.2f, total=$%.2f",
            snap.cash_balance, snap.total_positions_value, snap.total_portfolio_value,
        )
        return snap

    def get_portfolio_history(self, limit: int = 100) -> list[dict]:
        """Return portfolio snapshots for equity curve chart."""
        db = get_db()
        rows = db.execute(
            "SELECT timestamp, cash_balance, total_positions_value, "
            "total_portfolio_value, realized_pnl, unrealized_pnl "
            "FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?",
            [limit],
        ).fetchall()

        return [
            {
                "timestamp": str(r[0]),
                "cash_balance": round(r[1], 2),
                "total_positions_value": round(r[2], 2),
                "total_portfolio_value": round(r[3], 2),
                "realized_pnl": round(r[4] or 0, 2),
                "unrealized_pnl": round(r[5] or 0, 2),
            }
            for r in reversed(rows)  # oldest first for charting
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_position_row(self, ticker: str) -> dict | None:
        """Get a position row from DuckDB."""
        db = get_db()
        row = db.execute(
            "SELECT ticker, qty, avg_entry_price FROM positions WHERE ticker = ?",
            [ticker],
        ).fetchone()
        if not row:
            return None
        return {"ticker": row[0], "qty": row[1], "avg_entry_price": row[2]}

    def _store_order(self, order: Order) -> None:
        """Persist an order to DuckDB."""
        db = get_db()
        db.execute(
            """
            INSERT INTO orders
                (id, ticker, side, qty, price, order_type, status,
                 conviction_score, signal, filled_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                order.id,
                order.ticker,
                order.side,
                order.qty,
                order.price,
                order.order_type,
                order.status,
                order.conviction_score,
                order.signal,
                order.filled_at,
                order.created_at,
            ],
        )

    def _adjust_cash(self, amount: float) -> None:
        """Adjust cash balance by taking a new snapshot with updated cash."""
        current_cash = self.get_cash_balance()
        new_cash = current_cash + amount

        db = get_db()
        # Get current positions value
        positions = self.get_positions()
        positions_value = sum(p["qty"] * p["avg_entry_price"] for p in positions)

        db.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, cash_balance, total_positions_value,
                 total_portfolio_value, realized_pnl, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, 0.0)
            """,
            [
                datetime.now(),
                new_cash,
                positions_value,
                new_cash + positions_value,
                self._get_realized_pnl(),
            ],
        )

    def _get_realized_pnl(self) -> float:
        """Get cumulative realized P&L from closed trades."""
        db = get_db()
        row = db.execute(
            "SELECT realized_pnl FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return float(row[0]) if row and row[0] else 0.0

    def _record_realized_pnl(self, pnl: float) -> None:
        """Add to cumulative realized P&L (will be captured in next snapshot)."""
        # The realized P&L is tracked via snapshots — we just log it for now
        current = self._get_realized_pnl()
        logger.info(
            "[PaperTrader] Realized P&L: $%.2f (cumulative: $%.2f)",
            pnl, current + pnl,
        )

    def _ensure_initial_balance(self) -> None:
        """If no snapshots exist, create the initial one from config."""
        db = get_db()
        row = db.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()
        if row and row[0] > 0:
            return  # Already initialized

        starting = self._get_starting_balance()
        db.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, cash_balance, total_positions_value,
                 total_portfolio_value, realized_pnl, unrealized_pnl)
            VALUES (?, ?, 0.0, ?, 0.0, 0.0)
            """,
            [datetime.now(), starting, starting],
        )
        db.commit()
        logger.info("[PaperTrader] Initialized with $%.2f starting balance", starting)

    def _get_starting_balance(self) -> float:
        """Get starting balance from constructor or risk_params.json."""
        if self._starting_balance is not None:
            return self._starting_balance

        path = settings.USER_CONFIG_DIR / "risk_params.json"
        if path.exists():
            try:
                params = json.loads(path.read_text(encoding="utf-8"))
                return float(params.get("account_size_usd", 10000))
            except (json.JSONDecodeError, OSError):
                pass
        return 10000.0
