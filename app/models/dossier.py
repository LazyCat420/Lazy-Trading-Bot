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

    # PhD-Level Quant Signals (Phase 1A)
    momentum_12m: float = 0.0          # Jegadeesh & Titman 1993: 12mo return
    mean_reversion_score: float = 0.0  # (P - SMA50) / σ50: overbought/sold
    hurst_exponent: float = 0.5        # R/S analysis: >0.5=trending, <0.5=reverting
    vwap_deviation: float = 0.0        # (P - VWAP) / VWAP: institutional signal
    fama_french_alpha: float = 0.0     # True alpha after SMB/HML factor removal
    earnings_yield_gap: float = 0.0    # E/P - Treasury 10Y: equity risk premium
    altman_z_score: float = 0.0        # Bankruptcy risk: <1.81 = danger zone
    piotroski_f_score: int = 0         # Financial health: 0-9 scale

    # Market Cap Context
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0
    market_cap_tier: str = ""

    # Minervini / O'Neil Setup Scores
    trend_template_score: float = 0.0  # 0-100: How well does it fit Stage 2 uptrend?
    vcp_setup_score: float = 0.0       # 0-100: Is volatility contracting + volume drying up?
    relative_strength_rating: float = 0.0 # 0-100: RS rating proxy (vs market)

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
