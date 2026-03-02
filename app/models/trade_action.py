"""TradeAction — the one true decision schema for LLM trading decisions.

Every LLM trade decision MUST return a TradeAction.
Validated by trade_action_parser before execution.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TradeAction(BaseModel):
    """Strict schema for a single trading decision."""

    bot_id: str
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=1, description="0.0=no confidence, 1.0=max confidence")
    rationale: str = Field(description="1-3 sentence explanation for the decision")
    risk_notes: str = ""
    risk_level: Literal["LOW", "MED", "HIGH"] = "MED"
    time_horizon: Literal["INTRADAY", "SWING", "POSITION"] = "SWING"


# JSON Schema for Ollama structured output enforcement.
# Passed as `schema` kwarg to LLMService.chat() so the LLM
# is grammar-constrained to produce valid TradeAction JSON.
TRADE_ACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"],
        },
        "symbol": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "risk_notes": {"type": "string"},
        "risk_level": {
            "type": "string",
            "enum": ["LOW", "MED", "HIGH"],
        },
        "time_horizon": {
            "type": "string",
            "enum": ["INTRADAY", "SWING", "POSITION"],
        },
    },
    "required": ["action", "symbol", "confidence", "rationale"],
}
