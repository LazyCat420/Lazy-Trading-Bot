"""Pydantic models for the Watchlist system (Phase 2).

WatchlistEntry  — single row from the watchlist table.
WatchlistSummary — aggregate stats for the frontend header.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WatchlistEntry(BaseModel):
    """One ticker in the watchlist."""

    ticker: str
    source: str = "manual"
    added_at: datetime | None = None
    last_analyzed: datetime | None = None
    analysis_count: int = 0

    # Latest pipeline decision
    signal: str = "PENDING"
    confidence: float = 0.0

    # Discovery metadata
    discovery_score: float = 0.0
    sentiment_hint: str = "neutral"

    # Management
    status: str = "active"
    cooldown_until: datetime | None = None
    notes: str = ""
    updated_at: datetime | None = None


class WatchlistSummary(BaseModel):
    """Aggregate stats for the watchlist header."""

    total: int = 0
    active: int = 0
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0
    pending_count: int = 0
    last_scan: datetime | None = None
    top_confidence: dict = Field(default_factory=dict)
