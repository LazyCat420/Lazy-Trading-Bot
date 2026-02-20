"""Signal Router — converts TickerDossier conviction into trading orders.

Reads the dossier's conviction_score and risk_params.json to decide:
  • conviction ≥ 0.7  →  BUY (size from risk params)
  • conviction ≤ 0.3  →  SELL (close entire position)
  • 0.3 < conv < 0.7  →  HOLD (no action)

Safety guards:
  • Max position size (% of portfolio)
  • Max portfolio allocation (total invested %)
  • Max orders per day
  • Daily loss limit
  • Cooldown period after selling a ticker
"""

from __future__ import annotations

import json
import math
from datetime import date

from app.config import settings
from app.utils.logger import logger


class SignalRouter:
    """Convert dossier conviction scores into sized trading orders."""

    BUY_THRESHOLD = 0.55
    SELL_THRESHOLD = 0.30

    # Tiered position sizing: higher conviction → larger position
    CONVICTION_TIERS = [
        (0.80, 1.00),   # 80%+ conviction → 100% of max position
        (0.65, 0.75),   # 65-80% conviction → 75% of max position
        (0.55, 0.50),   # 55-65% conviction → 50% of max position
    ]

    def __init__(self) -> None:
        self._risk_params = self._load_risk_params()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def evaluate(
        self,
        ticker: str,
        conviction_score: float,
        current_price: float,
        cash_balance: float,
        total_portfolio_value: float,
        existing_position_qty: int = 0,
        orders_today: int = 0,
        daily_pnl_pct: float = 0.0,
        last_sold_date: date | None = None,
    ) -> dict | None:
        """Evaluate a dossier signal and return an order dict or None.

        Returns:
            {
                "ticker": str,
                "side": "buy" | "sell",
                "qty": int,
                "price": float,
                "signal": "BUY" | "SELL",
                "conviction": float,
                "reason": str,
            }
            or None if no action should be taken.
        """
        self._risk_params = self._load_risk_params()

        # ── Guard: daily order limit ────────────────────────────────
        max_orders = self._risk_params.get("max_orders_per_day", 10)
        if orders_today >= max_orders:
            logger.info(
                "[SignalRouter] %s SKIP — daily order limit reached (%d/%d)",
                ticker, orders_today, max_orders,
            )
            return None

        # ── Guard: daily loss limit ─────────────────────────────────
        daily_loss_limit = self._risk_params.get("daily_loss_limit_pct", 5.0)
        if daily_pnl_pct <= -daily_loss_limit:
            logger.info(
                "[SignalRouter] %s SKIP — daily loss limit hit (%.1f%%)",
                ticker, daily_pnl_pct,
            )
            return None

        # ── Guard: cooldown after selling ───────────────────────────
        cooldown_days = self._risk_params.get("cooldown_days", 7)
        if last_sold_date and conviction_score >= self.BUY_THRESHOLD:
            days_since_sell = (date.today() - last_sold_date).days
            if days_since_sell < cooldown_days:
                logger.info(
                    "[SignalRouter] %s SKIP — cooldown (%d/%d days since last sell)",
                    ticker, days_since_sell, cooldown_days,
                )
                return None

        # ── BUY signal ──────────────────────────────────────────────
        if conviction_score >= self.BUY_THRESHOLD:
            if existing_position_qty > 0:
                logger.info(
                    "[SignalRouter] %s HOLD — already holding %d shares",
                    ticker, existing_position_qty,
                )
                return None

            # Tiered sizing: scale position by conviction level
            tier_scale = self._get_conviction_tier_scale(conviction_score)
            qty = self._calculate_position_size(
                current_price, cash_balance, total_portfolio_value,
            )
            qty = math.floor(qty * tier_scale)
            if qty <= 0:
                logger.info(
                    "[SignalRouter] %s SKIP — position size is 0 "
                    "(price=$%.2f, cash=$%.2f, tier_scale=%.0f%%)",
                    ticker, current_price, cash_balance, tier_scale * 100,
                )
                return None

            logger.info(
                "[SignalRouter] %s → BUY %d @ $%.2f "
                "(conviction=%.2f, tier_scale=%.0f%%)",
                ticker, qty, current_price, conviction_score, tier_scale * 100,
            )
            return {
                "ticker": ticker,
                "side": "buy",
                "qty": qty,
                "price": current_price,
                "signal": "BUY",
                "conviction": conviction_score,
                "reason": (
                    f"Conviction {conviction_score:.2f} ≥ {self.BUY_THRESHOLD} "
                    f"(tier: {tier_scale:.0%} of max position)"
                ),
            }

        # ── SELL signal ─────────────────────────────────────────────
        if conviction_score <= self.SELL_THRESHOLD:
            if existing_position_qty <= 0:
                logger.info(
                    "[SignalRouter] %s SKIP SELL — no position to close",
                    ticker,
                )
                return None

            logger.info(
                "[SignalRouter] %s → SELL %d @ $%.2f (conviction=%.2f)",
                ticker, existing_position_qty, current_price, conviction_score,
            )
            return {
                "ticker": ticker,
                "side": "sell",
                "qty": existing_position_qty,
                "price": current_price,
                "signal": "SELL",
                "conviction": conviction_score,
                "reason": f"Conviction {conviction_score:.2f} ≤ {self.SELL_THRESHOLD}",
            }

        # ── HOLD or PASS — conviction between thresholds ──────────
        if existing_position_qty > 0:
            # We own it — genuine HOLD
            logger.info(
                "[SignalRouter] %s → HOLD (conviction=%.2f, holding %d shares)",
                ticker, conviction_score, existing_position_qty,
            )
        else:
            # We don't own it — PASS (conviction not high enough to buy)
            logger.info(
                "[SignalRouter] %s → PASS (conviction=%.2f, not enough to buy)",
                ticker, conviction_score,
            )
        return None

    def _get_conviction_tier_scale(self, conviction: float) -> float:
        """Return position size multiplier based on conviction level.

        Higher conviction → larger position.
        """
        for threshold, scale in self.CONVICTION_TIERS:
            if conviction >= threshold:
                return scale
        return 0.50  # fallback for threshold-level conviction

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _calculate_position_size(
        self,
        price: float,
        cash_balance: float,
        total_portfolio_value: float,
    ) -> int:
        """Calculate how many shares to buy based on risk params.

        Caps at both max_position_size_pct and max_portfolio_allocation_pct.
        """
        if price <= 0 or cash_balance <= 0:
            return 0

        max_pos_pct = self._risk_params.get("max_position_size_pct", 10.0) / 100
        max_alloc_pct = self._risk_params.get("max_portfolio_allocation_pct", 30.0) / 100

        # Max dollars for this single position
        max_position_dollars = total_portfolio_value * max_pos_pct

        # Max dollars available considering total allocation limit
        # (total_portfolio_value * max_alloc - currently_invested)
        # Simplified: just use cash available capped by position limit
        max_spend = min(max_position_dollars, cash_balance)

        # Don't exceed total allocation limit
        invested = total_portfolio_value - cash_balance
        remaining_allocation = (total_portfolio_value * max_alloc_pct) - invested
        if remaining_allocation <= 0:
            logger.info("[SignalRouter] Max portfolio allocation reached")
            return 0

        max_spend = min(max_spend, remaining_allocation)
        qty = math.floor(max_spend / price)
        return max(qty, 0)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @staticmethod
    def _load_risk_params() -> dict:
        """Load risk parameters from user config."""
        path = settings.USER_CONFIG_DIR / "risk_params.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "max_risk_per_trade_pct": 2.0,
            "max_position_size_pct": 10.0,
            "max_portfolio_allocation_pct": 30.0,
            "max_orders_per_day": 10,
            "daily_loss_limit_pct": 5.0,
            "cooldown_days": 7,
            "stop_loss_atr_multiplier": 2.0,
            "account_size_usd": 10000,
        }
