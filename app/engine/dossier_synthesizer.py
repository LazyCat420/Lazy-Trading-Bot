"""Layer 4 — Dossier Synthesizer.

Takes the QuantScorecard (Layer 1) and QAPairs (Layer 3) and compresses
everything into a single TickerDossier.  One LLM call produces:
  • Executive summary (3-5 sentences)
  • Bull case / Bear case
  • Key catalysts (3-5 upcoming events)
  • Conviction score (0.0-1.0)
"""

from __future__ import annotations

import json

from app.models.dossier import QAPair, QuantScorecard, TickerDossier
from app.services.llm_service import LLMService
from app.utils.logger import logger

SYNTHESIS_SYSTEM_PROMPT = """\
You are synthesizing a trading analysis dossier.  Compress all information
into a concise, decision-ready format.

COMPANY PROFILE:
Ticker: {ticker}
Sector: {sector}
Industry: {industry}
Market Cap: {market_cap_formatted} ({cap_tier}-cap)

TECHNICAL SETUP:
Trend Template Score: {trend_score}/100 ({trend_status})
VCP Setup Score: {vcp_score}/100 ({vcp_status})
RS Rating: {rs_rating}/100

QUANT SCORECARD:
{scorecard}

Q&A RESEARCH:
{qa_pairs}
{portfolio_section}
Generate a JSON object with exactly these keys:
{{
  "executive_summary": "3-5 sentences covering the thesis",
  "bull_case": "strongest arguments for buying (2-3 sentences)",
  "bear_case": "strongest arguments against buying (2-3 sentences)",
  "key_catalysts": ["catalyst 1", "catalyst 2", "catalyst 3"],
  "conviction_score": <CALCULATE — see calibration rules below>,
  "signal_summary": "One-line quant interpretation",
  "sector": "{sector}",
  "industry": "{industry}",
  "market_cap_tier": "{cap_tier}"
}}

CONVICTION SCORE CALIBRATION — you MUST calculate this from the data:

Step 1: Start with the Trend Template Score as your base:
  - Trend >= 80 → base = 0.70
  - Trend 50-79 → base = 0.50
  - Trend < 50  → base = 0.25

Step 2: Adjust based on flags and data:
  - bankruptcy_risk_high → subtract 0.20
  - negative_sortino → subtract 0.10
  - exceptional_calmar or strong_momentum_up → add 0.10
  - piotroski_strong (F-Score >= 7) → add 0.05
  - drawdown_exceeds_20pct → subtract 0.05
  - Strong bull case from Q&A research → add 0.05
  - Serious bear case risks from Q&A → subtract 0.05

Step 3: Clamp the final score to [0.10, 0.95].

CALIBRATION EXAMPLES (your scores MUST vary like this):
  - Trend=100, RS=99, strong_momentum, no red flags → conviction ~0.80-0.85
  - Trend=90, RS=88, drawdown, solid fundamentals → conviction ~0.65-0.70
  - Trend=40, RS=70, bankruptcy_risk → conviction ~0.15-0.25
  - Trend=0, RS=2, negative_sortino, drawdown → conviction ~0.10-0.20

CRITICAL: Every stock MUST get a DIFFERENT score. If you give the same score
to two stocks with different data, you have FAILED. The whole point is to RANK
stocks by attractiveness.

Additional rules:
- CAP-TIER CONTEXT:
  * Mega/Large cap: Weight competitive position and cash flow over pure growth.
  * Mid cap: Weight revenue growth trajectory and market expansion heavily.
  * Small/Micro cap: Require STRONGER catalysts for conviction above 0.70.
- Be specific with numbers, dates, and percentages
- Keep total output under 2000 characters
- Factor in the portfolio context when assigning conviction
- Respond ONLY with the JSON object, no markdown fences
"""


class DossierSynthesizer:
    """Synthesize a full TickerDossier from scorecard + Q&A pairs."""

    def __init__(self) -> None:
        self._llm = LLMService()

    async def synthesize(
        self,
        scorecard: QuantScorecard,
        qa_pairs: list[QAPair],
        portfolio_context: dict | None = None,
    ) -> TickerDossier:
        """Run the synthesis LLM call and return a TickerDossier."""
        ticker = scorecard.ticker

        # Format inputs
        sc_text = scorecard.model_dump_json(indent=2)
        qa_text = "\n".join(
            f"Q: {p.question}\nA: {p.answer} (source={p.source}, conf={p.confidence})"
            for p in qa_pairs
        )

        # Build portfolio section
        portfolio_section = ""
        if portfolio_context:
            pos_info = portfolio_context.get("positions", {})
            ticker_pos = pos_info.get(ticker, {})
            if ticker_pos:
                pos_line = (
                    f"Current Position in {ticker}: "
                    f"{ticker_pos['qty']} shares @ ${ticker_pos['avg_entry']:.2f} "
                    f"(cost basis ${ticker_pos['cost_basis']:.2f})"
                )
            else:
                pos_line = f"No current position in {ticker}"

            other_positions = [f"{t} ({d['qty']} shares)" for t, d in pos_info.items() if t != ticker]
            portfolio_section = (
                f"\nPORTFOLIO CONTEXT:\n"
                f"Cash Available: ${portfolio_context.get('cash_balance', 0):.2f}\n"
                f"Total Portfolio Value: ${portfolio_context.get('total_portfolio_value', 0):.2f}\n"
                f"{pos_line}\n"
                f"Other Positions: {', '.join(other_positions) if other_positions else 'None'}\n"
                f"Realized P&L: ${portfolio_context.get('realized_pnl', 0):.2f}\n"
            )

        # Format market cap for readability
        mc = scorecard.market_cap
        if mc >= 1e12:
            mc_str = f"${mc / 1e12:.2f}T"
        elif mc >= 1e9:
            mc_str = f"${mc / 1e9:.1f}B"
        elif mc >= 1e6:
            mc_str = f"${mc / 1e6:.0f}M"
        else:
            mc_str = f"${mc:,.0f}"

        # Prepare Setup Status strings
        t_score = getattr(scorecard, "trend_template_score", 0)
        v_score = getattr(scorecard, "vcp_setup_score", 0)
        rs = getattr(scorecard, "relative_strength_rating", 0)

        if t_score > 80:
            t_status = "Stage 2 Uptrend (Bullish)"
        elif t_score < 50:
            t_status = "Downtrend/Broken (Bearish)"
        else:
            t_status = "Choppy/Base (Neutral)"

        if v_score > 70:
            v_status = "Tight VCP Action"
        else:
            v_status = "Loose/Volatile"

        prompt = SYNTHESIS_SYSTEM_PROMPT.format(
            ticker=ticker,
            sector=scorecard.sector or "Unknown",
            industry=scorecard.industry or "Unknown",
            market_cap_formatted=mc_str,
            cap_tier=scorecard.market_cap_tier or "unknown",
            trend_score=int(t_score),
            trend_status=t_status,
            vcp_score=int(v_score),
            vcp_status=v_status,
            rs_rating=int(rs),
            scorecard=sc_text,
            qa_pairs=qa_text,
            portfolio_section=portfolio_section,
        )

        # ── Proactive context-window guardrail ─────────────────────
        # Estimate total tokens and trim Q&A if too large
        user_msg = f"Synthesize the dossier for {ticker}."
        total_chars = len(prompt) + len(user_msg)
        max_chars = self._llm.context_size * 4  # ~4 chars per token
        budget = int(max_chars * 0.75)  # reserve 25% for response

        if total_chars > budget and qa_pairs:
            # Sort by confidence ascending → drop lowest first
            sorted_pairs = sorted(qa_pairs, key=lambda p: p.confidence)
            kept = list(qa_pairs)
            while total_chars > budget and sorted_pairs:
                drop = sorted_pairs.pop(0)
                kept = [p for p in kept if p is not drop]
                qa_text = "\n".join(
                    f"Q: {p.question}\nA: {p.answer} "
                    f"(source={p.source}, conf={p.confidence})"
                    for p in kept
                )
                prompt = SYNTHESIS_SYSTEM_PROMPT.format(
                    ticker=ticker,
                    sector=scorecard.sector or "Unknown",
                    industry=scorecard.industry or "Unknown",
                    market_cap_formatted=mc_str,
                    cap_tier=scorecard.market_cap_tier or "unknown",
                    trend_score=int(t_score),
                    trend_status=t_status,
                    vcp_score=int(v_score),
                    vcp_status=v_status,
                    rs_rating=int(rs),
                    scorecard=sc_text,
                    qa_pairs=qa_text,
                    portfolio_section=portfolio_section,
                )
                total_chars = len(prompt) + len(user_msg)
            logger.info(
                "[Dossier] %s: trimmed Q&A from %d → %d pairs "
                "(~%d chars, budget=%d)",
                ticker, len(qa_pairs), len(kept),
                total_chars, budget,
            )

        try:
            raw = await self._llm.chat(
                system=prompt,
                user=f"Synthesize the dossier for {ticker}.",
                response_format="json",
                max_tokens=1500,
            )
            cleaned = LLMService.clean_json_response(raw)
            data = json.loads(cleaned)

            dossier = TickerDossier(
                ticker=ticker,
                quant_scorecard=scorecard,
                signal_summary=str(data.get("signal_summary", "")),
                qa_pairs=qa_pairs,
                executive_summary=str(data.get("executive_summary", "")),
                bull_case=str(data.get("bull_case", "")),
                bear_case=str(data.get("bear_case", "")),
                key_catalysts=data.get("key_catalysts", []),
                conviction_score=float(data.get("conviction_score", 0.5)),
                total_tokens=len(sc_text) + len(qa_text) + len(raw),
            )

            logger.info(
                "[Dossier] %s → conviction=%.2f, summary=%d chars",
                ticker,
                dossier.conviction_score,
                len(dossier.executive_summary),
            )
            return dossier

        except Exception as exc:
            logger.error("[Dossier] Synthesis failed for %s: %s", ticker, exc)
            # Return a minimal dossier so the pipeline doesn't crash
            return TickerDossier(
                ticker=ticker,
                quant_scorecard=scorecard,
                signal_summary="Synthesis failed — manual review recommended",
                qa_pairs=qa_pairs,
                executive_summary=f"Automated synthesis failed: {exc}",
                conviction_score=0.5,
            )
