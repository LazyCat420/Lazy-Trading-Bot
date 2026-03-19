"""Paper Trader — simulated order execution with DuckDB persistence.

All state is stored in DuckDB so the portfolio survives server restarts.
Uses yfinance fast_info for live price updates when calculating P&L.

Multi-bot support: Each PaperTrader instance is scoped to a bot_id.
All queries filter by bot_id so multiple bots have isolated portfolios.
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import json
import uuid
from datetime import date, datetime

from app.config import settings
from app.database import get_db
from app.models.trading import Order, PortfolioSnapshot
from app.utils.logger import logger


@track_class_telemetry
class PaperTrader:
    """Simulated trading engine — buys/sells are recorded but never real."""

    def __init__(
        self,
        starting_balance: float | None = None,
        bot_id: str = "default",
    ) -> None:
        self._starting_balance = starting_balance
        self.bot_id = bot_id
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
                WHERE ticker = ? AND bot_id = ?
                """,
                [new_qty, new_avg, now, ticker, self.bot_id],
            )
            logger.info(
                "[PaperTrader:%s] BUY %s: DCA %d+%d=%d shares @ avg $%.2f",
                self.bot_id, ticker, old_qty, qty, new_qty, new_avg,
            )
        else:
            # New position — use INSERT OR REPLACE as a safety net in case
            # _get_position_row missed an orphaned row from old schema
            db.execute(
                """
                INSERT OR REPLACE INTO positions
                    (ticker, qty, avg_entry_price, opened_at, last_updated, bot_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [ticker, qty, price, now, now, self.bot_id],
            )
            logger.info(
                "[PaperTrader:%s] BUY %s: %d shares @ $%.2f ($%.2f)",
                self.bot_id, ticker, qty, price, cost,
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
            db.execute(
                "DELETE FROM positions WHERE ticker = ? AND bot_id = ?",
                [ticker, self.bot_id],
            )
            logger.info(
                "[PaperTrader:%s] SELL %s: closed %d shares @ $%.2f (P&L=$%.2f)",
                self.bot_id, ticker, sell_qty, price, realized_pnl,
            )
        else:
            # Partial close
            db.execute(
                """
                UPDATE positions SET qty = ?, last_updated = ?
                WHERE ticker = ? AND bot_id = ?
                """,
                [remaining, now, ticker, self.bot_id],
            )
            logger.info(
                "[PaperTrader:%s] SELL %s: %d/%d shares @ $%.2f (P&L=$%.2f, %d remaining)",
                self.bot_id, ticker, sell_qty, existing["qty"], price, realized_pnl, remaining,
            )

        # Add proceeds to cash
        self._adjust_cash(proceeds)

        # Track realized P&L
        self._record_realized_pnl(realized_pnl)

        # Cancel any active triggers for this ticker if fully closed
        if remaining <= 0:
            db.execute(
                "UPDATE price_triggers SET status = 'cancelled' "
                "WHERE ticker = ? AND bot_id = ? AND status = 'active'",
                [ticker, self.bot_id],
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

        # ── Alpha attribution: update source credibility ──
        self._update_source_credibility(ticker, realized_pnl)

        return order

    # ------------------------------------------------------------------
    # Portfolio queries
    # ------------------------------------------------------------------

    def get_cash_balance(self) -> float:
        """Return current cash balance."""
        db = get_db()
        row = db.execute(
            "SELECT cash_balance FROM portfolio_snapshots "
            "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT 1",
            [self.bot_id],
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
            "trailing_stop_pct, opened_at, last_updated FROM positions "
            "WHERE bot_id = ?",
            [self.bot_id],
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
            "FROM orders WHERE bot_id = ? ORDER BY created_at DESC LIMIT ?",
            [self.bot_id, limit],
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
            "SELECT COUNT(*) FROM orders "
            "WHERE bot_id = ? AND CAST(created_at AS DATE) = CURRENT_DATE",
            [self.bot_id],
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
            WHERE bot_id = ? AND CAST(created_at AS DATE) = CURRENT_DATE
            """,
            [self.bot_id],
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
            "WHERE ticker = ? AND side = 'sell' AND bot_id = ?",
            [ticker, self.bot_id],
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
            "FROM price_triggers WHERE status = 'active' AND bot_id = ?",
            [self.bot_id],
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
                (id, ticker, trigger_type, trigger_price, action, qty, status, created_at, bot_id)
            VALUES (?, ?, 'stop_loss', ?, 'sell', ?, 'active', ?, ?)
            """,
            [sl_id, ticker, sl_price, qty, datetime.now(), self.bot_id],
        )
        triggers.append({"id": sl_id, "type": "stop_loss", "price": sl_price})

        # Update position stop_loss field
        db.execute(
            "UPDATE positions SET stop_loss = ? WHERE ticker = ? AND bot_id = ?",
            [sl_price, ticker, self.bot_id],
        )

        # Take-profit
        tp_price = round(entry_price * (1 + take_profit_pct / 100), 2)
        tp_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO price_triggers
                (id, ticker, trigger_type, trigger_price, action, qty, status, created_at, bot_id)
            VALUES (?, ?, 'take_profit', ?, 'sell', ?, 'active', ?, ?)
            """,
            [tp_id, ticker, tp_price, qty, datetime.now(), self.bot_id],
        )
        triggers.append({"id": tp_id, "type": "take_profit", "price": tp_price})

        # Update position take_profit field
        db.execute(
            "UPDATE positions SET take_profit = ? WHERE ticker = ? AND bot_id = ?",
            [tp_price, ticker, self.bot_id],
        )

        # Trailing stop (optional)
        if trailing_stop_pct > 0:
            ts_id = str(uuid.uuid4())
            ts_price = round(entry_price * (1 - trailing_stop_pct / 100), 2)
            db.execute(
                """
                INSERT INTO price_triggers
                    (id, ticker, trigger_type, trigger_price, high_water_mark,
                     trailing_pct, action, qty, status, created_at, bot_id)
                VALUES (?, ?, 'trailing_stop', ?, ?, ?, 'sell', ?, 'active', ?, ?)
                """,
                [ts_id, ticker, ts_price, entry_price, trailing_stop_pct, qty,
                 datetime.now(), self.bot_id],
            )
            triggers.append({"id": ts_id, "type": "trailing_stop", "price": ts_price})

            db.execute(
                "UPDATE positions SET trailing_stop_pct = ? WHERE ticker = ? AND bot_id = ?",
                [trailing_stop_pct, ticker, self.bot_id],
            )

        db.commit()
        logger.info(
            "[PaperTrader:%s] Set %d triggers for %s: SL=$%.2f, TP=$%.2f",
            self.bot_id, len(triggers), ticker, sl_price, tp_price,
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
                 total_portfolio_value, realized_pnl, unrealized_pnl, bot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snap.timestamp,
                snap.cash_balance,
                snap.total_positions_value,
                snap.total_portfolio_value,
                snap.realized_pnl,
                snap.unrealized_pnl,
                self.bot_id,
            ],
        )
        db.commit()
        logger.info(
            "[PaperTrader:%s] Snapshot: cash=$%.2f, positions=$%.2f, total=$%.2f",
            self.bot_id, snap.cash_balance, snap.total_positions_value,
            snap.total_portfolio_value,
        )
        return snap

    def get_portfolio_history(self, limit: int = 100) -> list[dict]:
        """Return portfolio snapshots for equity curve chart."""
        db = get_db()
        rows = db.execute(
            "SELECT timestamp, cash_balance, total_positions_value, "
            "total_portfolio_value, realized_pnl, unrealized_pnl "
            "FROM portfolio_snapshots WHERE bot_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            [self.bot_id, limit],
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
            "SELECT ticker, qty, avg_entry_price FROM positions "
            "WHERE ticker = ? AND bot_id = ?",
            [ticker, self.bot_id],
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
                 conviction_score, signal, filled_at, created_at, bot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                self.bot_id,
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
                 total_portfolio_value, realized_pnl, unrealized_pnl, bot_id)
            VALUES (?, ?, ?, ?, ?, 0.0, ?)
            """,
            [
                datetime.now(),
                new_cash,
                positions_value,
                new_cash + positions_value,
                self._get_realized_pnl(),
                self.bot_id,
            ],
        )

    def _get_realized_pnl(self) -> float:
        """Get cumulative realized P&L from closed trades."""
        db = get_db()
        row = db.execute(
            "SELECT realized_pnl FROM portfolio_snapshots "
            "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT 1",
            [self.bot_id],
        ).fetchone()
        return float(row[0]) if row and row[0] else 0.0

    def _record_realized_pnl(self, pnl: float) -> None:
        """Add to cumulative realized P&L (will be captured in next snapshot)."""
        # The realized P&L is tracked via snapshots — we just log it for now
        current = self._get_realized_pnl()
        logger.info(
            "[PaperTrader:%s] Realized P&L: $%.2f (cumulative: $%.2f)",
            self.bot_id, pnl, current + pnl,
        )

    def _update_source_credibility(self, ticker: str, realized_pnl: float) -> None:
        """Update source credibility table based on realized P&L.

        Looks up the original discovery source for this ticker and
        adjusts its win/loss count and trust score.
        """
        try:
            db = get_db()
            # Find the most recent discovery source for this ticker
            row = db.execute(
                "SELECT source, source_detail FROM discovered_tickers "
                "WHERE ticker = ? ORDER BY discovered_at DESC LIMIT 1",
                [ticker],
            ).fetchone()
            if not row or not row[0]:
                return

            source_type = row[0]  # e.g., 'reddit', 'youtube'
            source_detail = row[1] or source_type  # e.g., 'r/wallstreetbets'
            source_id = f"{source_type}:{source_detail}"

            now = datetime.now()
            is_win = realized_pnl > 0

            # Upsert: create or update the source_credibility row
            existing = db.execute(
                "SELECT win_count, loss_count, total_pnl FROM source_credibility "
                "WHERE source_id = ?",
                [source_id],
            ).fetchone()

            if existing:
                win = existing[0] + (1 if is_win else 0)
                loss = existing[1] + (0 if is_win else 1)
                total = existing[2] + realized_pnl
                trust = max(0.1, win / (win + loss)) if (win + loss) > 0 else 0.5
                db.execute(
                    "UPDATE source_credibility "
                    "SET win_count = ?, loss_count = ?, total_pnl = ?, "
                    "trust_score = ?, last_updated = ? "
                    "WHERE source_id = ?",
                    [win, loss, total, round(trust, 3), now, source_id],
                )
            else:
                win = 1 if is_win else 0
                loss = 0 if is_win else 1
                trust = max(0.1, win / (win + loss)) if (win + loss) > 0 else 0.5
                db.execute(
                    "INSERT INTO source_credibility "
                    "(source_id, source_type, win_count, loss_count, "
                    "total_pnl, trust_score, last_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [source_id, source_type, win, loss,
                     realized_pnl, round(trust, 3), now],
                )

            db.commit()
            logger.info(
                "[PaperTrader:%s] Source credibility updated: %s → "
                "%s (pnl=$%.2f, trust=%.2f)",
                self.bot_id, source_id,
                "WIN" if is_win else "LOSS",
                realized_pnl, trust,
            )
        except Exception as exc:
            # Non-critical — don't let attribution tracking break trading
            logger.warning(
                "[PaperTrader:%s] Source credibility update failed: %s",
                self.bot_id, exc,
            )

    def _ensure_initial_balance(self) -> None:
        """If no snapshots exist for this bot, create the initial one."""
        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE bot_id = ?",
            [self.bot_id],
        ).fetchone()
        if row and row[0] > 0:
            return  # Already initialized

        starting = self._get_starting_balance()
        db.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, cash_balance, total_positions_value,
                 total_portfolio_value, realized_pnl, unrealized_pnl, bot_id)
            VALUES (?, ?, 0.0, ?, 0.0, 0.0, ?)
            """,
            [datetime.now(), starting, starting, self.bot_id],
        )
        db.commit()
        logger.info(
            "[PaperTrader:%s] Initialized with $%.2f starting balance",
            self.bot_id, starting,
        )

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

    def reset_portfolio(self, new_balance: float | None = None) -> dict:
        """Wipe all trading data for this bot and reinitialize.

        Clears: positions, orders, price_triggers, portfolio_snapshots (bot-scoped).
        Then creates a fresh starting snapshot with the given balance.
        If new_balance is None, reads from risk_params.json.
        """
        balance = new_balance if new_balance is not None else self._get_starting_balance()
        db = get_db()

        # Wipe all trading tables (bot-scoped only)
        db.execute("DELETE FROM positions WHERE bot_id = ?", [self.bot_id])
        db.execute("DELETE FROM orders WHERE bot_id = ?", [self.bot_id])
        db.execute("DELETE FROM price_triggers WHERE bot_id = ?", [self.bot_id])
        db.execute("DELETE FROM portfolio_snapshots WHERE bot_id = ?", [self.bot_id])

        # Create fresh starting snapshot
        db.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, cash_balance, total_positions_value,
                 total_portfolio_value, realized_pnl, unrealized_pnl, bot_id)
            VALUES (?, ?, 0.0, ?, 0.0, 0.0, ?)
            """,
            [datetime.now(), balance, balance, self.bot_id],
        )
        db.commit()

        # Also update the risk_params.json so the value persists
        if new_balance is not None and self.bot_id == "default":
            path = settings.USER_CONFIG_DIR / "risk_params.json"
            if path.exists():
                try:
                    params = json.loads(path.read_text(encoding="utf-8"))
                    params["account_size_usd"] = new_balance
                    path.write_text(
                        json.dumps(params, indent=2) + "\n", encoding="utf-8",
                    )
                except (json.JSONDecodeError, OSError):
                    pass

        logger.info(
            "[PaperTrader:%s] Portfolio RESET — new balance: $%.2f",
            self.bot_id, balance,
        )
        return {
            "status": "reset",
            "bot_id": self.bot_id,
            "new_balance": balance,
            "positions_cleared": True,
            "orders_cleared": True,
            "triggers_cleared": True,
        }
