"""Tests for RiskRules — deterministic position sizing + stop/TP math.

All tests are pure math — no DB, no LLM, no network.
Uses Hypothesis for edge case fuzzing (NaN/infinity filtered out).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.services.risk_rules import RiskRules

# ── Finite-float strategy (no NaN, no inf) ─────────────────────────
_finite_price = st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False)
_finite_cash = st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_finite_pv = st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_finite_atr = st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False, allow_infinity=False)


class TestComputeQty:
    """Test position sizing logic."""

    def test_basic_buy(self):
        qty = RiskRules.compute_qty(
            price=100.0,
            cash=10_000.0,
            portfolio_value=100_000.0,
            risk_level="MED",
        )
        # MED scale=1.0, budget = 100k * 10% * 1.0 = 10k
        # capped by cash = 10k, qty = floor(10k/100) = 100
        assert qty == 100

    def test_low_risk_smaller_position(self):
        qty_low = RiskRules.compute_qty(
            price=50.0, cash=50_000.0, portfolio_value=100_000.0,
            risk_level="LOW",
        )
        qty_med = RiskRules.compute_qty(
            price=50.0, cash=50_000.0, portfolio_value=100_000.0,
            risk_level="MED",
        )
        assert qty_low < qty_med

    def test_high_risk_larger_position(self):
        qty_med = RiskRules.compute_qty(
            price=50.0, cash=50_000.0, portfolio_value=100_000.0,
            risk_level="MED",
        )
        qty_high = RiskRules.compute_qty(
            price=50.0, cash=50_000.0, portfolio_value=100_000.0,
            risk_level="HIGH",
        )
        assert qty_high > qty_med

    def test_zero_price_returns_zero(self):
        assert RiskRules.compute_qty(0, 10_000, 100_000) == 0

    def test_zero_cash_returns_zero(self):
        assert RiskRules.compute_qty(100, 0, 100_000) == 0

    def test_zero_portfolio_returns_zero(self):
        assert RiskRules.compute_qty(100, 10_000, 0) == 0

    def test_concentration_limit(self):
        """Existing position near max → qty should be 0."""
        qty = RiskRules.compute_qty(
            price=100.0,
            cash=50_000.0,
            portfolio_value=100_000.0,
            existing_position_value=15_000.0,  # Already at 15% max
        )
        assert qty == 0

    def test_cash_limited(self):
        """Cash < budget → limited by cash."""
        qty = RiskRules.compute_qty(
            price=100.0,
            cash=500.0,     # Only $500
            portfolio_value=100_000.0,
        )
        assert qty == 5  # floor(500/100)

    @given(price=_finite_price, cash=_finite_cash, pv=_finite_pv)
    def test_qty_always_non_negative(self, price: float, cash: float, pv: float):
        """Qty must never be negative regardless of inputs."""
        qty = RiskRules.compute_qty(price, cash, pv)
        assert qty >= 0


class TestComputeStopLoss:
    """Test stop-loss calculations."""

    def test_basic_stop(self):
        stop = RiskRules.compute_stop_loss(100.0, atr=5.0, risk_level="MED")
        assert stop == 90.0

    def test_low_risk_tighter_stop(self):
        stop = RiskRules.compute_stop_loss(100.0, atr=5.0, risk_level="LOW")
        assert stop == 95.0

    def test_zero_atr_fallback(self):
        stop = RiskRules.compute_stop_loss(100.0, atr=0.0)
        assert stop == 95.0  # 5% fallback

    def test_stop_never_negative(self):
        stop = RiskRules.compute_stop_loss(1.0, atr=100.0, risk_level="HIGH")
        assert stop >= 0.01

    @given(price=_finite_price, atr=_finite_atr)
    def test_stop_always_below_or_equal_price(self, price: float, atr: float):
        stop = RiskRules.compute_stop_loss(price, atr)
        assert stop <= price + 0.01  # rounding tolerance


class TestComputeTakeProfit:
    """Test take-profit calculations."""

    def test_basic_tp(self):
        tp = RiskRules.compute_take_profit(100.0, atr=5.0, risk_level="MED")
        assert tp == 115.0

    def test_zero_atr_fallback(self):
        tp = RiskRules.compute_take_profit(100.0, atr=0.0)
        assert tp == 110.0  # 10% fallback

    @given(price=_finite_price, atr=_finite_atr)
    def test_tp_always_above_or_equal_price(self, price: float, atr: float):
        tp = RiskRules.compute_take_profit(price, atr)
        assert tp >= price - 0.01  # rounding tolerance


class TestValidateTrade:
    """Test final trade validation gate."""

    def test_valid_trade(self):
        ok, reason = RiskRules.validate_trade(100.0, 10, 5_000.0, 100_000.0)
        assert ok is True
        assert reason == "ok"

    def test_zero_qty_rejected(self):
        ok, reason = RiskRules.validate_trade(100.0, 0, 5_000.0, 100_000.0)
        assert ok is False
        assert "qty" in reason

    def test_exceeds_cash_rejected(self):
        ok, reason = RiskRules.validate_trade(100.0, 100, 500.0, 100_000.0)
        assert ok is False
        assert "cash" in reason

    def test_exceeds_portfolio_pct_rejected(self):
        ok, reason = RiskRules.validate_trade(100.0, 300, 50_000.0, 100_000.0)
        assert ok is False
        assert "portfolio" in reason
