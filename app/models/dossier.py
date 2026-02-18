"""Dossier models — 4-Layer Analysis Funnel data structures.

Layer 1 → QuantScorecard  (pure math output)
Layer 3 → QAPair          (question + answer from RAG)
Layer 4 → TickerDossier   (final synthesized analysis)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class QuantScorecard(BaseModel):
    """Layer 1 output — pure numeric signals per ticker."""

    ticker: str
    computed_at: datetime = Field(default_factory=datetime.now)

    # Signal Generation
    z_score_20d: float = 0.0
    robust_z_score_20d: float = 0.0
    bollinger_pct_b: float = 0.5
    percentile_rank_price: float = 50.0
    percentile_rank_volume: float = 50.0

    # Risk / Reward
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    omega_ratio: float = 0.0
    kelly_fraction: float = 0.0
    half_kelly: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0

    # Anomaly flags for Layer 2 to investigate
    flags: list[str] = Field(default_factory=list)


class QAPair(BaseModel):
    """Layer 3 output — one answered follow-up question."""

    question: str
    answer: str = "No data available."
    source: Literal[
        "news", "transcripts", "fundamentals", "technicals", "insider"
    ] = "news"
    confidence: Literal["high", "medium", "low"] = "medium"


class TickerDossier(BaseModel):
    """Layer 4 output — the final synthesized analysis for Phase 3."""

    ticker: str
    generated_at: datetime = Field(default_factory=datetime.now)
    version: int = 1

    # Layer 1 summary
    quant_scorecard: QuantScorecard
    signal_summary: str = ""  # One-line quant interpretation

    # Layer 2 + 3 results
    qa_pairs: list[QAPair] = Field(default_factory=list)

    # Layer 4 synthesis
    executive_summary: str = ""
    bull_case: str = ""
    bear_case: str = ""
    key_catalysts: list[str] = Field(default_factory=list)
    conviction_score: float = 0.5  # 0.0 = strong sell → 1.0 = strong buy

    # Metadata
    total_tokens: int = 0
