"""Discovery models — scored ticker candidates from Reddit/YouTube."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ScoredTicker(BaseModel):
    """A ticker candidate discovered from Reddit or YouTube with a score."""

    ticker: str
    discovery_score: float = 0.0
    source: Literal["youtube", "reddit", "reddit+youtube"] = "reddit"
    source_detail: str = ""  # channel name or subreddit
    sentiment_hint: Literal["bullish", "bearish", "neutral"] = "neutral"
    context_snippets: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=datetime.now)
    last_seen: datetime = Field(default_factory=datetime.now)


class DiscoveryResult(BaseModel):
    """Result of a full discovery run (Reddit + YouTube combined)."""

    tickers: list[ScoredTicker] = Field(default_factory=list)
    reddit_count: int = 0
    youtube_count: int = 0
    transcript_count: int = 0
    run_at: datetime = Field(default_factory=datetime.now)
    duration_seconds: float = 0.0
