"""Decision models — final output from the rules engine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class RuleEvaluation(BaseModel):
    """Evaluation of a single user-defined trading rule."""

    rule_text: str
    is_met: bool = False  # Default False — LLM sometimes sends null
    evidence: str = ""
    data_source: str = ""  # Which agent report provided this

    @field_validator("is_met", mode="before")
    @classmethod
    def _coerce_none_to_false(cls, v: object) -> bool:
        """LLMs sometimes return null or strings for boolean fields."""
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            v_lower = v.lower()
            if v_lower in ("false", "no", "0", "null", "none", "n/a"):
                return False
            return True
        return bool(v)


class FinalDecision(BaseModel):
    """The output of the full pipeline — the buy/hold/sell decision."""

    ticker: str
    signal: Literal["BUY", "HOLD", "SELL"]
    confidence: float = Field(ge=0.0, le=1.0)

    # Rule-by-rule breakdown
    entry_rules_evaluated: list[RuleEvaluation] = Field(default_factory=list)
    exit_rules_evaluated: list[RuleEvaluation] = Field(default_factory=list)

    # Position sizing
    suggested_position_size_pct: Optional[float] = 0.0
    suggested_entry_price: Optional[float] = 0.0
    suggested_stop_loss: Optional[float] = 0.0
    suggested_take_profit: Optional[float] = 0.0
    risk_reward_ratio: Optional[float] = 0.0

    @field_validator(
        "suggested_position_size_pct",
        "suggested_entry_price",
        "suggested_stop_loss",
        "suggested_take_profit",
        "risk_reward_ratio",
        mode="before",
    )
    @classmethod
    def _coerce_none_to_zero(cls, v: object) -> float:
        """LLMs sometimes return null for numeric fields — treat as 0.0."""
        return float(v) if v is not None else 0.0

    # Summary
    reasoning: str = ""
    dissenting_signals: list[str] = Field(default_factory=list)

    # Metadata
    timestamp: datetime = Field(default_factory=datetime.now)
    strategy_version: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_invalid_timestamp(cls, v: object) -> datetime:
        """Handle cases where LLM returns 'N/A', 'null', or invalid timestamp string."""
        if v is None:
            return datetime.now()
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            # Known invalid strings from LLM
            if v.lower() in ("n/a", "null", "none", ""):
                return datetime.now()
            # Try parsing if it looks like a date string
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                # Fallback for any other unparseable string
                return datetime.now()
        return datetime.now()
