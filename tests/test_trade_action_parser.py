"""Tests for trade_action_parser — parse, validate, and repair LLM output.

Repair attempts call LLM → we patch LLMService.chat so no real LLM is needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.trade_action_parser import parse_trade_action


@pytest.fixture
def valid_json() -> str:
    return json.dumps({
        "action": "BUY",
        "symbol": "NVDA",
        "confidence": 0.85,
        "rationale": "Strong momentum and positive earnings",
        "risk_notes": "High valuation",
        "risk_level": "MED",
        "time_horizon": "SWING",
    })


@pytest.fixture
def markdown_wrapped_json() -> str:
    return """```json
{
    "action": "SELL",
    "symbol": "AAPL",
    "confidence": 0.60,
    "rationale": "Bearish divergence on RSI",
    "risk_notes": "Support at 170",
    "risk_level": "HIGH",
    "time_horizon": "INTRADAY"
}
```"""


@pytest.fixture
def think_block_json() -> str:
    return """<think>
The RSI is oversold but the MACD is crossing below signal.
I should sell because the trend is bearish.
</think>
{"action": "SELL", "symbol": "TSLA", "confidence": 0.70,
 "rationale": "Bearish MACD crossover", "risk_level": "HIGH"}"""


class TestParserHappyPath:
    """Parser should handle clean JSON."""

    @pytest.mark.asyncio
    async def test_clean_json(self, valid_json: str):
        action = await parse_trade_action(valid_json, "bot1", "NVDA")
        assert action.action == "BUY"
        assert action.symbol == "NVDA"
        assert action.confidence == 0.85

    @pytest.mark.asyncio
    async def test_markdown_wrapped(self, markdown_wrapped_json: str):
        action = await parse_trade_action(markdown_wrapped_json, "bot1", "AAPL")
        assert action.action == "SELL"
        assert action.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_think_block_stripped(self, think_block_json: str):
        action = await parse_trade_action(think_block_json, "bot1", "TSLA")
        assert action.action == "SELL"
        assert action.symbol == "TSLA"


class TestParserRepair:
    """Parser should auto-repair missing/wrong fields."""

    @pytest.mark.asyncio
    async def test_missing_symbol_filled(self):
        raw = json.dumps({
            "action": "BUY",
            "confidence": 0.80,
            "rationale": "Bullish",
        })
        action = await parse_trade_action(raw, "bot1", "GOOG")
        assert action.symbol == "GOOG"

    @pytest.mark.asyncio
    async def test_lowercase_action_repaired(self):
        raw = json.dumps({
            "action": "buy",
            "symbol": "MSFT",
            "confidence": 0.75,
            "rationale": "Breakout",
        })
        action = await parse_trade_action(raw, "bot1", "MSFT")
        assert action.action == "BUY"

    @pytest.mark.asyncio
    async def test_missing_rationale_triggers_repair_or_fallback(self):
        """Missing rationale → Pydantic rejects → repair attempt → forced HOLD."""
        raw = json.dumps({
            "action": "HOLD",
            "symbol": "META",
            "confidence": 0.50,
            # rationale missing — Pydantic requires it
        })
        # Patch LLM repair call to return valid JSON
        repaired = json.dumps({
            "action": "HOLD",
            "symbol": "META",
            "confidence": 0.50,
            "rationale": "No clear signals",
        })
        with patch(
            "app.services.trade_action_parser._llm.chat",
            new_callable=AsyncMock,
            return_value=repaired,
        ):
            action = await parse_trade_action(raw, "bot1", "META")
        # Either repaired or forced HOLD
        assert action.action == "HOLD"
        assert action.rationale  # Should have some text


class TestParserGarbage:
    """Totally broken input → safe HOLD fallback."""

    @pytest.mark.asyncio
    async def test_empty_string(self):
        # Patch LLM repair to also fail → forces HOLD
        with patch(
            "app.services.trade_action_parser._llm.chat",
            new_callable=AsyncMock,
            return_value="garbage",
        ):
            action = await parse_trade_action("", "bot1", "NVDA")
        assert action.action == "HOLD"
        assert action.confidence <= 0.1

    @pytest.mark.asyncio
    async def test_not_json(self):
        with patch(
            "app.services.trade_action_parser._llm.chat",
            new_callable=AsyncMock,
            return_value="still not json",
        ):
            action = await parse_trade_action(
                "I think we should buy NVDA because it's going up!",
                "bot1", "NVDA",
            )
        assert action.action == "HOLD"

    @pytest.mark.asyncio
    async def test_malformed_json_triggers_repair(self):
        """Partial JSON → repair attempt → should succeed or fallback."""
        repaired = json.dumps({
            "action": "BUY",
            "symbol": "NVDA",
            "confidence": 0.80,
            "rationale": "Repaired by LLM",
        })
        with patch(
            "app.services.trade_action_parser._llm.chat",
            new_callable=AsyncMock,
            return_value=repaired,
        ):
            action = await parse_trade_action(
                '{"action": "BUY", "symbol": "NVDA"',  # missing closing brace
                "bot1", "NVDA",
            )
        assert action.action in ("BUY", "HOLD")
