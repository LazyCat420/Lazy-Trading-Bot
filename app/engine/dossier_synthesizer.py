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
  "conviction_score": 0.65,
  "signal_summary": "One-line quant interpretation"
}}

Rules:
- conviction_score: 0.0 = strong sell, 0.5 = hold, 1.0 = strong buy
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

        prompt = SYNTHESIS_SYSTEM_PROMPT.format(
            scorecard=sc_text,
            qa_pairs=qa_text,
            portfolio_section=portfolio_section,
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
