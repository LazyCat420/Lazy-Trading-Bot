"""Risk Rules — deterministic position sizing and risk management.

All calculations are pure math — no LLM calls.
Uses ATR/volatility-based sizing for consistent, testable execution.
"""

from __future__ import annotations

import math

from app.utils.logger import logger


# ── Default configuration ────────────────────────────────────────
_MAX_POSITION_PCT = 0.15       # Max 15% of portfolio in one stock
_MAX_SINGLE_ORDER_PCT = 0.10   # Max 10% of portfolio per order
_STOP_ATR_MULT = 2.0           # Stop loss = 2x ATR below entry
_TP_ATR_MULT = 3.0             # Take profit = 3x ATR above entry (1.5:1 R:R)
_MIN_QTY = 1                   # Never buy less than 1 share
_RISK_SCALE = {"LOW": 0.5, "MED": 1.0, "HIGH": 1.5}


class RiskRules:
    """Deterministic position sizing and risk management."""

    @staticmethod
    def compute_qty(
        price: float,
        cash: float,
        portfolio_value: float,
        risk_level: str = "MED",
        existing_position_value: float = 0.0,
    ) -> int:
        """Compute number of shares to buy.

        Uses percentage-of-portfolio sizing:
          1. Scale order budget by risk_level
          2. Cap by max single order %
          3. Cap by max position concentration %
          4. Floor at 1 share minimum

        Returns 0 if the trade would violate constraints.
        """
        if price <= 0 or cash <= 0 or portfolio_value <= 0:
            return 0

        scale = _RISK_SCALE.get(risk_level.upper(), 1.0)

        # Budget = percentage of portfolio, scaled by risk
        budget = portfolio_value * _MAX_SINGLE_ORDER_PCT * scale
        budget = min(budget, cash)  # Can't spend more than available cash

        # Position concentration limit
        max_position_budget = (portfolio_value * _MAX_POSITION_PCT) - existing_position_value
        if max_position_budget <= 0:
            logger.info(
                "[RiskRules] Position concentration limit hit (existing=%.0f, max=%.0f)",
                existing_position_value,
                portfolio_value * _MAX_POSITION_PCT,
            )
            return 0

        budget = min(budget, max_position_budget)

        qty = math.floor(budget / price)
        return max(qty, 0)

    @staticmethod
    def compute_stop_loss(
        price: float,
        atr: float,
        risk_level: str = "MED",
    ) -> float:
        """Compute stop-loss price.

        Default: 2x ATR below entry. Scaled by risk level:
          - LOW: 1x ATR (tight stop)
          - MED: 2x ATR (standard)
          - HIGH: 3x ATR (wide stop)
        """
        if atr <= 0:
            # Fallback: 5% below entry
            return round(price * 0.95, 2)

        scale = _RISK_SCALE.get(risk_level.upper(), 1.0)
        stop = price - (atr * _STOP_ATR_MULT * scale)
        return round(max(stop, 0.01), 2)

    @staticmethod
    def compute_take_profit(
        price: float,
        atr: float,
        risk_level: str = "MED",
    ) -> float:
        """Compute take-profit price.

        Default: 3x ATR above entry for 1.5:1 risk/reward ratio.
        Scaled by risk level.
        """
        if atr <= 0:
            # Fallback: 10% above entry
            return round(price * 1.10, 2)

        scale = _RISK_SCALE.get(risk_level.upper(), 1.0)
        tp = price + (atr * _TP_ATR_MULT * scale)
        return round(tp, 2)

    @staticmethod
    def validate_trade(
        price: float,
        qty: int,
        cash: float,
        portfolio_value: float,
    ) -> tuple[bool, str]:
        """Final validation before execution.

        Returns (is_valid, reason).
        """
        if qty <= 0:
            return False, "qty must be positive"

        notional = price * qty
        if notional > cash:
            return False, f"notional ${notional:.0f} exceeds cash ${cash:.0f}"

        if portfolio_value > 0 and notional / portfolio_value > _MAX_SINGLE_ORDER_PCT * 2:
            return False, (
                f"order {notional / portfolio_value:.0%} of portfolio "
                f"exceeds safety limit {_MAX_SINGLE_ORDER_PCT * 2:.0%}"
            )

        return True, "ok"
