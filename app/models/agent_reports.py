"""Agent report models — structured JSON output schemas for each agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    reasoning: str


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
        if v is None: return 0.0
        try:
            val = float(v)
            return max(-1.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        """Clamp confidence to [0.0, 1.0]."""
        if v is None: return 0.0
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


class RiskReport(BaseModel):
    """Output from the Risk Assessment Agent."""

    ticker: str
    volatility_rating: Literal["LOW", "MODERATE", "HIGH", "EXTREME"]
    max_position_size_pct: float
    suggested_stop_loss: float
    suggested_take_profit: float
    risk_reward_ratio: float
    atr_based_stop: float = 0.0
    downside_scenarios: list[str] = Field(default_factory=list)
    portfolio_impact: str = ""
    risk_grade: Literal["LOW_RISK", "MODERATE_RISK", "HIGH_RISK", "DO_NOT_TRADE"]
    reasoning: str
