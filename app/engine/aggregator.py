"""Aggregator — pools all agent reports and feeds them to the rules engine."""

from __future__ import annotations

import json
from typing import Any

from app.models.agent_reports import (
    FundamentalReport,
    RiskReport,
    SentimentReport,
    TechnicalReport,
)
from app.utils.logger import logger


class PooledAnalysis:
    """Container for all agent reports, ready for the rules engine."""

    def __init__(
        self,
        ticker: str,
        technical: TechnicalReport | None = None,
        fundamental: FundamentalReport | None = None,
        sentiment: SentimentReport | None = None,
        risk: RiskReport | None = None,
    ) -> None:
        self.ticker = ticker
        self.technical = technical
        self.fundamental = fundamental
        self.sentiment = sentiment
        self.risk = risk

    def to_summary(self) -> dict[str, Any]:
        """Return a summary dict for logging and display."""
        return {
            "ticker": self.ticker,
            "technical": {
                "signal": self.technical.signal if self.technical else "N/A",
                "confidence": self.technical.confidence if self.technical else 0,
                "trend": self.technical.trend if self.technical else "N/A",
            },
            "fundamental": {
                "signal": self.fundamental.signal if self.fundamental else "N/A",
                "confidence": self.fundamental.confidence if self.fundamental else 0,
                "valuation": self.fundamental.valuation_grade if self.fundamental else "N/A",
            },
            "sentiment": {
                "signal": self.sentiment.signal if self.sentiment else "N/A",
                "confidence": self.sentiment.confidence if self.sentiment else 0,
                "overall": self.sentiment.overall_sentiment if self.sentiment else "N/A",
            },
            "risk": {
                "grade": self.risk.risk_grade if self.risk else "N/A",
                "max_position": self.risk.max_position_size_pct if self.risk else 0,
                "risk_reward": self.risk.risk_reward_ratio if self.risk else 0,
            },
        }

    def full_reports(self) -> dict[str, Any]:
        """Return complete serialized agent reports for the frontend."""
        def _dump(report: Any) -> dict | None:
            if report is None:
                return None
            return json.loads(report.model_dump_json())

        return {
            "technical": _dump(self.technical),
            "fundamental": _dump(self.fundamental),
            "sentiment": _dump(self.sentiment),
            "risk": _dump(self.risk),
        }

    def format_for_decision_maker(self) -> dict[str, str]:
        """Format all reports as strings for injection into the decision prompt."""
        def report_to_str(report: Any) -> str:
            if report is None:
                return "Report unavailable — agent failed or data missing."
            return report.model_dump_json(indent=2)

        return {
            "technical_report": report_to_str(self.technical),
            "fundamental_report": report_to_str(self.fundamental),
            "sentiment_report": report_to_str(self.sentiment),
            "risk_report": report_to_str(self.risk),
        }


class Aggregator:
    """Collects agent reports into a PooledAnalysis."""

    def pool(
        self,
        ticker: str,
        technical: TechnicalReport | None = None,
        fundamental: FundamentalReport | None = None,
        sentiment: SentimentReport | None = None,
        risk: RiskReport | None = None,
    ) -> PooledAnalysis:
        """Pool all agent reports into a single analysis object."""
        pooled = PooledAnalysis(
            ticker=ticker,
            technical=technical,
            fundamental=fundamental,
            sentiment=sentiment,
            risk=risk,
        )
        logger.info(
            "Pooled analysis for %s: %s",
            ticker,
            json.dumps(pooled.to_summary(), indent=2),
        )
        return pooled
