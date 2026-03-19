"""WatchlistManager — bridges Discovery (Phase 1) to Analysis (PipelineService).

Reads top-scoring discovered tickers, adds them to the watchlist,
runs the full analysis pipeline on each, and stores the signal+confidence.

Usage (from main.py):
    wm = WatchlistManager()
    await wm.import_from_discovery(min_score=5.0, max_tickers=10)
    await wm.analyze_all(batch_size=2)
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import asyncio
import time
from datetime import datetime
from typing import Any

from app.database import get_db
from app.models.watchlist import WatchlistSummary
from app.services.pipeline_service import PipelineService
from app.utils.logger import logger


@track_class_telemetry
class WatchlistManager:
    """Manages the watchlist — adding, removing, and analyzing tickers."""

    def __init__(self, bot_id: str = "default") -> None:
        self.pipeline = PipelineService()
        self.bot_id = bot_id

    # ── Read operations ───────────────────────────────────────────

    def get_watchlist(self, include_removed: bool = False) -> list[dict]:
        """Return all watchlist entries as dicts."""
        db = get_db()
        if include_removed:
            rows = db.execute(
                """
                SELECT ticker, source, added_at, last_analyzed, analysis_count,
                       signal, confidence, discovery_score, sentiment_hint,
                       status, cooldown_until, notes, updated_at
                FROM watchlist
                WHERE bot_id = ?
                ORDER BY confidence DESC, added_at DESC
                """,
                [self.bot_id],
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT ticker, source, added_at, last_analyzed, analysis_count,
                       signal, confidence, discovery_score, sentiment_hint,
                       status, cooldown_until, notes, updated_at
                FROM watchlist
                WHERE status = 'active' AND bot_id = ?
                ORDER BY confidence DESC, added_at DESC
                """,
                [self.bot_id],
            ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def get_active_tickers(self) -> list[str]:
        """Return just the active ticker symbols as strings."""
        db = get_db()
        rows = db.execute(
            "SELECT ticker FROM watchlist WHERE status = 'active' "
            "AND bot_id = ? ORDER BY confidence DESC, added_at DESC",
            [self.bot_id],
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_active_tickers_with_staleness(self) -> list[dict]:
        """Return active tickers with last_analyzed and last_collected timestamps."""
        db = get_db()
        rows = db.execute(
            "SELECT ticker, last_analyzed, last_collected FROM watchlist WHERE status = 'active' "
            "AND bot_id = ? ORDER BY confidence DESC, added_at DESC",
            [self.bot_id],
        ).fetchall()
        return [
            {"ticker": str(r[0]), "last_analyzed": r[1], "last_collected": r[2]}
            for r in rows
        ]

    def get_ticker_signals(self) -> dict[str, str]:
        """Return {ticker: signal} for all active tickers (for priority sorting)."""
        db = get_db()
        rows = db.execute(
            "SELECT ticker, signal FROM watchlist "
            "WHERE status = 'active' AND bot_id = ?",
            [self.bot_id],
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}

    def get_summary(self) -> dict:
        """Return aggregate stats for the frontend header."""
        db = get_db()

        total_row = db.execute(
            "SELECT COUNT(*) FROM watchlist WHERE status = 'active' AND bot_id = ?",
            [self.bot_id],
        ).fetchone()
        total = total_row[0] if total_row else 0

        signal_rows = db.execute(
            """
            SELECT signal, COUNT(*) as cnt
            FROM watchlist
            WHERE status = 'active' AND bot_id = ?
            GROUP BY signal
            """,
            [self.bot_id],
        ).fetchall()

        signal_counts: dict[str, int] = {}
        for row in signal_rows:
            signal_counts[row[0]] = row[1]

        last_row = db.execute(
            "SELECT MAX(last_analyzed) FROM watchlist WHERE status = 'active' AND bot_id = ?",
            [self.bot_id],
        ).fetchone()

        top_row = db.execute(
            """
            SELECT ticker, confidence, signal
            FROM watchlist
            WHERE status = 'active' AND signal != 'PENDING' AND bot_id = ?
            ORDER BY confidence DESC
            LIMIT 1
            """,
            [self.bot_id],
        ).fetchone()

        summary = WatchlistSummary(
            total=total,
            active=total,
            buy_count=signal_counts.get("BUY", 0)
            + signal_counts.get("STRONG_BUY", 0),
            sell_count=signal_counts.get("SELL", 0)
            + signal_counts.get("STRONG_SELL", 0),
            hold_count=signal_counts.get("HOLD", 0),
            pending_count=signal_counts.get("PENDING", 0),
            last_scan=(
                datetime.fromisoformat(str(last_row[0]))
                if last_row and last_row[0]
                else None
            ),
            top_confidence=(
                {
                    "ticker": top_row[0],
                    "confidence": top_row[1],
                    "signal": top_row[2],
                }
                if top_row
                else {}
            ),
        )
        return summary.model_dump()

    # ── Write operations ──────────────────────────────────────────

    def add_ticker(
        self,
        ticker: str,
        source: str = "manual",
        discovery_score: float = 0.0,
        sentiment_hint: str = "neutral",
        notes: str = "",
    ) -> dict:
        """Add a ticker to the watchlist. Reactivates if previously removed."""
        ticker = ticker.upper().strip()
        if not ticker:
            return {"error": "Empty ticker"}

        # ── Filter pipeline guard ────────────────────────────
        from app.services.symbol_filter import get_filter_pipeline

        fr = get_filter_pipeline().run(
            ticker, {"source": source, "bot_id": self.bot_id},
        )
        if not fr.passed:
            logger.info(
                "[Watchlist] Rejected %s (%s)", ticker, fr.reason,
            )
            return {
                "error": f"Rejected: {fr.reason}",
                "ticker": ticker,
            }
        ticker = fr.symbol  # use normalized form

        db = get_db()
        now = datetime.now()

        # Check if already exists
        existing = db.execute(
            "SELECT ticker, status FROM watchlist WHERE ticker = ? AND bot_id = ?",
            [ticker, self.bot_id],
        ).fetchone()

        if existing:
            if existing[1] == "active":
                logger.info("[Watchlist] %s already active", ticker)
                return {"status": "already_exists", "ticker": ticker}
            # Reactivate
            db.execute(
                """
                UPDATE watchlist
                SET status = 'active', source = ?, discovery_score = ?,
                    sentiment_hint = ?, notes = ?, updated_at = ?,
                    signal = 'PENDING', confidence = 0.0
                WHERE ticker = ? AND bot_id = ?
                """,
                [source, discovery_score, sentiment_hint, notes, now, ticker, self.bot_id],
            )
            db.commit()
            logger.info("[Watchlist] Reactivated %s", ticker)
            return {"status": "reactivated", "ticker": ticker}

        # Insert new
        db.execute(
            """
            INSERT INTO watchlist
                (ticker, source, added_at, discovery_score,
                 sentiment_hint, notes, updated_at, bot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [ticker, source, now, discovery_score, sentiment_hint, notes, now, self.bot_id],
        )
        db.commit()
        logger.info("[Watchlist] Added %s (source=%s)", ticker, source)
        return {"status": "added", "ticker": ticker}

    def remove_ticker(self, ticker: str) -> dict:
        """Set a ticker's status to 'removed'."""
        ticker = ticker.upper().strip()
        db = get_db()
        now = datetime.now()

        existing = db.execute(
            "SELECT ticker FROM watchlist WHERE ticker = ? AND bot_id = ?",
            [ticker, self.bot_id],
        ).fetchone()

        if not existing:
            return {"error": "not_found", "ticker": ticker}

        db.execute(
            "UPDATE watchlist SET status = 'removed', updated_at = ? "
            "WHERE ticker = ? AND bot_id = ?",
            [now, ticker, self.bot_id],
        )
        db.commit()
        logger.info("[Watchlist] Removed %s", ticker)
        return {"status": "removed", "ticker": ticker}

    def mark_collected(self, ticker: str) -> None:
        """Stamp last_collected = NOW() after successful data collection."""
        db = get_db()
        now = datetime.now()
        db.execute(
            "UPDATE watchlist SET last_collected = ?, updated_at = ? "
            "WHERE ticker = ? AND bot_id = ?",
            [now, now, ticker.upper().strip(), self.bot_id],
        )
        db.commit()

    def import_from_discovery(
        self,
        min_score: float = 3.0,
        max_tickers: int = 10,
    ) -> dict:
        """Pull top-scoring tickers from discovery into the watchlist.

        NOTE: This is the legacy threshold-based import. For LLM-powered
        evaluation, use llm_import_evaluation() instead.
        """
        db = get_db()

        rows = db.execute(
            """
            SELECT ticker, total_score, sentiment_hint
            FROM ticker_scores
            WHERE is_validated = TRUE
              AND total_score >= ?
              AND ticker NOT IN (
                  SELECT ticker FROM watchlist
                  WHERE status = 'active' AND bot_id = ?
              )
            ORDER BY total_score DESC
            LIMIT ?
            """,
            [min_score, self.bot_id, max_tickers],
        ).fetchall()

        logger.info(
            "[Watchlist] import_from_discovery: found %d candidates "
            "(min_score=%.1f, excluding active watchlist for bot=%s)",
            len(rows), min_score, self.bot_id,
        )

        imported = []
        skipped = []

        for row in rows:
            ticker, score, sentiment = row[0], row[1], row[2]
            result = self.add_ticker(
                ticker=ticker,
                source="discovery",
                discovery_score=score,
                sentiment_hint=sentiment or "neutral",
            )
            if result.get("status") == "added":
                imported.append(ticker)
            else:
                skipped.append(ticker)

        logger.info(
            "[Watchlist] Imported %d tickers from discovery (skipped %d)",
            len(imported),
            len(skipped),
        )
        return {
            "imported": imported,
            "skipped": skipped,
            "total_imported": len(imported),
        }

    async def llm_import_evaluation(
        self,
        min_score: float = 2.0,
        max_candidates: int = 10,
    ) -> dict:
        """LLM-powered import: evaluate discovered tickers using collected data.

        Instead of a simple score threshold, this method:
        1. Gets candidate tickers from discovery (score >= min_score)
        2. Builds a data summary from DB (fundamentals, technicals, risk, analyst)
        3. Sends ALL candidates to the LLM in one call
        4. LLM decides which tickers deserve watchlist spots

        Returns dict with imported/rejected lists and LLM rationale.
        """
        import json as _json
        from app.services.llm_service import LLMService

        db = get_db()

        # ── 1. Get candidate tickers ──
        rows = db.execute(
            """
            SELECT ticker, total_score, sentiment_hint, mention_count,
                   youtube_score, reddit_score
            FROM ticker_scores
            WHERE is_validated = TRUE
              AND total_score >= ?
              AND ticker NOT IN (
                  SELECT ticker FROM watchlist
                  WHERE status = 'active' AND bot_id = ?
              )
            ORDER BY total_score DESC
            LIMIT ?
            """,
            [min_score, self.bot_id, max_candidates],
        ).fetchall()

        if not rows:
            logger.info(
                "[Watchlist] LLM import: no candidates (min_score=%.1f)",
                min_score,
            )
            return {
                "imported": [],
                "rejected": [],
                "total_imported": 0,
                "llm_used": False,
            }

        candidates = []
        for row in rows:
            ticker = row[0]
            candidate = {
                "ticker": ticker,
                "discovery_score": row[1],
                "sentiment_hint": row[2],
                "mention_count": row[3],
                "youtube_score": row[4],
                "reddit_score": row[5],
            }

            # ── 2. Build data summary from DB ──
            # Fundamentals
            try:
                fund_row = db.execute(
                    """
                    SELECT market_cap, trailing_pe, forward_pe, peg_ratio,
                           profit_margin, operating_margin, revenue_growth,
                           return_on_equity, debt_to_equity, free_cash_flow,
                           sector, industry, dividend_yield
                    FROM fundamentals
                    WHERE ticker = ?
                    ORDER BY snapshot_date DESC LIMIT 1
                    """,
                    [ticker],
                ).fetchone()
                if fund_row:
                    candidate["fundamentals"] = {
                        "market_cap": fund_row[0],
                        "trailing_pe": fund_row[1],
                        "forward_pe": fund_row[2],
                        "peg_ratio": fund_row[3],
                        "profit_margin": fund_row[4],
                        "operating_margin": fund_row[5],
                        "revenue_growth": fund_row[6],
                        "return_on_equity": fund_row[7],
                        "debt_to_equity": fund_row[8],
                        "free_cash_flow": fund_row[9],
                        "sector": fund_row[10],
                        "industry": fund_row[11],
                        "dividend_yield": fund_row[12],
                    }
            except Exception:
                pass

            # Latest technicals
            try:
                tech_row = db.execute(
                    """
                    SELECT rsi, macd, macd_signal, sma_20, sma_50, sma_200,
                           bb_upper, bb_lower, adx, stoch_k
                    FROM technicals
                    WHERE ticker = ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    [ticker],
                ).fetchone()
                if tech_row:
                    candidate["technicals"] = {
                        "rsi": tech_row[0],
                        "macd": tech_row[1],
                        "macd_signal": tech_row[2],
                        "sma_20": tech_row[3],
                        "sma_50": tech_row[4],
                        "sma_200": tech_row[5],
                        "bb_upper": tech_row[6],
                        "bb_lower": tech_row[7],
                        "adx": tech_row[8],
                        "stoch_k": tech_row[9],
                    }
            except Exception:
                pass

            # Risk metrics
            try:
                risk_row = db.execute(
                    """
                    SELECT sharpe_ratio, sortino_ratio, max_drawdown,
                           var_95, daily_volatility, beta, alpha
                    FROM risk_metrics
                    WHERE ticker = ?
                    ORDER BY computed_date DESC LIMIT 1
                    """,
                    [ticker],
                ).fetchone()
                if risk_row:
                    candidate["risk"] = {
                        "sharpe_ratio": risk_row[0],
                        "sortino_ratio": risk_row[1],
                        "max_drawdown": risk_row[2],
                        "var_95": risk_row[3],
                        "daily_volatility": risk_row[4],
                        "beta": risk_row[5],
                        "alpha": risk_row[6],
                    }
            except Exception:
                pass

            # Analyst consensus
            try:
                analyst_row = db.execute(
                    """
                    SELECT target_mean, target_median, num_analysts,
                           strong_buy, buy, hold, sell, strong_sell
                    FROM analyst_data
                    WHERE ticker = ?
                    ORDER BY snapshot_date DESC LIMIT 1
                    """,
                    [ticker],
                ).fetchone()
                if analyst_row:
                    candidate["analyst"] = {
                        "target_mean": analyst_row[0],
                        "target_median": analyst_row[1],
                        "num_analysts": analyst_row[2],
                        "strong_buy": analyst_row[3],
                        "buy": analyst_row[4],
                        "hold": analyst_row[5],
                        "sell": analyst_row[6],
                        "strong_sell": analyst_row[7],
                    }
            except Exception:
                pass

            # Latest price
            try:
                price_row = db.execute(
                    """
                    SELECT close, volume
                    FROM price_history
                    WHERE ticker = ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    [ticker],
                ).fetchone()
                if price_row:
                    candidate["latest_price"] = price_row[0]
                    candidate["latest_volume"] = price_row[1]
            except Exception:
                pass

            candidates.append(candidate)

        logger.info(
            "[Watchlist] LLM import: evaluating %d candidates: %s",
            len(candidates),
            [c["ticker"] for c in candidates],
        )

        # ── 3. Send to LLM for evaluation ──
        llm = LLMService()

        # Build the prompt with all candidate data
        candidates_json = _json.dumps(candidates, indent=2, default=str)

        system_prompt = (
            "You are a stock screening analyst for a trading bot. "
            "Your job is to evaluate candidate stocks and decide which ones "
            "deserve to be added to the active watchlist for further analysis and trading.\n\n"
            "You will receive a list of candidate tickers with their financial data:\n"
            "- Discovery data (social media mentions, sentiment)\n"
            "- Fundamentals (P/E, margins, growth, debt)\n"
            "- Technicals (RSI, MACD, moving averages)\n"
            "- Risk metrics (Sharpe, drawdown, VaR)\n"
            "- Analyst consensus (price targets, recommendations)\n\n"
            "SELECTION CRITERIA:\n"
            "1. Reasonable valuation — not extreme P/E unless justified by high growth\n"
            "2. Positive or improving technical trend — RSI not overbought, MACD positive or crossing\n"
            "3. Acceptable risk profile — positive Sharpe, reasonable drawdown\n"
            "4. Analyst sentiment aligns — more buys than sells\n"
            "5. Not just social media hype — needs fundamental backing\n"
            "6. If data is missing for a ticker, note it but don't auto-reject — "
            "some newly discovered tickers may not have full data yet\n\n"
            "Be selective but reasonable. Aim to promote 2-5 tickers per evaluation.\n"
            "If NO tickers meet the criteria, return an empty selections array.\n\n"
            "Respond with valid JSON ONLY."
        )

        user_prompt = (
            f"Evaluate these {len(candidates)} candidate tickers for watchlist promotion:\n\n"
            f"{candidates_json}\n\n"
            "Return JSON with this exact structure:\n"
            "{\n"
            '  "selections": [\n'
            '    {"ticker": "SYMBOL", "rationale": "Brief reason for selection", "score": 8.5}\n'
            "  ],\n"
            '  "rejections": [\n'
            '    {"ticker": "SYMBOL", "reason": "Brief reason for rejection"}\n'
            "  ]\n"
            "}\n\n"
            "The score should be 1-10 reflecting overall investment quality."
        )

        try:
            response = await llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format="json",
                audit_step="import_evaluation",
            )

            # Parse LLM response
            cleaned = LLMService.clean_json_response(response)
            result_data = _json.loads(cleaned)

            selections = result_data.get("selections", [])
            rejections = result_data.get("rejections", [])

            logger.info(
                "[Watchlist] LLM import evaluation result: %d selected, %d rejected",
                len(selections), len(rejections),
            )

        except Exception as exc:
            logger.error(
                "[Watchlist] LLM import evaluation failed: %s — "
                "falling back to threshold-based import",
                exc,
            )
            # Fallback to simple threshold
            return self.import_from_discovery(
                min_score=min_score, max_tickers=max_candidates,
            )

        # ── 4. Import selected tickers ──
        imported = []
        skipped = []

        for sel in selections:
            ticker = sel.get("ticker", "").upper().strip()
            if not ticker:
                continue

            score = sel.get("score", 5.0)
            rationale = sel.get("rationale", "LLM selected")

            add_result = self.add_ticker(
                ticker=ticker,
                source="llm_import",
                discovery_score=score,
                sentiment_hint="positive",
                notes=f"LLM: {rationale[:200]}",
            )
            if add_result.get("status") in ("added", "reactivated"):
                imported.append(ticker)
                logger.info(
                    "[Watchlist] LLM imported %s (score=%.1f): %s",
                    ticker, score, rationale[:100],
                )
            else:
                skipped.append(ticker)

        # Log rejections
        for rej in rejections:
            ticker = rej.get("ticker", "")
            reason = rej.get("reason", "no reason given")
            logger.info(
                "[Watchlist] LLM rejected %s: %s", ticker, reason[:100],
            )

        logger.info(
            "[Watchlist] LLM import complete: %d imported, %d skipped, %d rejected",
            len(imported), len(skipped), len(rejections),
        )

        return {
            "imported": imported,
            "skipped": skipped,
            "rejected": [
                {"ticker": r.get("ticker"), "reason": r.get("reason")}
                for r in rejections
            ],
            "total_imported": len(imported),
            "llm_used": True,
            "selections_detail": selections,
        }

    def clear(self) -> dict:
        """Remove all entries from the watchlist."""
        db = get_db()
        db.execute("DELETE FROM watchlist WHERE bot_id = ?", [self.bot_id])
        db.commit()
        logger.info("[Watchlist] Cleared all data")
        return {"status": "cleared"}

    # ── Analysis operations ───────────────────────────────────────

    async def analyze_ticker(self, ticker: str) -> dict:
        """Run the full analysis pipeline on a single watchlist ticker.

        Updates the watchlist row with the resulting signal and confidence.
        """
        ticker = ticker.upper().strip()
        db = get_db()
        now = datetime.now()

        logger.info("[Watchlist] Starting analysis for %s", ticker)
        t0 = time.perf_counter()

        try:
            result = await self.pipeline.run(ticker, mode="full")

            signal = "HOLD"
            confidence = 0.0

            if result.decision:
                signal = result.decision.signal or "HOLD"
                confidence = getattr(result.decision, "confidence", 0.0)

            elapsed = time.perf_counter() - t0

            # Update watchlist row
            db.execute(
                """
                UPDATE watchlist
                SET signal = ?, confidence = ?, last_analyzed = ?,
                    analysis_count = analysis_count + 1, updated_at = ?
                WHERE ticker = ? AND bot_id = ?
                """,
                [signal, confidence, now, now, ticker, self.bot_id],
            )

            logger.info(
                "[Watchlist] Analysis complete for %s: %s (%.0f%%) in %.1fs",
                ticker,
                signal,
                confidence * 100,
                elapsed,
            )

            return {
                "ticker": ticker,
                "signal": signal,
                "confidence": confidence,
                "elapsed_s": round(elapsed, 2),
                "errors": result.errors,
            }

        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(
                "[Watchlist] Analysis failed for %s after %.1fs: %s",
                ticker,
                elapsed,
                e,
            )
            return {
                "ticker": ticker,
                "signal": "ERROR",
                "confidence": 0.0,
                "elapsed_s": round(elapsed, 2),
                "errors": [str(e)],
            }

    async def analyze_all(self, batch_size: int = 2) -> dict:
        """Analyze all active watchlist tickers in parallel batches.

        With OLLAMA_NUM_PARALLEL=10 and each ticker using 4 agent slots,
        batch_size=2 uses 8 slots, leaving 2 for other requests.

        Args:
            batch_size: Number of tickers to analyze concurrently.

        Returns:
            Dict with results per ticker and timing info.
        """
        tickers = [
            entry["ticker"]
            for entry in self.get_watchlist()
            if entry["status"] == "active"
        ]

        if not tickers:
            return {"results": [], "total_time_s": 0, "message": "No active tickers"}

        logger.info(
            "[Watchlist] Analyzing %d tickers in batches of %d",
            len(tickers),
            batch_size,
        )

        all_results: list[dict] = []
        t0 = time.perf_counter()

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            logger.info(
                "[Watchlist] Batch %d/%d: %s",
                (i // batch_size) + 1,
                (len(tickers) + batch_size - 1) // batch_size,
                batch,
            )

            batch_results = await asyncio.gather(
                *[self.analyze_ticker(t) for t in batch],
                return_exceptions=True,
            )

            for result in batch_results:
                if isinstance(result, Exception):
                    all_results.append(
                        {"ticker": "UNKNOWN", "error": str(result)}
                    )
                else:
                    all_results.append(result)

        total_time = time.perf_counter() - t0
        logger.info(
            "[Watchlist] All %d tickers analyzed in %.1fs",
            len(tickers),
            total_time,
        )

        return {
            "results": all_results,
            "total_tickers": len(tickers),
            "total_time_s": round(total_time, 2),
            "batch_size": batch_size,
        }

    # ── Private helpers ───────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        """Convert a DuckDB row tuple to a dict."""
        return {
            "ticker": row[0],
            "source": row[1],
            "added_at": str(row[2]) if row[2] else None,
            "last_analyzed": str(row[3]) if row[3] else None,
            "analysis_count": row[4],
            "signal": row[5],
            "confidence": row[6],
            "discovery_score": row[7],
            "sentiment_hint": row[8],
            "status": row[9],
            "cooldown_until": str(row[10]) if row[10] else None,
            "notes": row[11],
            "updated_at": str(row[12]) if row[12] else None,
        }


