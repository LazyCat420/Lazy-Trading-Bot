"""Agent report models — structured JSON output schemas for each agent.

Includes post-validation to extract structured data from reasoning
text when the LLM fails to populate array fields directly.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Helpers — extract structured data from free-text reasoning
# ---------------------------------------------------------------------------

_DOLLAR_RE = re.compile(r"\$(\d[\d,]*\.?\d*)")
"""Match dollar amounts like $450, $1,234.56."""


def _extract_dollar_levels(text: str) -> list[float]:
    """Pull unique dollar amounts from text."""
    found: list[float] = []
    for m in _DOLLAR_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
            if val > 0:
                found.append(round(val, 2))
        except ValueError:
            continue
    # Return unique, sorted
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Technical Report
# ---------------------------------------------------------------------------

class TechnicalReport(BaseModel):
    """Output from the Technical Analysis Agent."""

    ticker: str
    trend: Literal[
        "STRONG_UPTREND", "UPTREND", "SIDEWAYS", "DOWNTREND", "STRONG_DOWNTREND"
    ]
    momentum: Literal["BULLISH", "NEUTRAL", "BEARISH"]
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    key_signals: list[str] = Field(default_factory=list)
    chart_pattern: str | None = None
    signal: Literal["BUY", "HOLD", "SELL"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    @model_validator(mode="after")
    def _backfill_from_reasoning(self) -> TechnicalReport:
        """Extract support/resistance/signals from reasoning if arrays empty."""
        # Back-fill support/resistance from dollar amounts in reasoning
        if not self.support_levels or not self.resistance_levels:
            levels = _extract_dollar_levels(self.reasoning)
            if levels and not self.support_levels:
                # Lower half → support, upper half → resistance
                mid = len(levels) // 2 or 1
                self.support_levels = levels[:mid]
            if levels and not self.resistance_levels:
                mid = len(levels) // 2 or 1
                self.resistance_levels = levels[mid:]

        # Back-fill key_signals from reasoning sentences
        if not self.key_signals:
            sentences = [
                s.strip() for s in self.reasoning.split(".")
                if any(kw in s.lower() for kw in
                       ["rsi", "macd", "sma", "ema", "crossover",
                        "divergence", "hurst", "momentum", "volume",
                        "atr", "bollinger", "breakout", "support",
                        "resistance", "bullish", "bearish"])
            ]
            self.key_signals = [f"{s}." for s in sentences[:5] if len(s) > 10]

        return self


# ---------------------------------------------------------------------------
# Fundamental Report
# ---------------------------------------------------------------------------

class FundamentalReport(BaseModel):
    """Output from the Fundamental Analysis Agent."""

    ticker: str
    valuation_grade: Literal["UNDERVALUED", "FAIR", "OVERVALUED"]
    financial_health: Literal["STRONG", "MODERATE", "WEAK"]
    growth_trajectory: Literal[
        "ACCELERATING", "STEADY", "DECELERATING", "DECLINING"
    ]
    key_metrics: dict[str, str] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    intrinsic_value_estimate: float | None = None
    signal: Literal["BUY", "HOLD", "SELL"]
    confidence: float = Field(ge=0.0, le=1.0)
    industry_comparison: str = ""
    reasoning: str

    @model_validator(mode="after")
    def _backfill_fundamentals(self) -> FundamentalReport:
        """Extract strengths/risks from reasoning if arrays empty."""
        if not self.strengths:
            # Look for positive indicators in reasoning
            positive_kw = ["strong", "growth", "increasing", "healthy",
                           "improve", "solid", "robust", "consistent"]
            sentences = self.reasoning.split(".")
            self.strengths = [
                s.strip() + "."
                for s in sentences
                if any(kw in s.lower() for kw in positive_kw)
                and len(s.strip()) > 15
            ][:3]

        if not self.risks:
            negative_kw = ["risk", "decline", "weak", "concern", "debt",
                           "slow", "pressure", "downside", "volatile"]
            sentences = self.reasoning.split(".")
            self.risks = [
                s.strip() + "."
                for s in sentences
                if any(kw in s.lower() for kw in negative_kw)
                and len(s.strip()) > 15
            ][:3]

        return self


# ---------------------------------------------------------------------------
# Sentiment Report
# ---------------------------------------------------------------------------

class SentimentReport(BaseModel):
    """Output from the News Sentiment Agent."""

    ticker: str
    overall_sentiment: Literal[
        "VERY_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "VERY_BEARISH"
    ]
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    catalysts: list[str] = Field(default_factory=list)
    risks_mentioned: list[str] = Field(default_factory=list)
    narrative_shift: str | None = None
    top_headlines: list[dict[str, str]] = Field(default_factory=list)
    signal: Literal["BUY", "HOLD", "SELL"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    @field_validator("sentiment_score", mode="before")
    @classmethod
    def _clamp_score(cls, v: float) -> float:
        """Clamp sentiment score to [-1.0, 1.0]."""
        if v is None:
            return 0.0
        try:
            val = float(v)
            return max(-1.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        """Clamp confidence to [0.0, 1.0]."""
        if v is None:
            return 0.0
        try:
            val = float(v)
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("overall_sentiment", mode="before")
    @classmethod
    def _validate_sentiment_enum(cls, v: str) -> str:
        """Handle casing or fallback for sentiment enum."""
        valid = {"VERY_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "VERY_BEARISH"}
        if v is None:
            return "NEUTRAL"
        v_upper = str(v).upper()
        if v_upper in valid:
            return v_upper
        return "NEUTRAL"

    @field_validator("signal", mode="before")
    @classmethod
    def _validate_signal_enum(cls, v: str) -> str:
        """Handle casing or fallback for signal enum."""
        valid = {"BUY", "HOLD", "SELL"}
        if v is None:
            return "HOLD"
        v_upper = str(v).upper()
        if v_upper in valid:
            return v_upper
        return "HOLD"


# ---------------------------------------------------------------------------
# Scenario Case (Fix 4 — structured bull/base/bear)
# ---------------------------------------------------------------------------

class ScenarioCase(BaseModel):
    """A single scenario (bull, base, or bear) for risk modeling."""

    label: str = ""
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    price_target: float | None = None

    @field_validator("probability", mode="before")
    @classmethod
    def _clamp_prob(cls, v: object) -> float:
        """Handle null/out-of-range probability."""
        if v is None:
            return 0.0
        try:
            val = float(v)
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0


# ---------------------------------------------------------------------------
# Risk Report
# ---------------------------------------------------------------------------

class RiskReport(BaseModel):
    """Output from the Risk Assessment Agent."""

    ticker: str
    volatility_rating: Literal["LOW", "MODERATE", "HIGH", "EXTREME"]
    max_position_size_pct: float
    entry_price: float = 0.0  # Current market price reference (Fix 5)
    suggested_stop_loss: float  # Absolute dollar price level
    suggested_take_profit: float  # Absolute dollar price level
    risk_reward_ratio: float
    atr_based_stop: float = 0.0
    downside_scenarios: list[str] = Field(default_factory=list)
    portfolio_impact: str = ""
    risk_grade: Literal["LOW_RISK", "MODERATE_RISK", "HIGH_RISK", "DO_NOT_TRADE"]
    reasoning: str

    # Structured scenario modeling (Fix 4)
    bull_case: ScenarioCase | None = None
    base_case: ScenarioCase | None = None
    bear_case: ScenarioCase | None = None

    @field_validator("entry_price", mode="before")
    @classmethod
    def _coerce_entry(cls, v: object) -> float:
        """Handle null entry price."""
        if v is None:
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    @model_validator(mode="after")
    def _backfill_risk_data(self) -> RiskReport:
        """Extract downside scenarios from reasoning if empty."""
        if not self.downside_scenarios:
            scenario_kw = ["bear", "bull", "base", "worst", "downside",
                           "scenario", "case", "if", "drop", "fall"]
            sentences = self.reasoning.split(".")
            self.downside_scenarios = [
                s.strip() + "."
                for s in sentences
                if any(kw in s.lower() for kw in scenario_kw)
                and len(s.strip()) > 15
            ][:3]

        # Back-fill scenario cases from reasoning if not provided
        if not self.bull_case:
            self.bull_case = ScenarioCase(
                label="Bull", probability=0.3,
                description="Trend continues with momentum.",
            )
        if not self.base_case:
            self.base_case = ScenarioCase(
                label="Base", probability=0.45,
                description="Sideways consolidation within current range.",
            )
        if not self.bear_case:
            self.bear_case = ScenarioCase(
                label="Bear", probability=0.25,
                description="Reversal toward key support levels.",
            )

        return self
