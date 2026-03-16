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
from app.models.dossier import TickerDossier
from app.services.data_distiller import DataDistiller
from app.services.event_logger import log_event
from app.services.quant_engine import QuantSignalEngine
from app.utils.logger import logger


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

        # ── Progressive Summarization: fetch additional data sources ──

        # News articles (yfinance summaries)
        news_articles = []
        try:
            rows = db.execute(
                "SELECT * FROM news_articles WHERE ticker = ? "
                "ORDER BY published_at DESC LIMIT 20",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                news_articles = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] news_articles fetch failed for %s: %s", ticker, exc)

        # News full articles (RSS/EDGAR — no ticker column, use LIKE)
        news_full = []
        try:
            rows = db.execute(
                "SELECT * FROM news_full_articles "
                "WHERE tickers_found LIKE ? "
                "ORDER BY published_at DESC LIMIT 10",
                [f"%{ticker}%"],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                news_full = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] news_full_articles fetch failed for %s: %s", ticker, exc)

        # YouTube transcripts
        yt_transcripts = []
        try:
            rows = db.execute(
                "SELECT * FROM youtube_transcripts WHERE ticker = ? "
                "ORDER BY published_at DESC LIMIT 5",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                yt_transcripts = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] youtube_transcripts fetch failed for %s: %s", ticker, exc)

        # YouTube structured trading data
        yt_trading = []
        try:
            rows = db.execute(
                "SELECT * FROM youtube_trading_data WHERE ticker = ? "
                "ORDER BY collected_at DESC LIMIT 5",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                yt_trading = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] youtube_trading_data fetch failed for %s: %s", ticker, exc)

        # SEC 13F holdings
        holdings_13f = []
        try:
            rows = db.execute(
                "SELECT * FROM sec_13f_holdings WHERE ticker = ? "
                "ORDER BY filing_date DESC LIMIT 20",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                holdings_13f = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] sec_13f_holdings fetch failed for %s: %s", ticker, exc)

        # Congressional trades (ticker nullable — guard for NULL)
        congress_trades = []
        try:
            rows = db.execute(
                "SELECT * FROM congressional_trades "
                "WHERE ticker = ? AND ticker IS NOT NULL "
                "ORDER BY tx_date DESC LIMIT 20",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                congress_trades = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] congressional_trades fetch failed for %s: %s", ticker, exc)

        # Reddit: discovered_tickers (snippets)
        reddit_snippets = []
        try:
            rows = db.execute(
                "SELECT * FROM discovered_tickers "
                "WHERE ticker = ? AND source LIKE '%reddit%' "
                "ORDER BY discovered_at DESC LIMIT 10",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                reddit_snippets = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] discovered_tickers fetch failed for %s: %s", ticker, exc)

        # Reddit: ticker_scores (aggregate)
        reddit_scores = []
        try:
            rows = db.execute(
                "SELECT * FROM ticker_scores WHERE ticker = ?",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                reddit_scores = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] ticker_scores fetch failed for %s: %s", ticker, exc)

        # Analyst data
        analyst_rows = []
        try:
            rows = db.execute(
                "SELECT * FROM analyst_data WHERE ticker = ? "
                "ORDER BY snapshot_date DESC LIMIT 5",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                analyst_rows = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] analyst_data fetch failed for %s: %s", ticker, exc)

        # Insider activity
        insider_rows = []
        try:
            rows = db.execute(
                "SELECT * FROM insider_activity WHERE ticker = ? "
                "ORDER BY snapshot_date DESC LIMIT 5",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                insider_rows = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] insider_activity fetch failed for %s: %s", ticker, exc)

        # Earnings calendar
        earnings_rows = []
        try:
            rows = db.execute(
                "SELECT * FROM earnings_calendar WHERE ticker = ? "
                "ORDER BY snapshot_date DESC LIMIT 3",
                [ticker],
            ).fetchall()
            if rows:
                cols = [d[0] for d in db.description]
                earnings_rows = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("[DeepAnalysis] earnings_calendar fetch failed for %s: %s", ticker, exc)

        # ── Distill all data (pure Python — no LLM) ──
        price_analysis = self._distiller.distill_price_action(
            prices, technicals, scorecard,
        )
        fund_analysis = self._distiller.distill_fundamentals(
            fundamentals, financial_history, balance_sheet, cashflow, scorecard,
        )
        risk_analysis = self._distiller.distill_risk(risk_metrics, scorecard)

        # Progressive summarization: distill each new data source
        all_news = news_articles + news_full
        news_analysis = self._distiller.distill_news(all_news)[:1500]
        youtube_analysis = self._distiller.distill_youtube(yt_transcripts, yt_trading)[:1000]
        smart_money_analysis = self._distiller.distill_smart_money(holdings_13f, congress_trades)[:800]
        reddit_analysis = self._distiller.distill_reddit(reddit_scores, reddit_snippets)[:500]
        peer_analysis = self._distiller.distill_peers([], fundamentals)[:1000]  # peers fetched separately
        analyst_consensus_analysis = self._distiller.distill_analyst_consensus(analyst_rows)[:500]
        insider_activity_analysis = self._distiller.distill_insider_activity(insider_rows)[:500]
        earnings_catalyst_analysis = self._distiller.distill_earnings_catalyst(earnings_rows)[:500]

        # Cross-signal synthesis (all 11 distill outputs)
        cross_signal_summary = self._distiller.distill_cross_signals(
            price_analysis, fund_analysis, risk_analysis,
            news_analysis, youtube_analysis, smart_money_analysis,
            reddit_analysis, peer_analysis, analyst_consensus_analysis,
            insider_activity_analysis, earnings_catalyst_analysis,
        )[:1000]

        # Build TickerDossier with progressive summarization
        conviction = self._compute_conviction(scorecard)

        dossier = TickerDossier(
            ticker=ticker,
            quant_scorecard=scorecard,
            signal_summary=self._build_signal_summary(scorecard),
            executive_summary=price_analysis[:2000] if price_analysis else "",
            bull_case=fund_analysis[:1000] if fund_analysis else "",
            bear_case=risk_analysis[:1000] if risk_analysis else "",
            conviction_score=conviction,
            # Progressive summarization fields
            news_analysis=news_analysis,
            youtube_analysis=youtube_analysis,
            smart_money_analysis=smart_money_analysis,
            reddit_analysis=reddit_analysis,
            peer_analysis=peer_analysis,
            analyst_consensus_analysis=analyst_consensus_analysis,
            insider_activity_analysis=insider_activity_analysis,
            earnings_catalyst_analysis=earnings_catalyst_analysis,
            cross_signal_summary=cross_signal_summary,
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
        total = len(tickers)

        async def _run(idx: int, t: str) -> TickerDossier:
            async with sem:
                logger.info(
                    "[DeepAnalysis] ➤ Starting analysis %d/%d: $%s",
                    idx + 1, total, t,
                )
                d = await self.analyze_ticker(
                    t, portfolio_context=portfolio_context, bot_id=bot_id,
                )
                logger.info(
                    "[DeepAnalysis] ✅ Finished analysis %d/%d: $%s (conviction=%.2f)",
                    idx + 1, total, t, d.conviction_score,
                )
                if progress_callback:
                    progress_callback(t)
                return d

        tasks = [_run(i, t) for i, t in enumerate(tickers)]
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
            "key_catalysts, conviction_score, total_tokens, "
            "news_analysis, youtube_analysis, smart_money_analysis, "
            "reddit_analysis, peer_analysis, analyst_consensus_analysis, "
            "insider_activity_analysis, earnings_catalyst_analysis, "
            "cross_signal_summary "
            "FROM ticker_dossiers "
            "WHERE ticker = ? ORDER BY generated_at DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if not row:
            return None

        cols = [d[0] for d in db.description]
        d = dict(zip(cols, row))

        scorecard = json.loads(d.get("scorecard_json") or "{}") if d.get("scorecard_json") else {}

        return {
            "id": d.get("id"),
            "ticker": d.get("ticker"),
            "generated_at": str(d.get("generated_at", "")),
            "version": d.get("version"),
            "scorecard": scorecard,
            "qa_pairs": json.loads(d.get("qa_pairs_json") or "[]") if d.get("qa_pairs_json") else [],
            "executive_summary": d.get("executive_summary") or "",
            "bull_case": d.get("bull_case") or "",
            "bear_case": d.get("bear_case") or "",
            "key_catalysts": json.loads(d.get("key_catalysts") or "[]") if d.get("key_catalysts") else [],
            "conviction_score": d.get("conviction_score") or 0.5,
            "total_tokens": d.get("total_tokens") or 0,
            # Progressive summarization fields
            "news_analysis": d.get("news_analysis") or "",
            "youtube_analysis": d.get("youtube_analysis") or "",
            "smart_money_analysis": d.get("smart_money_analysis") or "",
            "reddit_analysis": d.get("reddit_analysis") or "",
            "peer_analysis": d.get("peer_analysis") or "",
            "analyst_consensus_analysis": d.get("analyst_consensus_analysis") or "",
            "insider_activity_analysis": d.get("insider_activity_analysis") or "",
            "earnings_catalyst_analysis": d.get("earnings_catalyst_analysis") or "",
            "cross_signal_summary": d.get("cross_signal_summary") or "",
            # Derived from scorecard
            "sector": scorecard.get("sector", "Unknown"),
            "industry": scorecard.get("industry", "Unknown"),
            "market_cap_tier": scorecard.get("market_cap_tier", "unknown"),
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
        """Persist a TickerDossier to DuckDB.

        Dedup: delete old dossiers for this ticker before inserting
        the new one — keeps only the latest per ticker.
        """
        db = get_db()
        # Purge stale dossiers for this ticker to prevent duplication
        db.execute(
            "DELETE FROM ticker_dossiers WHERE ticker = ?",
            [dossier.ticker],
        )
        dossier_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO ticker_dossiers
                (id, ticker, generated_at, version,
                 scorecard_json, qa_pairs_json,
                 executive_summary, bull_case, bear_case,
                 key_catalysts, conviction_score, total_tokens,
                 news_analysis, youtube_analysis, smart_money_analysis,
                 reddit_analysis, peer_analysis, analyst_consensus_analysis,
                 insider_activity_analysis, earnings_catalyst_analysis,
                 cross_signal_summary)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                dossier.news_analysis,
                dossier.youtube_analysis,
                dossier.smart_money_analysis,
                dossier.reddit_analysis,
                dossier.peer_analysis,
                dossier.analyst_consensus_analysis,
                dossier.insider_activity_analysis,
                dossier.earnings_catalyst_analysis,
                dossier.cross_signal_summary,
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
