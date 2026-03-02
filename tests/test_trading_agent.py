"""Tests for TradingAgent — prompt building and LLM response handling.

Uses respx to mock the Ollama HTTP endpoint so no real LLM is needed.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.trading_agent import TradingAgent


@pytest.fixture
def agent() -> TradingAgent:
    return TradingAgent()


@pytest.fixture
def sample_context() -> dict:
    return {
        "symbol": "NVDA",
        "last_price": 950.50,
        "today_change_pct": 2.3,
        "volume": 45_000_000,
        "avg_volume": 38_000_000,
        "technical_summary": "RSI=62 | MACD=3.5 | SMA20=$920 | SMA50=$880",
        "quant_summary": "Conviction: 78% | Kelly: 12% | Sharpe: 1.8",
        "news_summary": "NVIDIA announced strong Q4 earnings with revenue up 30%.",
        "portfolio_cash": 50_000,
        "portfolio_value": 150_000,
        "max_position_pct": 15,
        "existing_position": {},
    }


class TestPromptBuilding:
    """Verify context → prompt conversion."""

    def test_prompt_contains_ticker(self, agent: TradingAgent, sample_context: dict):
        prompt = agent._build_prompt(sample_context)
        assert "NVDA" in prompt

    def test_prompt_contains_price(self, agent: TradingAgent, sample_context: dict):
        prompt = agent._build_prompt(sample_context)
        assert "$950.50" in prompt

    def test_prompt_contains_technicals(self, agent: TradingAgent, sample_context: dict):
        prompt = agent._build_prompt(sample_context)
        assert "RSI=62" in prompt

    def test_prompt_contains_portfolio(self, agent: TradingAgent, sample_context: dict):
        prompt = agent._build_prompt(sample_context)
        assert "Cash=$50,000" in prompt

    def test_prompt_no_position(self, agent: TradingAgent, sample_context: dict):
        prompt = agent._build_prompt(sample_context)
        assert "EXISTING POSITION: None" in prompt

    def test_prompt_with_position(self, agent: TradingAgent, sample_context: dict):
        sample_context["existing_position"] = {
            "qty": 10,
            "avg_entry": 900.0,
            "unrealized_pnl": 505.0,
        }
        prompt = agent._build_prompt(sample_context)
        assert "10 shares" in prompt
        assert "$900.00" in prompt

    def test_prompt_handles_missing_data(self, agent: TradingAgent):
        minimal_context = {"symbol": "TEST", "last_price": 100}
        prompt = agent._build_prompt(minimal_context)
        assert "TEST" in prompt
        assert "$100.00" in prompt


class TestLLMIntegration:
    """Mock Ollama to verify end-to-end decide() flow."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_decide_buy(self, agent: TradingAgent, sample_context: dict):
        """Mock Ollama returning a BUY decision."""
        from app.config import settings

        llm_response = json.dumps({
            "action": "BUY",
            "symbol": "NVDA",
            "confidence": 0.85,
            "rationale": "Strong earnings beat with 30% revenue growth",
            "risk_notes": "High valuation",
            "risk_level": "MED",
            "time_horizon": "SWING",
        })

        # Mock the Ollama /api/chat endpoint
        respx.post(f"{settings.LLM_BASE_URL}/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={"message": {"content": llm_response}},
            ),
        )

        action, _raw = await agent.decide(sample_context, bot_id="test")
        assert action.action == "BUY"
        assert action.symbol == "NVDA"
        assert action.confidence == 0.85
        assert "earnings" in action.rationale.lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_decide_hold_on_uncertainty(
        self, agent: TradingAgent, sample_context: dict,
    ):
        """Mock Ollama returning a HOLD decision."""
        from app.config import settings

        llm_response = json.dumps({
            "action": "HOLD",
            "symbol": "NVDA",
            "confidence": 0.40,
            "rationale": "Mixed signals, waiting for clarity",
            "risk_level": "LOW",
        })

        respx.post(f"{settings.LLM_BASE_URL}/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={"message": {"content": llm_response}},
            ),
        )

        action, _raw = await agent.decide(sample_context, bot_id="test")
        assert action.action == "HOLD"
        assert action.confidence < 0.5
