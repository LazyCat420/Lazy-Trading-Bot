"""Execution Service — deterministic safety gating before trade execution.

Accepts a validated TradeAction, enforces safety rules, then executes
via PaperTrader. Every gate is deterministic and testable.
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
from datetime import datetime, timedelta

from app.models.trade_action import TradeAction
from app.services.decision_logger import DecisionLogger
from app.services.risk_rules import RiskRules
from app.utils.logger import logger

# ── Safety gate defaults ─────────────────────────────────────────
_MAX_NOTIONAL = 50_000  # Max dollar value per trade
_MAX_DAILY_TRADES = 20  # Max trades per day per bot
_DUPLICATE_WINDOW_MIN = 5  # Block same symbol+side within N minutes

# Track recent trades for duplicate detection (in-memory, resets on restart)
_recent_trades: list[dict] = []


@track_class_telemetry
class ExecutionService:
    """Deterministic execution with safety gates."""

    def __init__(self, paper_trader) -> None:
        """Args: paper_trader — a PaperTrader instance."""
        self._trader = paper_trader

    async def execute(
        self,
        action: TradeAction,
        decision_id: str,
        *,
        dry_run: bool = True,
        atr: float = 0.0,
        current_price: float = 0.0,
    ) -> dict:
        """Execute a TradeAction through safety gates.

        Args:
            action: Validated TradeAction (BUY or SELL)
            decision_id: ID from DecisionLogger.log_decision()
            dry_run: If True, log everything but skip PaperTrader
            atr: Average True Range for risk rule calculations
            current_price: Latest price for the symbol

        Returns:
            Dict with execution result details
        """
        symbol = action.symbol
        side = action.action.lower()

        if action.action == "HOLD":
            DecisionLogger.update_decision_status(decision_id, "hold")
            return {"status": "hold", "symbol": symbol, "reason": "HOLD decision"}

        # ── Gate 0: Circuit breaker (daily drawdown) ────────────
        from app.services.circuit_breaker import CircuitBreaker

        tripped, cb_reason = CircuitBreaker.is_tripped(action.bot_id)
        if tripped:
            logger.warning(
                "[Execution] CIRCUIT BREAKER tripped for %s: %s",
                symbol,
                cb_reason,
            )
            DecisionLogger.update_decision_status(decision_id, "circuit_breaker")
            return {"status": "circuit_breaker", "symbol": symbol, "reason": cb_reason}

        # ── Get portfolio state ─────────────────────────────────
        portfolio = self._trader.get_portfolio()
        cash = portfolio.get("cash_balance", 0)
        portfolio_value = portfolio.get("total_portfolio_value", cash)
        positions = portfolio.get("positions", [])

        # Find existing position for this symbol
        existing_pos = None
        for p in positions:
            if p.get("ticker") == symbol:
                existing_pos = p
                break

        # ── Gate 1.5: Already-holding guard (BUY only) ──────────
        # Prevent the bot from DCA-ing endlessly into the same stock
        if side == "buy" and existing_pos and existing_pos.get("qty", 0) > 0:
            logger.warning(
                "[Execution] BLOCKED BUY %s: already holding %d shares @ $%.2f",
                symbol,
                existing_pos["qty"],
                existing_pos.get("avg_entry_price", 0),
            )
            DecisionLogger.update_decision_status(decision_id, "skipped_already_held")
            return {
                "status": "skipped",
                "reason": (
                    f"Already holding {existing_pos['qty']} shares of {symbol}. "
                    f"Cannot buy more — sell first or pick a different ticker."
                ),
            }

        # ── Gate 1.7: 24-hour buy cooldown (DB-persisted) ───────
        # Prevents re-buying the same ticker across server restarts
        if side == "buy":
            from app.database import get_db

            try:
                db = get_db()
                last_buy = db.execute(
                    "SELECT MAX(created_at) FROM orders "
                    "WHERE ticker = ? AND side = 'buy' AND bot_id = ? "
                    "AND created_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
                    [symbol, action.bot_id],
                ).fetchone()
                if last_buy and last_buy[0]:
                    logger.warning(
                        "[Execution] BLOCKED BUY %s: bought within last 24h (at %s)",
                        symbol,
                        last_buy[0],
                    )
                    DecisionLogger.update_decision_status(
                        decision_id,
                        "skipped_buy_cooldown",
                    )
                    return {
                        "status": "skipped",
                        "reason": (
                            f"Already bought {symbol} within the last 24 hours "
                            f"(at {str(last_buy[0])[:19]}). "
                            f"Pick a different ticker."
                        ),
                    }
            except Exception as exc:
                logger.debug("[Execution] Buy cooldown check failed: %s", exc)

        # Resolve price
        price = current_price
        if price <= 0:
            # Try to get from existing position
            if existing_pos:
                price = existing_pos.get("avg_entry_price", 0)
            if price <= 0:
                try:
                    import yfinance as yf

                    t = yf.Ticker(symbol)
                    price = t.fast_info.get("lastPrice", 0) or 0
                except Exception:
                    pass

        if price <= 0:
            DecisionLogger.update_decision_status(decision_id, "error_no_price")
            return {"status": "error", "reason": f"could not get price for {symbol}"}

        # ── Gate 2: Compute qty + limits ────────────────────────
        if action.action == "BUY":
            existing_value = 0
            if existing_pos:
                existing_value = existing_pos.get("qty", 0) * existing_pos.get("avg_entry_price", 0)

            qty = RiskRules.compute_qty(
                price=price,
                cash=cash,
                portfolio_value=portfolio_value,
                risk_level=action.risk_level,
                existing_position_value=existing_value,
            )
            if qty <= 0:
                DecisionLogger.update_decision_status(decision_id, "skipped_qty_zero")
                return {"status": "skipped", "reason": "computed qty = 0"}

            notional = price * qty
            if notional > _MAX_NOTIONAL:
                qty = int(_MAX_NOTIONAL / price)
                if qty <= 0:
                    DecisionLogger.update_decision_status(decision_id, "skipped_max_notional")
                    return {"status": "skipped", "reason": "exceeds max notional"}

            valid, reason = RiskRules.validate_trade(price, qty, cash, portfolio_value)
            if not valid:
                DecisionLogger.update_decision_status(decision_id, f"rejected_{reason}")
                return {"status": "rejected", "reason": reason}

        elif action.action == "SELL":
            if not existing_pos or existing_pos.get("qty", 0) <= 0:
                DecisionLogger.update_decision_status(decision_id, "skipped_no_position")
                return {"status": "skipped", "reason": f"no position in {symbol}"}
            qty = existing_pos["qty"]

        else:
            return {"status": "error", "reason": f"unknown action: {action.action}"}

        # ── Gate 3: Duplicate detection ─────────────────────────
        now = datetime.now()
        # Prune expired entries in-place (preserves list reference for tests)
        _recent_trades[:] = [
            t for t in _recent_trades if now - t["ts"] < timedelta(minutes=_DUPLICATE_WINDOW_MIN)
        ]
        for t in _recent_trades:
            if t["symbol"] == symbol and t["side"] == side and t["bot_id"] == action.bot_id:
                DecisionLogger.update_decision_status(decision_id, "skipped_duplicate")
                return {"status": "skipped", "reason": "duplicate order within window"}

        # ── Gate 4: Daily trade limit ───────────────────────────
        today_count = self._trader.get_orders_today_count()
        if today_count >= _MAX_DAILY_TRADES:
            DecisionLogger.update_decision_status(decision_id, "skipped_daily_limit")
            return {"status": "skipped", "reason": "daily trade limit reached"}

        # ── Compute risk levels ─────────────────────────────────
        stop = RiskRules.compute_stop_loss(price, atr, action.risk_level)
        tp = RiskRules.compute_take_profit(price, atr, action.risk_level)

        # ── Dry run: log without executing ──────────────────────
        if dry_run:
            logger.info(
                "[Execution] DRY RUN: %s %d x %s @ $%.2f (stop=$%.2f, tp=$%.2f, confidence=%.2f)",
                side.upper(),
                qty,
                symbol,
                price,
                stop,
                tp,
                action.confidence,
            )
            DecisionLogger.update_decision_status(decision_id, "dry_run")
            DecisionLogger.log_execution(
                decision_id=decision_id,
                filled_qty=qty,
                avg_price=price,
                status="dry_run",
            )
            # Track for duplicate detection even in dry_run
            _recent_trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "bot_id": action.bot_id,
                    "ts": now,
                }
            )
            return {
                "status": "dry_run",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "stop": stop,
                "take_profit": tp,
            }

        # ── Live execution via PaperTrader ──────────────────────
        try:
            if action.action == "BUY":
                order = self._trader.buy(
                    ticker=symbol,
                    qty=qty,
                    price=price,
                    conviction_score=action.confidence,
                    signal="BUY",
                )
                if order:
                    # Set stop-loss and take-profit triggers
                    self._trader.set_triggers_for_position(
                        ticker=symbol,
                        entry_price=price,
                        qty=qty,
                        stop_loss_pct=round((price - stop) / price * 100, 1),
                        take_profit_pct=round((tp - price) / price * 100, 1),
                    )
            else:
                order = self._trader.sell(
                    ticker=symbol,
                    qty=qty,
                    price=price,
                    conviction_score=action.confidence,
                    signal="SELL",
                )

            if not order:
                DecisionLogger.update_decision_status(decision_id, "rejected_by_trader")
                return {"status": "rejected", "reason": "PaperTrader rejected the order"}

            order_id = order.id
            _recent_trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "bot_id": action.bot_id,
                    "ts": now,
                }
            )

            DecisionLogger.update_decision_status(decision_id, "executed")
            DecisionLogger.log_execution(
                decision_id=decision_id,
                order_id=order_id,
                filled_qty=qty,
                avg_price=price,
                status="filled",
            )

            logger.info(
                "[Execution] EXECUTED: %s %d x %s @ $%.2f (order=%s)",
                side.upper(),
                qty,
                symbol,
                price,
                order_id[:8],
            )
            return {
                "status": "executed",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "order_id": order_id,
                "stop": stop,
                "take_profit": tp,
            }

        except Exception as exc:
            logger.error("[Execution] Trade FAILED for %s: %s", symbol, exc)
            DecisionLogger.update_decision_status(decision_id, "error")
            DecisionLogger.log_execution(
                decision_id=decision_id,
                status="failed",
                broker_error=str(exc)[:500],
            )
            return {"status": "error", "symbol": symbol, "reason": str(exc)}
