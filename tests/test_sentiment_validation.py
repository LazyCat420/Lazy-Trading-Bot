"""Tests for SentimentReport structural rescue and fallback logic."""

from __future__ import annotations

import json

import pytest

from app.models.agent_reports import SentimentReport
from app.agents.base_agent import BaseAgent


# ---------------------------------------------------------------------------
# Direct model validation
# ---------------------------------------------------------------------------


class TestSentimentReportValidation:
    """Verify SentimentReport handles valid and edge-case inputs."""

    def test_valid_report(self) -> None:
        data = {
            "ticker": "NVDA",
            "overall_sentiment": "BULLISH",
            "sentiment_score": 0.85,
            "catalysts": ["Earnings beat"],
            "risks_mentioned": [],
            "narrative_shift": None,
            "top_headlines": [],
            "signal": "BUY",
            "confidence": 0.9,
            "reasoning": "Strong sentiment.",
        }
        report = SentimentReport.model_validate(data)
        assert report.ticker == "NVDA"
        assert report.signal == "BUY"
        assert report.overall_sentiment == "BULLISH"

    def test_clamped_score(self) -> None:
        """Sentiment score > 1.0 should be clamped to 1.0."""
        data = {
            "ticker": "NVDA",
            "overall_sentiment": "BULLISH",
            "sentiment_score": 5.0,
            "signal": "BUY",
            "confidence": 0.9,
            "reasoning": "Test",
        }
        report = SentimentReport.model_validate(data)
        assert report.sentiment_score == 1.0

    def test_invalid_sentiment_enum_defaults_to_neutral(self) -> None:
        data = {
            "ticker": "NVDA",
            "overall_sentiment": "SUPER_BULLISH",  # Invalid enum
            "sentiment_score": 0.5,
            "signal": "BUY",
            "confidence": 0.7,
            "reasoning": "Test",
        }
        report = SentimentReport.model_validate(data)
        assert report.overall_sentiment == "NEUTRAL"

    def test_wrong_structure_fails_validation(self) -> None:
        """The exact error the user reported — a Summary wrapper."""
        bad_data = {
            "Summary": {
                "Video 1": "The video discusses quarterly earnings...",
                "Video 2": "A review of growth company strategies.",
            }
        }
        with pytest.raises(Exception):
            SentimentReport.model_validate(bad_data)


# ---------------------------------------------------------------------------
# BaseAgent structural rescue helpers
# ---------------------------------------------------------------------------


class TestBaseAgentRescueHelpers:
    """Test the structural rescue methods on BaseAgent."""

    @pytest.fixture()
    def agent(self) -> BaseAgent:
        """Create a BaseAgent configured for SentimentReport."""
        return BaseAgent(
            prompt_file="sentiment_analysis.md",
            output_model=SentimentReport,
        )

    def test_get_required_keys(self, agent: BaseAgent) -> None:
        required = agent._get_required_keys()
        assert "ticker" in required
        assert "signal" in required
        assert "reasoning" in required
        assert "overall_sentiment" in required

    def test_diagnose_wrong_structure(self, agent: BaseAgent) -> None:
        """A Summary-wrapper response should be identified as wrong structure."""
        bad_json = json.dumps({
            "Summary": {"Video 1": "text"},
        })
        result = agent._diagnose_response(bad_json)
        assert result is not None
        assert "Summary" in result

    def test_diagnose_correct_structure_returns_none(self, agent: BaseAgent) -> None:
        """A correctly structured response should NOT be flagged."""
        good_json = json.dumps({
            "ticker": "NVDA",
            "overall_sentiment": "BULLISH",
            "sentiment_score": 0.85,
            "signal": "BUY",
            "confidence": 0.9,
            "reasoning": "Good sentiment.",
        })
        result = agent._diagnose_response(good_json)
        assert result is None

    def test_diagnose_invalid_json_returns_none(self, agent: BaseAgent) -> None:
        result = agent._diagnose_response("not json at all {{{")
        assert result is None

    def test_fallback_report(self, agent: BaseAgent) -> None:
        """Fallback should produce a valid NEUTRAL/HOLD report."""
        fallback = agent._build_fallback_report("NVDA")
        assert fallback is not None
        assert isinstance(fallback, SentimentReport)
        assert fallback.ticker == "NVDA"
        assert fallback.signal == "HOLD"
        assert fallback.overall_sentiment == "NEUTRAL"
        assert fallback.confidence == 0.0
        assert fallback.sentiment_score == 0.0
        assert "Fallback" in fallback.reasoning

    def test_rescue_prompt_contains_error_info(self, agent: BaseAgent) -> None:
        bad = {"Summary": {"Video 1": "text"}}
        prompt = agent._build_rescue_prompt("NVDA", bad, "some context")
        assert "WRONG structure" in prompt
        assert "Summary" in prompt
        assert "NVDA" in prompt
