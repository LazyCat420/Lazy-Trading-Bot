"""WatchlistManager — bridges Discovery (Phase 1) to Analysis (PipelineService).

Reads top-scoring discovered tickers, adds them to the watchlist,
runs the full analysis pipeline on each, and stores the signal+confidence.

Usage (from main.py):
    wm = WatchlistManager()
    await wm.import_from_discovery(min_score=5.0, max_tickers=10)
    await wm.analyze_all(batch_size=2)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from app.database import get_db
from app.models.watchlist import WatchlistSummary
from app.services.pipeline_service import PipelineService
from app.utils.logger import logger


class WatchlistManager:
    """Manages the watchlist — adding, removing, and analyzing tickers."""

    def __init__(self) -> None:
        self.pipeline = PipelineService()

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
                ORDER BY confidence DESC, added_at DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT ticker, source, added_at, last_analyzed, analysis_count,
                       signal, confidence, discovery_score, sentiment_hint,
                       status, cooldown_until, notes, updated_at
                FROM watchlist
                WHERE status = 'active'
                ORDER BY confidence DESC, added_at DESC
                """
            ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def get_active_tickers(self) -> list[str]:
        """Return just the active ticker symbols as strings."""
        db = get_db()
        rows = db.execute(
            "SELECT ticker FROM watchlist WHERE status = 'active' "
            "ORDER BY confidence DESC, added_at DESC"
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_summary(self) -> dict:
        """Return aggregate stats for the frontend header."""
        db = get_db()

        total_row = db.execute(
            "SELECT COUNT(*) FROM watchlist WHERE status = 'active'"
        ).fetchone()
        total = total_row[0] if total_row else 0

        signal_rows = db.execute(
            """
            SELECT signal, COUNT(*) as cnt
            FROM watchlist
            WHERE status = 'active'
            GROUP BY signal
            """
        ).fetchall()

        signal_counts: dict[str, int] = {}
        for row in signal_rows:
            signal_counts[row[0]] = row[1]

        last_row = db.execute(
            "SELECT MAX(last_analyzed) FROM watchlist WHERE status = 'active'"
        ).fetchone()

        top_row = db.execute(
            """
            SELECT ticker, confidence, signal
            FROM watchlist
            WHERE status = 'active' AND signal != 'PENDING'
            ORDER BY confidence DESC
            LIMIT 1
            """
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

        db = get_db()
        now = datetime.now()

        # Check if already exists
        existing = db.execute(
            "SELECT ticker, status FROM watchlist WHERE ticker = ?",
            [ticker],
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
                WHERE ticker = ?
                """,
                [source, discovery_score, sentiment_hint, notes, now, ticker],
            )
            db.commit()
            logger.info("[Watchlist] Reactivated %s", ticker)
            return {"status": "reactivated", "ticker": ticker}

        # Insert new
        db.execute(
            """
            INSERT INTO watchlist
                (ticker, source, added_at, discovery_score,
                 sentiment_hint, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [ticker, source, now, discovery_score, sentiment_hint, notes, now],
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
            "SELECT ticker FROM watchlist WHERE ticker = ?",
            [ticker],
        ).fetchone()

        if not existing:
            return {"error": "not_found", "ticker": ticker}

        db.execute(
            "UPDATE watchlist SET status = 'removed', updated_at = ? WHERE ticker = ?",
            [now, ticker],
        )
        db.commit()
        logger.info("[Watchlist] Removed %s", ticker)
        return {"status": "removed", "ticker": ticker}

    def import_from_discovery(
        self,
        min_score: float = 3.0,
        max_tickers: int = 10,
    ) -> dict:
        """Pull top-scoring tickers from discovery into the watchlist."""
        db = get_db()

        rows = db.execute(
            """
            SELECT ticker, total_score, sentiment_hint
            FROM ticker_scores
            WHERE is_validated = TRUE AND total_score >= ?
            ORDER BY total_score DESC
            LIMIT ?
            """,
            [min_score, max_tickers],
        ).fetchall()

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

    def clear(self) -> dict:
        """Remove all entries from the watchlist."""
        db = get_db()
        db.execute("DELETE FROM watchlist")
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
                WHERE ticker = ?
                """,
                [signal, confidence, now, now, ticker],
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
