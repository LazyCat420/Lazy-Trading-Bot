"""Execution Service — deterministic safety gating before trade execution.

Accepts a validated TradeAction, enforces safety rules, then executes
via PaperTrader. Every gate is deterministic and testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.trade_action import TradeAction
from app.services.decision_logger import DecisionLogger
from app.services.risk_rules import RiskRules
from app.utils.logger import logger

# ── Safety gate defaults ─────────────────────────────────────────
_MAX_NOTIONAL = 50_000       # Max dollar value per trade
_MAX_DAILY_TRADES = 20       # Max trades per day per bot
_DUPLICATE_WINDOW_MIN = 5    # Block same symbol+side within N minutes

# Track recent trades for duplicate detection (in-memory, resets on restart)
_recent_trades: list[dict] = []


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

        # ── Gate 1: Market hours ────────────────────────────────
        try:
            from app.utils.market_hours import is_market_open
            if not is_market_open():
                logger.info("[Execution] Market closed — skipping %s %s", side, symbol)
                DecisionLogger.update_decision_status(decision_id, "skipped_market_closed")
                return {"status": "skipped", "reason": "market_closed"}
        except ImportError:
            pass  # market_hours module not available, skip gate

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
                existing_value = (
                    existing_pos.get("qty", 0) * existing_pos.get("avg_entry_price", 0)
                )

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
            t for t in _recent_trades
            if now - t["ts"] < timedelta(minutes=_DUPLICATE_WINDOW_MIN)
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
                "[Execution] DRY RUN: %s %d x %s @ $%.2f "
                "(stop=$%.2f, tp=$%.2f, confidence=%.2f)",
                side.upper(), qty, symbol, price, stop, tp, action.confidence,
            )
            DecisionLogger.update_decision_status(decision_id, "dry_run")
            DecisionLogger.log_execution(
                decision_id=decision_id,
                filled_qty=qty,
                avg_price=price,
                status="dry_run",
            )
            # Track for duplicate detection even in dry_run
            _recent_trades.append({
                "symbol": symbol,
                "side": side,
                "bot_id": action.bot_id,
                "ts": now,
            })
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
            _recent_trades.append({
                "symbol": symbol,
                "side": side,
                "bot_id": action.bot_id,
                "ts": now,
            })

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
                side.upper(), qty, symbol, price, order_id[:8],
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
