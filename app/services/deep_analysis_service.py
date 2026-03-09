"""Deep Analysis Service — quant scoring + data distillation for each ticker.

Entry points:
  • analyze_ticker(ticker)  → QuantScorecard + distilled context → TickerDossier
  • analyze_batch(tickers)  → parallel analysis for multiple tickers
  • get_latest_dossier(ticker) → retrieve stored dossier from DuckDB

Replaces the old 4-layer funnel (QuestionGen → RAG → DossierSynth).
Now uses zero LLM calls — pure math + pure Python pre-analysis.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from app.database import get_db
from app.services.quant_engine import QuantSignalEngine
from app.services.data_distiller import DataDistiller
from app.models.dossier import TickerDossier
from app.utils.logger import logger
from app.services.event_logger import log_event


class DeepAnalysisService:
    """Run quant scoring + data distillation for ticker analysis.

    Zero LLM calls — all analysis is pure math or pure Python.
    The PortfolioStrategist handles interpretation and trading decisions.
    """

    def __init__(self) -> None:
        self._quant = QuantSignalEngine()
        self._distiller = DataDistiller()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_ticker(
        self, ticker: str, portfolio_context: dict | None = None,
        bot_id: str | None = None,
    ) -> TickerDossier:
        """Run quant analysis + data distillation for a single ticker.

        Layer 1: QuantSignalEngine  → QuantScorecard   (pure math)
        Layer 2: DataDistiller      → distilled text     (pure Python)
        """
        t0 = datetime.now()
        logger.info("=" * 60)
        logger.info("[DeepAnalysis] Starting analysis for %s", ticker)
        logger.info("=" * 60)

        # Layer 1 — synchronous, pure math
        logger.info("[DeepAnalysis] Layer 1: Computing quant scorecard …")
        scorecard = self._quant.compute(ticker)
        logger.info(
            "[DeepAnalysis] Layer 1 done: %d flags detected",
            len(scorecard.flags),
        )

        # ── Junk Quality Gate ─────────────────────────────────
        _JUNK_FLAGS = {"penny_stock", "micro_junk", "pump_dump", "illiquid"}
        junk_hits = _JUNK_FLAGS & set(scorecard.flags)
        if junk_hits:
            logger.warning(
                "[DeepAnalysis] %s FAILED quality gate: %s — removing from watchlist",
                ticker, junk_hits,
            )
            from app.services.watchlist_manager import WatchlistManager
            WatchlistManager(bot_id=bot_id or "default").remove_ticker(ticker)
            return TickerDossier(
                ticker=ticker,
                quant_scorecard=scorecard,
                executive_summary=f"Auto-removed: {', '.join(junk_hits)}",
                signal_summary=f"JUNK: {', '.join(junk_hits)}",
                conviction_score=0.0,
            )

        # Layer 2 — Data Distillation (pure Python, zero LLM calls)
        logger.info("[DeepAnalysis] Layer 2: Distilling data for %s …", ticker)
        log_event(
            "analysis", "layer_start",
            f"Distilling data for {ticker}",
            metadata={"ticker": ticker, "layer": 2},
        )

        # Fetch raw data from DuckDB for distillation
        db = get_db()

        # Price history
        prices = []
        try:
            rows = db.execute(
                "SELECT * FROM price_history WHERE ticker = ? ORDER BY date",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                prices = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] price_history fetch failed for %s: %s", ticker, exc)

        # Technicals
        technicals = []
        try:
            rows = db.execute(
                "SELECT * FROM technicals WHERE ticker = ? ORDER BY date",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                technicals = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] technicals fetch failed for %s: %s", ticker, exc)

        # Fundamentals
        fundamentals = None
        try:
            row = db.execute(
                "SELECT * FROM fundamentals WHERE ticker = ? "
                "ORDER BY snapshot_date DESC LIMIT 1",
                [ticker],
            ).fetchone()
            if row:
                cols = [d[0] for d in db.description]
                fundamentals = dict(zip(cols, row))
        except Exception as exc:
            logger.debug("[DeepAnalysis] fundamentals fetch failed for %s: %s", ticker, exc)

        # Risk metrics
        risk_metrics = None
        try:
            row = db.execute(
                "SELECT * FROM risk_metrics WHERE ticker = ? "
                "ORDER BY computed_date DESC LIMIT 1",
                [ticker],
            ).fetchone()
            if row:
                cols = [d[0] for d in db.description]
                risk_metrics = dict(zip(cols, row))
        except Exception as exc:
            logger.debug("[DeepAnalysis] risk_metrics fetch failed for %s: %s", ticker, exc)

        # Financial history (revenue trajectory for distiller)
        financial_history = []
        try:
            rows = db.execute(
                "SELECT * FROM financial_history WHERE ticker = ? ORDER BY year",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                financial_history = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] financial_history fetch failed for %s: %s", ticker, exc)

        # Balance sheet (for Altman Z-Score context)
        balance_sheet = []
        try:
            rows = db.execute(
                "SELECT * FROM balance_sheet WHERE ticker = ? ORDER BY year DESC",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                balance_sheet = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] balance_sheet fetch failed for %s: %s", ticker, exc)

        # Cash flows (for FCF yield, earnings quality)
        cashflow = []
        try:
            rows = db.execute(
                "SELECT * FROM cash_flows WHERE ticker = ? ORDER BY year DESC",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                cashflow = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] cash_flows fetch failed for %s: %s", ticker, exc)

        # Distill all data (pure Python — no LLM)
        price_analysis = self._distiller.distill_price_action(
            prices, technicals, scorecard,
        )
        fund_analysis = self._distiller.distill_fundamentals(
            fundamentals, financial_history, balance_sheet, cashflow, scorecard,
        )
        risk_analysis = self._distiller.distill_risk(risk_metrics, scorecard)

        # Build simplified TickerDossier
        # conviction_score derived from quant signals (no LLM synthesis)
        conviction = self._compute_conviction(scorecard)

        dossier = TickerDossier(
            ticker=ticker,
            quant_scorecard=scorecard,
            signal_summary=self._build_signal_summary(scorecard),
            executive_summary=price_analysis[:500] if price_analysis else "",
            bull_case=fund_analysis[:300] if fund_analysis else "",
            bear_case=risk_analysis[:300] if risk_analysis else "",
            conviction_score=conviction,
        )

        # Persist the dossier
        self._store_dossier(dossier)
        self._update_watchlist(ticker, dossier, bot_id=bot_id)

        elapsed = (datetime.now() - t0).total_seconds()
        logger.info(
            "[DeepAnalysis] %s complete in %.1fs — conviction=%.2f, flags=%s",
            ticker, elapsed, dossier.conviction_score, scorecard.flags,
        )

        return dossier

    @staticmethod
    def _compute_conviction(scorecard) -> float:
        """Derive a conviction score from quant signals (no LLM needed)."""
        score = 0.5  # baseline

        # Trend template contributes up to +/- 0.2
        if scorecard.trend_template_score > 70:
            score += 0.15
        elif scorecard.trend_template_score < 30:
            score -= 0.15

        # RS rating contributes up to +/- 0.1
        if scorecard.relative_strength_rating > 80:
            score += 0.1
        elif scorecard.relative_strength_rating < 30:
            score -= 0.1

        # Sharpe ratio
        if scorecard.sharpe_ratio > 1.5:
            score += 0.1
        elif scorecard.sharpe_ratio < 0:
            score -= 0.1

        # Junk flags push conviction down
        bad_flags = {"bankruptcy_risk", "extreme_volatility", "negative_sortino"}
        if bad_flags & set(scorecard.flags):
            score -= 0.15

        return max(0.0, min(1.0, score))

    @staticmethod
    def _build_signal_summary(scorecard) -> str:
        """One-line summary of the quant signals."""
        parts = [
            f"Trend={scorecard.trend_template_score:.0f}/100",
            f"RS={scorecard.relative_strength_rating:.0f}/100",
            f"Sharpe={scorecard.sharpe_ratio:.2f}",
            f"MaxDD={scorecard.max_drawdown:.1%}",
        ]
        if scorecard.flags:
            parts.append(f"Flags=[{', '.join(scorecard.flags[:3])}]")
        return " | ".join(parts)

    async def analyze_batch(
        self,
        tickers: list[str],
        concurrency: int = 2,
        portfolio_context: dict | None = None,
        bot_id: str | None = None,
        progress_callback: Any = None,
    ) -> list[TickerDossier]:
        """Analyze multiple tickers with bounded concurrency."""
        sem = asyncio.Semaphore(concurrency)
        results: list[TickerDossier] = []

        async def _run(t: str) -> TickerDossier:
            async with sem:
                d = await self.analyze_ticker(
                    t, portfolio_context=portfolio_context, bot_id=bot_id,
                )
                if progress_callback:
                    progress_callback(t)
                return d

        tasks = [_run(t) for t in tickers]
        dossiers = await asyncio.gather(*tasks, return_exceptions=True)

        for t, d in zip(tickers, dossiers):
            if isinstance(d, Exception):
                logger.error("[DeepAnalysis] %s failed: %s", t, d)
            else:
                results.append(d)

        logger.info(
            "[DeepAnalysis] Batch complete: %d/%d succeeded",
            len(results), len(tickers),
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
            "sector": (
                json.loads(row[4]).get("sector", "Unknown")
                if row[4] else "Unknown"
            ),
            "industry": (
                json.loads(row[4]).get("industry", "Unknown")
                if row[4] else "Unknown"
            ),
            "market_cap_tier": (
                json.loads(row[4]).get("market_cap_tier", "unknown")
                if row[4] else "unknown"
            ),
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
                "[]",  # No more QA pairs
                dossier.executive_summary,
                dossier.bull_case,
                dossier.bear_case,
                json.dumps(dossier.key_catalysts),
                dossier.conviction_score,
                dossier.total_tokens,
            ],
        )
        db.commit()
        logger.info(
            "[DeepAnalysis] Stored dossier %s for %s", dossier_id, dossier.ticker
        )

    @staticmethod
    def _update_watchlist(
        ticker: str, dossier: TickerDossier, *, bot_id: str | None = None,
    ) -> None:
        """Update the watchlist entry with analysis results."""
        db = get_db()
        now = datetime.now()

        conv = dossier.conviction_score
        if conv >= 0.7:
            signal = "BUY"
        elif conv <= 0.3:
            signal = "SELL"
        else:
            signal = "HOLD"

        try:
            if bot_id:
                db.execute(
                    """
                    UPDATE watchlist
                    SET signal = ?,
                        confidence = ?,
                        last_analyzed = ?,
                        analysis_count = analysis_count + 1,
                        updated_at = ?
                    WHERE ticker = ? AND bot_id = ?
                    """,
                    [signal, conv, now, now, ticker, bot_id],
                )
            else:
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
                ticker, signal, conv,
            )
        except Exception as exc:
            logger.warning(
                "[DeepAnalysis] Watchlist update failed for %s: %s", ticker, exc
            )
