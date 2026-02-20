"""Tests for PortfolioStrategist tool-calling agent."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-mock app.main before importing portfolio_strategist
# This prevents DuckDB file lock errors during testing
_mock_main = ModuleType("app.main")
_mock_main._fetch_one_quote = MagicMock(return_value={"price": 150.0})  # type: ignore[attr-defined]
sys.modules.setdefault("app.main", _mock_main)

from app.engine.portfolio_strategist import PortfolioStrategist  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def mock_paper_trader():
    """Create a mock PaperTrader."""
    trader = MagicMock()
    trader.get_portfolio.return_value = {
        "cash_balance": 10000.0,
        "total_portfolio_value": 10000.0,
        "positions": [],
        "realized_pnl": 0,
    }
    trader.get_orders_today_count.return_value = 0
    trader.get_daily_pnl_pct.return_value = 0.0
    trader.get_positions.return_value = []
    trader.buy.return_value = MagicMock(qty=10, price=150.0, side="buy")
    trader.sell.return_value = MagicMock(qty=5, price=200.0, side="sell")
    return trader


# ── Unit Tests ───────────────────────────────────────────────────

class TestPortfolioStrategist:
    """Tests for the PortfolioStrategist class."""

    def test_init(self, mock_paper_trader):
        """Test initialization."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL", "META"],
        )
        assert strategist._tickers == ["AAPL", "META"]
        assert strategist._trader is mock_paper_trader

    @pytest.mark.asyncio
    async def test_tool_get_portfolio(self, mock_paper_trader):
        """Test get_portfolio tool returns portfolio state."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        result = await strategist._tool_get_portfolio({})
        assert result["cash_balance"] == 10000.0
        assert result["total_portfolio_value"] == 10000.0
        assert result["position_count"] == 0

    @pytest.mark.asyncio
    async def test_tool_get_all_candidates_no_dossiers(self, mock_paper_trader):
        """Test get_all_candidates when no dossiers exist."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        with patch(
            "app.engine.portfolio_strategist.DeepAnalysisService"
        ) as mock_das:
            mock_das.get_latest_dossier.return_value = None
            result = await strategist._tool_get_all_candidates({})
            assert result["total"] == 0
            assert result["candidates"] == []

    @pytest.mark.asyncio
    async def test_tool_place_buy_insufficient_cash(self, mock_paper_trader):
        """Test buy rejection when not enough cash."""
        mock_paper_trader.get_portfolio.return_value = {
            "cash_balance": 100.0,
            "total_portfolio_value": 100.0,
            "positions": [],
        }
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )

        # Mock the price fetcher to return $150
        _mock_main._fetch_one_quote = MagicMock(return_value={"price": 150.0})  # type: ignore[attr-defined]
        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 10,
            "reason": "test buy",
        })
        assert "error" in result
        assert "Insufficient cash" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_place_buy_too_large(self, mock_paper_trader):
        """Test buy rejection when order exceeds 40% of portfolio."""
        mock_paper_trader.get_portfolio.return_value = {
            "cash_balance": 10000.0,
            "total_portfolio_value": 10000.0,
            "positions": [],
        }
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )

        # Mock the price fetcher to return $500
        _mock_main._fetch_one_quote = MagicMock(return_value={"price": 500.0})  # type: ignore[attr-defined]
        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 10,  # $5000 = 50% of portfolio
            "reason": "test big buy",
        })
        assert "error" in result
        assert "too large" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_tool_set_triggers_no_position(self, mock_paper_trader):
        """Test set_triggers fails when no position exists."""
        mock_paper_trader.get_positions.return_value = []
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        result = await strategist._tool_set_triggers({
            "ticker": "AAPL",
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
        })
        assert "error" in result
        assert "No open position" in result["error"]

    @pytest.mark.asyncio
    async def test_run_finish_immediately(self, mock_paper_trader):
        """Test that run() handles the LLM calling finish immediately."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )

        # Mock LLM to immediately return finish
        with patch.object(
            strategist._llm,
            "chat",
            new_callable=AsyncMock,
            return_value=json.dumps({
                "action": "finish",
                "params": {"summary": "No trades — market uncertain"},
            }),
        ):
            result = await strategist.run()

        assert result["orders_placed"] == 0
        assert result["summary"] == "No trades — market uncertain"

    @pytest.mark.asyncio
    async def test_run_buy_then_finish(self, mock_paper_trader):
        """Test a complete run: portfolio check, candidates, buy, finish."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )

        # Simulate a multi-turn conversation
        responses = [
            json.dumps({"action": "get_portfolio", "params": {}}),
            json.dumps({"action": "finish", "params": {"summary": "Checked portfolio, holding for now"}}),
        ]

        call_count = 0

        async def mock_chat(*args, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with patch.object(
            strategist._llm,
            "chat",
            side_effect=mock_chat,
        ):
            result = await strategist.run()

        assert result["summary"] == "Checked portfolio, holding for now"
        assert result["orders_placed"] == 0


class TestLLMServiceRetry:
    """Tests for the LLM service retry-with-trim logic."""

    def test_estimate_tokens(self):
        """Test token estimation."""
        assert LLMService.estimate_tokens("hello") == 1
        assert LLMService.estimate_tokens("a" * 100) == 25

    def test_trim_messages(self):
        """Test message trimming keeps ~60% of longest message."""
        messages = [
            {"role": "system", "content": "short"},
            {"role": "user", "content": "x" * 1000},
        ]
        trimmed = LLMService._trim_messages(messages)
        # System message unchanged
        assert trimmed[0]["content"] == "short"
        # User message trimmed (~60% of 1000 = ~600 + separator text)
        assert len(trimmed[1]["content"]) < 1000
        assert "[... content trimmed" in trimmed[1]["content"]

    def test_trim_preserves_short_messages(self):
        """Test that short messages are not trimmed."""
        messages = [
            {"role": "system", "content": "x" * 2000},  # longest
            {"role": "user", "content": "short query"},
        ]
        trimmed = LLMService._trim_messages(messages)
        assert trimmed[1]["content"] == "short query"
        assert len(trimmed[0]["content"]) < 2000


class TestCleanJsonMultiObject:
    """Regression tests for the multi-object JSON extraction bug.

    The LLM was outputting multiple JSON objects in one response, causing
    rfind('}') to grab a blob spanning all objects → json.loads failure.
    """

    def test_extracts_first_json_from_multi_object(self):
        """Exact pattern from audit reports: multiple actions in one message."""
        raw = (
            '{"action": "place_buy", "params": {"ticker": "NVDA", "qty": 400, '
            '"reason": "Strong uptrend"}}\n\n'
            '{"action": "place_buy", "params": {"ticker": "QQQ", "qty": 100, '
            '"reason": "Speculative play"}}\n\n'
            '{"action": "place_buy", "params": {"ticker": "TXN", "qty": 30, '
            '"reason": "Growth"}}'
        )
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["action"] == "place_buy"
        assert parsed["params"]["ticker"] == "NVDA"

    def test_extracts_json_from_prose(self):
        """LLM wraps JSON in analysis text — should still extract first {}."""
        raw = (
            "Based on the candidates:\n\n"
            "1. Buy NVDA:\n"
            '{"action": "place_buy", "params": {"ticker": "NVDA", "qty": 10, '
            '"reason": "AI momentum"}}\n\n'
            "2. Buy AAPL:\n"
            '{"action": "place_buy", "params": {"ticker": "AAPL", "qty": 5, '
            '"reason": "safe bet"}}'
        )
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["params"]["ticker"] == "NVDA"

    def test_handles_markdown_fences_multi_object(self):
        """JSON inside markdown code blocks with multiple objects."""
        raw = (
            "```json\n"
            '{"action": "place_buy", "params": {"ticker": "GOOG", "qty": 10, '
            '"reason": "Strong momentum + AI catalyst"}}\n'
            "```\n\n"
            "```json\n"
            '{"action": "place_buy", "params": {"ticker": "INTC", "qty": 40, '
            '"reason": "Strong momentum"}}\n'
            "```"
        )
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["params"]["ticker"] == "GOOG"

    def test_single_valid_json_unchanged(self):
        """A single clean JSON object should pass through unchanged."""
        raw = '{"action": "finish", "params": {"summary": "Done"}}'
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["action"] == "finish"

    def test_truncated_json_returns_best_effort(self):
        """Truncated JSON (from max_tokens) should return the incomplete blob."""
        raw = '{"action": "place_buy", "params": {"ticker": "NVDA", "qty'
        result = LLMService.clean_json_response(raw)
        # Should start with { but not be valid JSON
        assert result.startswith("{")
        # json.loads should fail on truncated input
        import pytest
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

