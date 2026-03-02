"""Tests for TradeAction schema — validates Pydantic model + edge cases."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.models.trade_action import TRADE_ACTION_SCHEMA, TradeAction

# ── Basic schema tests ─────────────────────────────────────────────

class TestTradeActionModel:
    """Validate TradeAction Pydantic model."""

    def test_valid_buy(self):
        action = TradeAction(
            action="BUY",
            symbol="NVDA",
            confidence=0.85,
            rationale="Strong momentum",
            bot_id="test",
        )
        assert action.action == "BUY"
        assert action.symbol == "NVDA"
        assert action.confidence == 0.85
        assert action.risk_level == "MED"  # default
        assert action.time_horizon == "SWING"  # default

    def test_valid_sell(self):
        action = TradeAction(
            action="SELL",
            symbol="AAPL",
            confidence=0.60,
            rationale="Bearish divergence",
            risk_level="HIGH",
            time_horizon="INTRADAY",
            bot_id="test",
        )
        assert action.action == "SELL"
        assert action.risk_level == "HIGH"
        assert action.time_horizon == "INTRADAY"

    def test_valid_hold(self):
        action = TradeAction(
            action="HOLD",
            symbol="TSLA",
            confidence=0.50,
            rationale="Neutral signals",
            bot_id="test",
        )
        assert action.action == "HOLD"

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            TradeAction(
                action="YOLO",
                symbol="GME",
                confidence=1.0,
                rationale="Diamond hands",
                bot_id="test",
            )

    def test_confidence_above_1_rejected(self):
        """Confidence > 1.0 should be rejected by ge/le validators."""
        with pytest.raises(ValidationError):
            TradeAction(
                action="BUY",
                symbol="NVDA",
                confidence=1.5,
                rationale="Overly confident",
                bot_id="test",
            )

    def test_confidence_below_0_rejected(self):
        with pytest.raises(ValidationError):
            TradeAction(
                action="BUY",
                symbol="NVDA",
                confidence=-0.1,
                rationale="Negative confidence",
                bot_id="test",
            )

    def test_empty_symbol_allowed(self):
        """Parser fills in symbol, so model allows empty."""
        action = TradeAction(
            action="HOLD",
            symbol="",
            confidence=0.5,
            rationale="No data",
            bot_id="test",
        )
        assert action.symbol == ""

    def test_schema_dict_has_required_fields(self):
        """Verify TRADE_ACTION_SCHEMA has the structure Ollama expects."""
        assert "type" in TRADE_ACTION_SCHEMA
        assert TRADE_ACTION_SCHEMA["type"] == "object"
        props = TRADE_ACTION_SCHEMA["properties"]
        assert "action" in props
        assert "symbol" in props
        assert "confidence" in props
        assert "rationale" in props


# ── Property-based (Hypothesis) ─────────────────────────────────────

class TestTradeActionHypothesis:
    """Fuzz TradeAction with random data."""

    @given(
        confidence=st.floats(
            min_value=0.0, max_value=1.0,
            allow_nan=False, allow_infinity=False,
        ),
        symbol=st.text(min_size=1, max_size=6, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    )
    def test_valid_inputs_always_parse(self, confidence: float, symbol: str):
        """Any valid action + float confidence should produce a TradeAction."""
        action = TradeAction(
            action="BUY",
            symbol=symbol,
            confidence=confidence,
            rationale="Test",
            bot_id="fuzz",
        )
        assert 0.0 <= action.confidence <= 1.0
        assert action.symbol == symbol

    @given(
        risk=st.sampled_from(["LOW", "MED", "HIGH"]),
        horizon=st.sampled_from(["INTRADAY", "SWING", "POSITION"]),
    )
    def test_enums_always_valid(self, risk: str, horizon: str):
        action = TradeAction(
            action="HOLD",
            symbol="TEST",
            confidence=0.5,
            rationale="Test",
            risk_level=risk,
            time_horizon=horizon,
            bot_id="fuzz",
        )
        assert action.risk_level == risk
        assert action.time_horizon == horizon
