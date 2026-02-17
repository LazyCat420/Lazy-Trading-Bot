"""Tests for BaseAgent._try_unwrap_nested — extracts valid flat dicts
from nested/wrapped LLM responses.
"""

from __future__ import annotations

import pytest

from app.agents.base_agent import BaseAgent
from app.models.agent_reports import FundamentalReport, SentimentReport


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def fundamental_agent() -> BaseAgent:
    """Create a BaseAgent configured for FundamentalReport."""
    return BaseAgent(
        prompt_file="fundamental_analysis.md",
        output_model=FundamentalReport,
    )


@pytest.fixture
def sentiment_agent() -> BaseAgent:
    """Create a BaseAgent configured for SentimentReport."""
    return BaseAgent(
        prompt_file="sentiment_analysis.md",
        output_model=SentimentReport,
    )


# ── Valid flat data fixtures ──────────────────────────────────────────

VALID_FUNDAMENTAL = {
    "ticker": "MSFT",
    "valuation_grade": "FAIR",
    "financial_health": "MODERATE",
    "growth_trajectory": "STEADY",
    "key_metrics": {"pe": "30.5"},
    "strengths": ["Strong cash flow"],
    "risks": ["High valuation"],
    "intrinsic_value_estimate": None,
    "signal": "HOLD",
    "confidence": 0.7,
    "reasoning": "Fairly valued with steady growth.",
}

VALID_SENTIMENT = {
    "ticker": "MSFT",
    "overall_sentiment": "BULLISH",
    "sentiment_score": 0.6,
    "catalysts": ["Earnings beat"],
    "risks_mentioned": ["Macro headwinds"],
    "narrative_shift": None,
    "top_headlines": [],
    "signal": "BUY",
    "confidence": 0.8,
    "reasoning": "Positive news flow.",
}


# ── Tests ─────────────────────────────────────────────────────────────


class TestTryUnwrapNested:
    """Test the _try_unwrap_nested helper."""

    def test_flat_valid_response_returns_as_is(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """A response that already has the right keys returns itself."""
        result = fundamental_agent._try_unwrap_nested(VALID_FUNDAMENTAL)
        assert result is not None
        assert result["ticker"] == "MSFT"
        assert result["signal"] == "HOLD"

    def test_single_wrapper_unwrap(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """Response wrapped in one arbitrary key gets unwrapped."""
        wrapped = {"Microsoft Fundamental Analysis": VALID_FUNDAMENTAL}
        result = fundamental_agent._try_unwrap_nested(wrapped)
        assert result is not None
        assert result["ticker"] == "MSFT"
        assert result["confidence"] == 0.7

    def test_double_nested_unwrap(
        self, sentiment_agent: BaseAgent
    ) -> None:
        """Response nested two levels deep still gets found."""
        nested = {
            "Analysis": {
                "Sentiment": VALID_SENTIMENT,
            }
        }
        result = sentiment_agent._try_unwrap_nested(nested)
        assert result is not None
        assert result["ticker"] == "MSFT"
        assert result["overall_sentiment"] == "BULLISH"

    def test_completely_wrong_returns_none(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """A response with no matching sub-dict returns None."""
        wrong = {
            "Microsoft Earnings Analysis": {
                "Video 1": {
                    "Summary": "The speaker discusses earnings...",
                    "Key Points": ["Revenue grew 20%"],
                }
            }
        }
        result = fundamental_agent._try_unwrap_nested(wrong)
        assert result is None

    def test_empty_dict_returns_none(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """An empty dict returns None."""
        result = fundamental_agent._try_unwrap_nested({})
        assert result is None

    def test_depth_limit_prevents_infinite_recursion(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """Deeply nested data beyond depth 5 won't be found."""
        # Build a 7-level deep nesting
        inner = VALID_FUNDAMENTAL
        for i in range(7):
            inner = {f"level_{i}": inner}
        result = fundamental_agent._try_unwrap_nested(inner)
        assert result is None


class TestUnwrapValidation:
    """Test that unwrapped data actually validates against the model."""

    def test_unwrapped_fundamental_validates(
        self, fundamental_agent: BaseAgent
    ) -> None:
        """Unwrapped data successfully creates a FundamentalReport."""
        wrapped = {"Wrapper": VALID_FUNDAMENTAL}
        unwrapped = fundamental_agent._try_unwrap_nested(wrapped)
        assert unwrapped is not None
        report = FundamentalReport.model_validate(unwrapped)
        assert report.ticker == "MSFT"
        assert report.signal == "HOLD"
        assert report.confidence == 0.7

    def test_unwrapped_sentiment_validates(
        self, sentiment_agent: BaseAgent
    ) -> None:
        """Unwrapped data successfully creates a SentimentReport."""
        wrapped = {"News Sentiment": VALID_SENTIMENT}
        unwrapped = sentiment_agent._try_unwrap_nested(wrapped)
        assert unwrapped is not None
        report = SentimentReport.model_validate(unwrapped)
        assert report.ticker == "MSFT"
        assert report.signal == "BUY"
        assert report.overall_sentiment == "BULLISH"
