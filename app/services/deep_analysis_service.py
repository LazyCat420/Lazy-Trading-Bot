"""Deep Analysis Service — orchestrates the 4-Layer Analysis Funnel.

Entry points:
  • analyze_ticker(ticker)  → full pipeline for one ticker
  • analyze_batch(tickers)  → parallel analysis for multiple tickers
  • get_latest_dossier(ticker) → retrieve stored dossier from DuckDB
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from app.database import get_db
from app.engine.dossier_synthesizer import DossierSynthesizer
from app.engine.question_generator import QuestionGenerator
from app.engine.quant_signals import QuantSignalEngine
from app.engine.rag_engine import RAGEngine
from app.models.dossier import QAPair, QuantScorecard, TickerDossier
from app.utils.logger import logger


class DeepAnalysisService:
    """Orchestrate Layer 1 → 2 → 3 → 4 for ticker analysis."""

    def __init__(self) -> None:
        self._quant = QuantSignalEngine()
        self._questions = QuestionGenerator()
        self._rag = RAGEngine()
        self._synth = DossierSynthesizer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_ticker(self, ticker: str) -> TickerDossier:
        """Run the full 4-layer funnel for a single ticker.

        Layer 1: QuantSignalEngine  → QuantScorecard   (pure math)
        Layer 2: QuestionGenerator  → 5 follow-up Qs   (1 LLM call)
        Layer 3: RAGEngine          → 5 QAPairs         (5 LLM calls)
        Layer 4: DossierSynthesizer → TickerDossier     (1 LLM call)
        """
        t0 = datetime.now()
        logger.info("=" * 60)
        logger.info("[DeepAnalysis] Starting full analysis for %s", ticker)
        logger.info("=" * 60)

        # Layer 1 — synchronous, pure math
        logger.info("[DeepAnalysis] Layer 1: Computing quant scorecard …")
        scorecard = self._quant.compute(ticker)
        logger.info(
            "[DeepAnalysis] Layer 1 done: %d flags detected",
            len(scorecard.flags),
        )

        # Layer 2 — async LLM call
        logger.info("[DeepAnalysis] Layer 2: Generating follow-up questions …")
        questions = await self._questions.generate(scorecard)
        logger.info(
            "[DeepAnalysis] Layer 2 done: %d questions generated",
            len(questions),
        )

        # Layer 3 — async LLM calls (one per question)
        logger.info("[DeepAnalysis] Layer 3: Searching data & extracting answers …")
        qa_pairs = await self._rag.answer_all(questions, ticker)
        logger.info(
            "[DeepAnalysis] Layer 3 done: %d answers extracted",
            len(qa_pairs),
        )

        # Layer 4 — async LLM call (synthesis)
        logger.info("[DeepAnalysis] Layer 4: Synthesizing dossier …")
        dossier = await self._synth.synthesize(scorecard, qa_pairs)
        logger.info(
            "[DeepAnalysis] Layer 4 done: conviction=%.2f",
            dossier.conviction_score,
        )

        # Persist the full dossier
        self._store_dossier(dossier)

        # Update the watchlist entry with conviction info
        self._update_watchlist(ticker, dossier)

        elapsed = (datetime.now() - t0).total_seconds()
        logger.info(
            "[DeepAnalysis] %s complete in %.1fs — conviction=%.2f, "
            "flags=%s",
            ticker,
            elapsed,
            dossier.conviction_score,
            scorecard.flags,
        )

        return dossier

    async def analyze_batch(
        self,
        tickers: list[str],
        concurrency: int = 2,
    ) -> list[TickerDossier]:
        """Analyze multiple tickers with bounded concurrency.

        Default concurrency=2 to avoid overwhelming the LLM backend.
        """
        sem = asyncio.Semaphore(concurrency)
        results: list[TickerDossier] = []

        async def _run(t: str) -> TickerDossier:
            async with sem:
                return await self.analyze_ticker(t)

        tasks = [_run(t) for t in tickers]
        dossiers = await asyncio.gather(*tasks, return_exceptions=True)

        for t, d in zip(tickers, dossiers):
            if isinstance(d, Exception):
                logger.error("[DeepAnalysis] %s failed: %s", t, d)
            else:
                results.append(d)

        logger.info(
            "[DeepAnalysis] Batch complete: %d/%d succeeded",
            len(results),
            len(tickers),
        )
        return results

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def get_latest_dossier(ticker: str) -> dict | None:
        """Retrieve the most recent dossier for a ticker from DuckDB."""
        db = get_db()
        row = db.execute(
            "SELECT id, ticker, generated_at, version, "
            "scorecard_json, qa_pairs_json, "
            "executive_summary, bull_case, bear_case, "
            "key_catalysts, conviction_score, total_tokens "
            "FROM ticker_dossiers "
            "WHERE ticker = ? ORDER BY generated_at DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "ticker": row[1],
            "generated_at": str(row[2]),
            "version": row[3],
            "scorecard": json.loads(row[4]) if row[4] else {},
            "qa_pairs": json.loads(row[5]) if row[5] else [],
            "executive_summary": row[6] or "",
            "bull_case": row[7] or "",
            "bear_case": row[8] or "",
            "key_catalysts": json.loads(row[9]) if row[9] else [],
            "conviction_score": row[10] or 0.5,
            "total_tokens": row[11] or 0,
        }

    @staticmethod
    def get_latest_scorecard(ticker: str) -> dict | None:
        """Retrieve the most recent quant scorecard for a ticker."""
        db = get_db()
        row = db.execute(
            "SELECT id, ticker, computed_at, "
            "z_score_20d, robust_z_score, bollinger_pct_b, "
            "pctl_rank_price, pctl_rank_volume, "
            "sharpe_ratio, sortino_ratio, calmar_ratio, "
            "omega_ratio, kelly_fraction, half_kelly, "
            "var_95, cvar_95, max_drawdown, flags "
            "FROM quant_scorecards "
            "WHERE ticker = ? ORDER BY computed_at DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "ticker": row[1],
            "computed_at": str(row[2]),
            "z_score_20d": row[3],
            "robust_z_score": row[4],
            "bollinger_pct_b": row[5],
            "pctl_rank_price": row[6],
            "pctl_rank_volume": row[7],
            "sharpe_ratio": row[8],
            "sortino_ratio": row[9],
            "calmar_ratio": row[10],
            "omega_ratio": row[11],
            "kelly_fraction": row[12],
            "half_kelly": row[13],
            "var_95": row[14],
            "cvar_95": row[15],
            "max_drawdown": row[16],
            "flags": json.loads(row[17]) if row[17] else [],
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _store_dossier(dossier: TickerDossier) -> None:
        """Persist a TickerDossier to DuckDB."""
        db = get_db()
        dossier_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO ticker_dossiers
                (id, ticker, generated_at, version,
                 scorecard_json, qa_pairs_json,
                 executive_summary, bull_case, bear_case,
                 key_catalysts, conviction_score, total_tokens)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                dossier_id,
                dossier.ticker,
                dossier.generated_at,
                dossier.version,
                dossier.quant_scorecard.model_dump_json(),
                json.dumps([p.model_dump() for p in dossier.qa_pairs]),
                dossier.executive_summary,
                dossier.bull_case,
                dossier.bear_case,
                json.dumps(dossier.key_catalysts),
                dossier.conviction_score,
                dossier.total_tokens,
            ],
        )
        db.commit()
        logger.info("[DeepAnalysis] Stored dossier %s for %s", dossier_id, dossier.ticker)

    @staticmethod
    def _update_watchlist(ticker: str, dossier: TickerDossier) -> None:
        """Update the watchlist entry with analysis results."""
        db = get_db()
        now = datetime.now()

        # Convert conviction to a signal label
        conv = dossier.conviction_score
        if conv >= 0.7:
            signal = "BUY"
        elif conv <= 0.3:
            signal = "SELL"
        else:
            signal = "HOLD"

        try:
            db.execute(
                """
                UPDATE watchlist
                SET signal = ?,
                    confidence = ?,
                    last_analyzed = ?,
                    analysis_count = analysis_count + 1,
                    updated_at = ?
                WHERE ticker = ?
                """,
                [signal, conv, now, now, ticker],
            )
            db.commit()
            logger.info(
                "[DeepAnalysis] Updated watchlist %s → signal=%s, confidence=%.2f",
                ticker,
                signal,
                conv,
            )
        except Exception as exc:
            logger.warning("[DeepAnalysis] Watchlist update failed for %s: %s", ticker, exc)
