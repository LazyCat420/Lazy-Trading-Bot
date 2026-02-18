"""Trading models — Position, Order, PortfolioSnapshot, PriceTrigger.

Used by the Phase 3 Trading Engine (SignalRouter → PaperTrader → PriceMonitor).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Position(BaseModel):
    """A single open position in the paper portfolio."""

    ticker: str
    qty: int
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop_pct: float = 0.0  # e.g. 5.0 = sell if drops 5% from peak
    opened_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)


class Order(BaseModel):
    """A single trade order (filled or pending)."""

    id: str  # UUID
    ticker: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    status: Literal["pending", "filled", "cancelled", "failed"] = "filled"
    filled_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    # Link back to the dossier signal
    conviction_score: float = 0.0
    signal: str = ""  # BUY / SELL / HOLD


class PortfolioSnapshot(BaseModel):
    """Point-in-time snapshot of the full portfolio for equity curve."""

    timestamp: datetime = Field(default_factory=datetime.now)
    cash_balance: float
    total_positions_value: float
    total_portfolio_value: float
    realized_pnl: float = 0.0  # cumulative
    unrealized_pnl: float = 0.0  # current


class PriceTrigger(BaseModel):
    """A stop-loss, take-profit, or trailing stop trigger."""

    id: str  # UUID
    ticker: str
    trigger_type: Literal["stop_loss", "take_profit", "trailing_stop"]
    trigger_price: float
    high_water_mark: float = 0.0
    trailing_pct: float = 0.0
    action: Literal["sell"] = "sell"  # triggers always sell for safety
    qty: int
    status: Literal["active", "triggered", "cancelled"] = "active"
    created_at: datetime = Field(default_factory=datetime.now)
