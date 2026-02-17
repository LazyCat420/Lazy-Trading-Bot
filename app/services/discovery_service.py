"""Discovery Service — orchestrates Reddit + YouTube ticker discovery.

Runs both collectors, merges scores, persists to DuckDB.
"""

from __future__ import annotations

import time
from datetime import datetime

from app.collectors.reddit_collector import RedditCollector
from app.collectors.ticker_scanner import TickerScanner
from app.collectors.youtube_collector import YouTubeCollector
from app.database import get_db
from app.models.discovery import DiscoveryResult, ScoredTicker
from app.utils.logger import logger


class DiscoveryService:
    """Orchestrates ticker discovery from all sources."""

    def __init__(self) -> None:
        self.reddit = RedditCollector()
        self.youtube = TickerScanner()
        self.yt_collector = YouTubeCollector()
        self._running: bool = False
        self._last_run_at: datetime | None = None

    async def run_discovery(
        self,
        *,
        enable_reddit: bool = True,
        enable_youtube: bool = True,
        youtube_hours: int = 24,
    ) -> DiscoveryResult:
        """Run full discovery pipeline and return merged results."""
        self._running = True
        start = time.time()
        logger.info("=" * 70)
        logger.info("[Discovery] Starting full discovery run")
        logger.info("=" * 70)

        reddit_tickers: list[ScoredTicker] = []
        youtube_tickers: list[ScoredTicker] = []

        # Reddit collection
        if enable_reddit:
            try:
                reddit_tickers = await self.reddit.collect()
                logger.info(
                    "[Discovery] Reddit returned %d tickers", len(reddit_tickers)
                )
            except Exception as e:
                logger.error("[Discovery] Reddit collection failed: %s", e)

        # YouTube collection
        if enable_youtube:
            try:
                youtube_tickers = self.youtube.scan_recent_transcripts(
                    hours=youtube_hours
                )
                logger.info(
                    "[Discovery] YouTube returned %d tickers", len(youtube_tickers)
                )
            except Exception as e:
                logger.error("[Discovery] YouTube collection failed: %s", e)

        # Merge scores from both sources
        merged = self._merge_scores(reddit_tickers, youtube_tickers)

        # Persist to DuckDB
        self._save_to_db(merged)

        # NEW: Scrape one YouTube transcript per discovered ticker
        transcript_count = await self._collect_transcripts(merged)

        elapsed = time.time() - start
        result = DiscoveryResult(
            tickers=merged,
            reddit_count=len(reddit_tickers),
            youtube_count=len(youtube_tickers),
            transcript_count=transcript_count,
            run_at=datetime.now(),
            duration_seconds=elapsed,
        )

        logger.info("=" * 70)
        logger.info(
            "[Discovery] Complete: %d unique tickers in %.1fs",
            len(merged), elapsed,
        )
        logger.info(
            "[Discovery]   Reddit: %d, YouTube: %d, Transcripts scraped: %d",
            result.reddit_count, result.youtube_count, transcript_count,
        )
        for t in merged[:10]:
            logger.info(
                "[Discovery]   $%s: %.1f pts (source: %s)",
                t.ticker, t.discovery_score, t.source,
            )
        logger.info("=" * 70)

        self._running = False
        self._last_run_at = datetime.now()
        return result

    def status(self) -> dict:
        """Return bot vitals: running state, last run, aggregate stats."""
        db = get_db()

        # Total unique tickers
        total_row = db.execute(
            "SELECT COUNT(*) FROM ticker_scores"
        ).fetchone()
        total_discovered = total_row[0] if total_row else 0

        # Source counts from discovered_tickers
        reddit_row = db.execute(
            "SELECT COUNT(DISTINCT ticker) FROM discovered_tickers WHERE source = 'reddit'"
        ).fetchone()
        youtube_row = db.execute(
            "SELECT COUNT(DISTINCT ticker) FROM discovered_tickers WHERE source = 'youtube'"
        ).fetchone()

        # Top ticker
        top_row = db.execute(
            "SELECT ticker, total_score FROM ticker_scores ORDER BY total_score DESC LIMIT 1"
        ).fetchone()

        # Last discovered_at
        last_row = db.execute(
            "SELECT MAX(discovered_at) FROM discovered_tickers"
        ).fetchone()

        return {
            "is_running": self._running,
            "last_run_at": (
                self._last_run_at.isoformat() if self._last_run_at
                else (str(last_row[0]) if last_row and last_row[0] else None)
            ),
            "total_discovered": total_discovered,
            "reddit_total": reddit_row[0] if reddit_row else 0,
            "youtube_total": youtube_row[0] if youtube_row else 0,
            "top_ticker": {
                "ticker": top_row[0],
                "score": top_row[1],
            } if top_row else None,
        }

    def get_latest_scores(self, limit: int = 20) -> list[dict]:
        """Get latest aggregated scores from DuckDB."""
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, total_score, youtube_score, reddit_score,
                   mention_count, first_seen, last_seen, sentiment_hint
            FROM ticker_scores
            ORDER BY total_score DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()

        return [
            {
                "ticker": r[0],
                "total_score": r[1],
                "youtube_score": r[2],
                "reddit_score": r[3],
                "mention_count": r[4],
                "first_seen": str(r[5]) if r[5] else None,
                "last_seen": str(r[6]) if r[6] else None,
                "sentiment_hint": r[7],
            }
            for r in rows
        ]

    def get_discovery_history(self, limit: int = 50) -> list[dict]:
        """Get raw discovery history from DuckDB."""
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, source, source_detail, discovery_score,
                   sentiment_hint, context_snippet, discovered_at
            FROM discovered_tickers
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()

        return [
            {
                "ticker": r[0],
                "source": r[1],
                "source_detail": r[2],
                "discovery_score": r[3],
                "sentiment_hint": r[4],
                "context_snippet": r[5],
                "discovered_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    # ── Private: transcript collection ───────────────────────────────

    async def _collect_transcripts(
        self, tickers: list[ScoredTicker],
    ) -> int:
        """Search YouTube and grab one transcript per discovered ticker.

        Uses YouTubeCollector in discovery_mode (no daily guard, no 24h filter)
        so we pick up ANY related video, not just today's.

        Returns the total number of transcripts successfully collected.
        """
        if not tickers:
            return 0

        logger.info(
            "[Discovery] Collecting YouTube transcripts for %d tickers...",
            len(tickers),
        )

        total_collected = 0
        for scored in tickers:
            ticker = scored.ticker
            try:
                transcripts = await self.yt_collector.collect(
                    ticker, max_videos=1, discovery_mode=True,
                )
                count = len(transcripts)
                total_collected += count
                if count:
                    logger.info(
                        "[Discovery]   $%s: collected %d transcript(s) — '%s'",
                        ticker, count,
                        transcripts[0].title[:60] if transcripts else "",
                    )
                else:
                    logger.info(
                        "[Discovery]   $%s: no transcript found", ticker,
                    )
            except Exception as e:
                logger.error(
                    "[Discovery]   $%s: transcript collection failed: %s",
                    ticker, e,
                )

        logger.info(
            "[Discovery] Transcript collection done: %d/%d tickers got transcripts",
            total_collected, len(tickers),
        )
        return total_collected

    # ── Private: merge + persist ────────────────────────────────────

    def _merge_scores(
        self,
        reddit: list[ScoredTicker],
        youtube: list[ScoredTicker],
    ) -> list[ScoredTicker]:
        """Merge scores from Reddit + YouTube. Same ticker gets combined score."""
        combined: dict[str, ScoredTicker] = {}

        for t in reddit + youtube:
            if t.ticker in combined:
                existing = combined[t.ticker]
                combined[t.ticker] = ScoredTicker(
                    ticker=t.ticker,
                    discovery_score=existing.discovery_score + t.discovery_score,
                    source="reddit+youtube" if existing.source != t.source else t.source,
                    source_detail=f"{existing.source_detail}, {t.source_detail}",
                    sentiment_hint=t.sentiment_hint,
                    context_snippets=existing.context_snippets + t.context_snippets,
                    first_seen=min(existing.first_seen, t.first_seen),
                    last_seen=max(existing.last_seen, t.last_seen),
                )
            else:
                combined[t.ticker] = t

        # Sort by total score descending
        result = sorted(combined.values(), key=lambda x: x.discovery_score, reverse=True)
        return result

    def _save_to_db(self, tickers: list[ScoredTicker]) -> None:
        """Persist discovery results to DuckDB."""
        if not tickers:
            return

        db = get_db()
        now = datetime.now()

        for t in tickers:
            # Save raw discovery record
            db.execute(
                """
                INSERT INTO discovered_tickers
                    (ticker, source, source_detail, discovery_score,
                     sentiment_hint, context_snippet, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    t.ticker,
                    t.source,
                    t.source_detail,
                    t.discovery_score,
                    t.sentiment_hint,
                    t.context_snippets[0] if t.context_snippets else "",
                    now,
                ],
            )

            # Upsert aggregated score
            reddit_score = t.discovery_score if "reddit" in t.source else 0.0
            youtube_score = t.discovery_score if "youtube" in t.source else 0.0

            existing = db.execute(
                "SELECT total_score, mention_count, first_seen FROM ticker_scores WHERE ticker = ?",
                [t.ticker],
            ).fetchone()

            if existing:
                db.execute(
                    """
                    UPDATE ticker_scores
                    SET total_score = total_score + ?,
                        reddit_score = reddit_score + ?,
                        youtube_score = youtube_score + ?,
                        mention_count = mention_count + 1,
                        last_seen = ?,
                        sentiment_hint = ?,
                        is_validated = TRUE,
                        updated_at = ?
                    WHERE ticker = ?
                    """,
                    [
                        t.discovery_score, reddit_score, youtube_score,
                        now, t.sentiment_hint, now, t.ticker,
                    ],
                )
            else:
                db.execute(
                    """
                    INSERT INTO ticker_scores
                        (ticker, total_score, youtube_score, reddit_score,
                         mention_count, first_seen, last_seen,
                         sentiment_hint, is_validated, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, TRUE, ?)
                    """,
                    [
                        t.ticker, t.discovery_score, youtube_score, reddit_score,
                        now, now, t.sentiment_hint, now,
                    ],
                )

        logger.info("[Discovery] Saved %d tickers to DuckDB", len(tickers))
