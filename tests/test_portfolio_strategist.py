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
    async def test_market_overview_compact(self, mock_paper_trader):
        """Test get_market_overview returns compact metadata, no prose."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        with patch(
            "app.engine.portfolio_strategist.DeepAnalysisService"
        ) as mock_das:
            mock_das.get_latest_dossier.return_value = {
                "scorecard": {
                    "trend_template_score": 85,
                    "vcp_setup_score": 70,
                    "relative_strength_rating": 90,
                    "signal_summary": "Strong uptrend",
                },
                "conviction_score": 0.8,
                "sector": "Technology",
                "executive_summary": "A long detailed summary...",
                "bull_case": "Very bullish...",
                "bear_case": "Some risks...",
                "key_catalysts": ["AI", "iPhone"],
            }
            result = await strategist._tool_get_market_overview({})
            assert result["total_new"] == 1
            c = result["candidates"][0]
            # Compact metadata present
            assert c["ticker"] == "AAPL"
            assert c["conviction"] == 0.8
            assert c["trend_score"] == 85
            # Full prose fields should NOT be present
            assert "executive_summary" not in c
            assert "bull_case" not in c
            assert "bear_case" not in c
            assert "key_catalysts" not in c

    @pytest.mark.asyncio
    async def test_market_overview_no_dossiers(self, mock_paper_trader):
        """Test get_market_overview when no dossiers exist."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        with patch(
            "app.engine.portfolio_strategist.DeepAnalysisService"
        ) as mock_das:
            mock_das.get_latest_dossier.return_value = None
            result = await strategist._tool_get_market_overview({})
            assert result["total_new"] == 0
            assert result["candidates"] == []

    @pytest.mark.asyncio
    async def test_get_dossier_returns_full_prose(self, mock_paper_trader):
        """Test get_dossier returns full analysis for one ticker."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        with patch(
            "app.engine.portfolio_strategist.DeepAnalysisService"
        ) as mock_das:
            mock_das.get_latest_dossier.return_value = {
                "scorecard": {"signal_summary": "Bullish"},
                "conviction_score": 0.9,
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "market_cap_tier": "mega",
                "executive_summary": "Apple is the world leader...",
                "bull_case": "AI integration + services growth",
                "bear_case": "Regulatory pressure in EU",
                "key_catalysts": ["WWDC", "iPhone 17"],
            }
            result = await strategist._tool_get_dossier({"ticker": "AAPL"})
            assert result["ticker"] == "AAPL"
            assert result["executive_summary"] == "Apple is the world leader..."
            assert result["bull_case"] == "AI integration + services growth"
            assert result["key_catalysts"] == ["WWDC", "iPhone 17"]

    @pytest.mark.asyncio
    async def test_get_dossier_missing_ticker(self, mock_paper_trader):
        """Test get_dossier returns error for unknown ticker."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        with patch(
            "app.engine.portfolio_strategist.DeepAnalysisService"
        ) as mock_das:
            mock_das.get_latest_dossier.return_value = None
            result = await strategist._tool_get_dossier({"ticker": "ZZZZ"})
            assert "error" in result

    def test_portfolio_state_bounded(self, mock_paper_trader):
        """Test that portfolio state trades list stays bounded at 10."""
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )
        # Simulate 15 trades filling the state
        for i in range(15):
            strategist._portfolio_state["trades_this_session"].append({
                "side": "BUY", "ticker": f"T{i}", "qty": 10, "price": 100.0,
            })
            trades = strategist._portfolio_state["trades_this_session"]
            if len(trades) > 10:
                strategist._portfolio_state["trades_this_session"] = trades[-10:]

        assert len(strategist._portfolio_state["trades_this_session"]) == 10
        # Most recent trade should be T14 (0-indexed)
        assert strategist._portfolio_state["trades_this_session"][-1]["ticker"] == "T14"

    @pytest.mark.asyncio
    async def test_tool_place_buy_insufficient_cash(self, mock_paper_trader):
        """Test buy with very low cash auto-clamps to max affordable."""
        mock_paper_trader.get_portfolio.return_value = {
            "cash_balance": 100.0,
            "total_portfolio_value": 100.0,
            "positions": [],
        }
        mock_paper_trader.get_positions.return_value = []
        # Auto-clamp will reduce to 0 shares ($100 cash / $150 price = 0)
        # so this should return an error
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
        assert "max safe qty is 0" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_place_buy_too_large_auto_clamped(self, mock_paper_trader):
        """Test that oversized order is auto-clamped, not rejected."""
        mock_paper_trader.get_portfolio.return_value = {
            "cash_balance": 10000.0,
            "total_portfolio_value": 10000.0,
            "positions": [],
        }
        mock_paper_trader.get_positions.return_value = []
        # Auto-clamp will reduce from 10 to 8 shares (40% of $10k = $4k / $500 = 8)
        mock_paper_trader.buy.return_value = MagicMock(qty=8, price=500.0, side="buy")
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
        # Should be auto-clamped and filled, not rejected
        assert result.get("status") == "filled"
        assert result.get("clamped") is True

    @pytest.mark.asyncio
    async def test_tool_place_buy_rejects_existing_position(self, mock_paper_trader):
        """Test buy rejection when ticker is already held."""
        mock_paper_trader.get_portfolio.return_value = {
            "cash_balance": 10000.0,
            "total_portfolio_value": 10000.0,
            "positions": [{"ticker": "AAPL", "qty": 10, "avg_entry_price": 150.0}],
        }
        mock_paper_trader.get_positions.return_value = [
            {"ticker": "AAPL", "qty": 10, "avg_entry_price": 150.0},
        ]
        strategist = PortfolioStrategist(
            paper_trader=mock_paper_trader,
            tickers=["AAPL"],
        )

        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 5,
            "reason": "test duplicate buy",
        })
        assert "error" in result
        assert "POSITION EXISTS" in result["error"]
        # Should be added to failed set so retries are blocked too
        assert "AAPL" in strategist._failed_buy_tickers

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


class TestThinkBlockStripping:
    """Tests for <think> block stripping in clean_json_response.

    Reasoning models (e.g. QwQ, Qwen3) output <think>...</think> blocks
    before their JSON. This broke the pipeline when the parser tried
    to extract JSON from the thinking text.
    """

    def test_strips_think_block_before_json(self):
        """Think block followed by clean JSON should extract the JSON."""
        raw = (
            "<think>Let me analyze the portfolio... KO has strong momentum "
            "and I should buy it. The conviction is 0.75 so I'll allocate "
            "15% of the portfolio.</think>\n"
            '{"action": "place_buy", "params": {"ticker": "KO", "qty": 56, '
            '"reason": "Strong momentum"}}'
        )
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["action"] == "place_buy"
        assert parsed["params"]["ticker"] == "KO"

    def test_strips_think_block_with_markdown_fences(self):
        """Think block + markdown-fenced JSON should work."""
        raw = (
            "<think>Analyzing market data...</think>\n"
            "```json\n"
            '{"action": "get_dossier", "params": {"ticker": "NVDA"}}\n'
            "```"
        )
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["action"] == "get_dossier"
        assert parsed["params"]["ticker"] == "NVDA"

    def test_no_think_block_unchanged(self):
        """JSON without think blocks should pass through normally."""
        raw = '{"action": "finish", "params": {"summary": "All done"}}'
        result = LLMService.clean_json_response(raw)
        parsed = json.loads(result)
        assert parsed["action"] == "finish"


class TestAutoClampOversizedBuy:
    """Tests for the auto-clamp feature in _tool_place_buy.

    Instead of rejecting oversized orders with an error (which wastes
    LLM turns), auto-clamp calculates the max safe qty and places the
    order at that reduced size.
    """

    @pytest.mark.asyncio
    async def test_auto_clamp_oversized_order(self):
        """An order exceeding 40% should be auto-clamped, not rejected."""
        trader = MagicMock()
        trader.get_portfolio.return_value = {
            "cash_balance": 10000.0,
            "total_portfolio_value": 10000.0,
            "positions": [],
        }
        trader.get_positions.return_value = []
        trader.buy.return_value = MagicMock(qty=26, price=150.0, side="buy")

        strategist = PortfolioStrategist(
            paper_trader=trader,
            tickers=["AAPL"],
        )

        # Mock the price fetcher to return $150
        _mock_main._fetch_one_quote = MagicMock(return_value={"price": 150.0})

        # Request 100 shares ($15k = 150% of $10k portfolio) → should clamp
        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 100,  # Way too many — $15k > 40% of $10k
            "reason": "test auto-clamp",
        })

        # Should be filled, NOT rejected
        assert result.get("status") == "filled"
        assert result.get("clamped") is True
        assert result.get("original_qty") == 100
        assert "auto-clamped" in result.get("note", "").lower()

    @pytest.mark.asyncio
    async def test_zero_safe_qty_returns_error(self):
        """When max safe qty is 0 (no cash), should return error."""
        trader = MagicMock()
        trader.get_portfolio.return_value = {
            "cash_balance": 0.0,
            "total_portfolio_value": 10000.0,
            "positions": [],
        }
        trader.get_positions.return_value = []

        strategist = PortfolioStrategist(
            paper_trader=trader,
            tickers=["AAPL"],
        )

        _mock_main._fetch_one_quote = MagicMock(return_value={"price": 150.0})

        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 10,
            "reason": "test zero cash",
        })

        assert "error" in result
        assert "max safe qty is 0" in result["error"]

    @pytest.mark.asyncio
    async def test_correctly_sized_order_not_clamped(self):
        """A properly sized order should NOT be clamped."""
        trader = MagicMock()
        trader.get_portfolio.return_value = {
            "cash_balance": 10000.0,
            "total_portfolio_value": 10000.0,
            "positions": [],
        }
        trader.get_positions.return_value = []
        trader.buy.return_value = MagicMock(qty=10, price=150.0, side="buy")

        strategist = PortfolioStrategist(
            paper_trader=trader,
            tickers=["AAPL"],
        )

        _mock_main._fetch_one_quote = MagicMock(return_value={"price": 150.0})

        # 10 shares @ $150 = $1500 = 15% of portfolio — well within limits
        result = await strategist._tool_place_buy({
            "ticker": "AAPL",
            "qty": 10,
            "reason": "test normal buy",
        })

        assert result.get("status") == "filled"
        assert "clamped" not in result


class TestActionSchema:
    """Test that ACTION_SCHEMA is properly defined."""

    def test_schema_has_required_fields(self):
        """Schema should enforce action and params."""
        from app.engine.portfolio_strategist import ACTION_SCHEMA
        assert ACTION_SCHEMA["required"] == ["action", "params"]
        assert "action" in ACTION_SCHEMA["properties"]
        assert "params" in ACTION_SCHEMA["properties"]

    def test_schema_action_enum(self):
        """Schema action should include all tool names."""
        from app.engine.portfolio_strategist import ACTION_SCHEMA
        enum = ACTION_SCHEMA["properties"]["action"]["enum"]
        expected = [
            "get_portfolio", "get_market_overview", "get_dossier",
            "get_sector_peers", "place_buy", "place_sell", "set_triggers",
            "get_market_status", "remove_from_watchlist", "schedule_wakeup",
            "finish",
        ]
        assert set(enum) == set(expected)
