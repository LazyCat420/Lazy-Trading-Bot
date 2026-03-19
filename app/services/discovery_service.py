"""Discovery Service — orchestrates Reddit + YouTube + SEC 13F + Congress + News ticker discovery.

Runs all collectors IN PARALLEL, merges scores, persists to DuckDB.
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import asyncio
import time
from datetime import datetime
from typing import Any

from app.database import get_db
from app.models.discovery import DiscoveryResult, ScoredTicker
from app.services.congress_service import CongressCollector
from app.services.reddit_service import RedditCollector
from app.services.rss_news_service import RSSNewsCollector
from app.services.sec_13f_service import SEC13FCollector
from app.services.ticker_scanner import TickerScanner
from app.services.youtube_service import YouTubeCollector
from app.utils.logger import logger


@track_class_telemetry
class DiscoveryService:
    """Orchestrates ticker discovery from all sources."""

    def __init__(self) -> None:
        self.reddit = RedditCollector()
        self.youtube = TickerScanner()
        self.yt_collector = YouTubeCollector()
        self.sec_13f = SEC13FCollector()
        self.congress = CongressCollector()
        self.rss_news = RSSNewsCollector()
        self._running: bool = False
        self._last_run_at: datetime | None = None

    def clear_data(self) -> dict:
        """Truncate both discovery tables so the monitor starts fresh.

        Runs DELETE on both tables, then verifies they are empty.
        Returns remaining row counts so the frontend can confirm success.
        """
        db = get_db()

        # Log pre-clear counts for diagnostics
        dt_before = db.execute("SELECT COUNT(*) FROM discovered_tickers").fetchone()[0]
        ts_before = db.execute("SELECT COUNT(*) FROM ticker_scores").fetchone()[0]
        logger.info(
            "[Discovery] clear_data called — discovered_tickers=%d, ticker_scores=%d",
            dt_before,
            ts_before,
        )

        try:
            db.execute("DELETE FROM discovered_tickers")
            db.execute("DELETE FROM ticker_scores")
        except Exception as e:
            logger.error("[Discovery] clear_data DELETE failed: %s", e)
            return {"status": "error", "error": str(e)}

        # Verify the tables are actually empty
        dt_after = db.execute("SELECT COUNT(*) FROM discovered_tickers").fetchone()[0]
        ts_after = db.execute("SELECT COUNT(*) FROM ticker_scores").fetchone()[0]
        remaining = dt_after + ts_after

        logger.info(
            "[Discovery] clear_data complete — remaining: discovered_tickers=%d, ticker_scores=%d",
            dt_after,
            ts_after,
        )

        if remaining > 0:
            logger.warning(
                "[Discovery] clear_data: %d rows still remain!",
                remaining,
            )
            return {"status": "partial", "remaining": remaining}

        return {"status": "cleared", "remaining": 0}

    async def run_discovery(
        self,
        *,
        enable_reddit: bool = True,
        enable_youtube: bool = True,
        enable_sec_13f: bool = True,
        enable_congress: bool = True,
        enable_rss_news: bool = True,
        youtube_hours: int = 24,
        max_tickers: int | None = None,
    ) -> DiscoveryResult:
        """Run full discovery pipeline and return merged results."""
        self._running = True
        start = time.time()
        logger.info("=" * 70)
        logger.info("[Discovery] Starting full discovery run (max_tickers=%s)", max_tickers)
        logger.info("=" * 70)

        # ── Temporarily override per-source fetch limits when Limit is set ──
        # The UI "Limit" field controls BOTH how many tickers to return AND
        # how many items each collector fetches per source.
        from app.config import settings as _cfg

        _saved_yt = _cfg.YOUTUBE_MAX_VIDEOS
        _saved_reddit = _cfg.REDDIT_MAX_POSTS_PER_SUB
        _saved_news = _cfg.NEWS_FETCH_LIMIT

        if max_tickers and max_tickers > 0:
            _cfg.YOUTUBE_MAX_VIDEOS = max_tickers
            _cfg.REDDIT_MAX_POSTS_PER_SUB = max_tickers
            _cfg.NEWS_FETCH_LIMIT = max_tickers
            # Also update the RedditCollector instance's cached value
            self.reddit.MAX_POSTS_PER_SUB = max_tickers
            logger.info(
                "[Discovery] Per-source limits overridden: yt=%d, reddit=%d, news=%d",
                max_tickers, max_tickers, max_tickers,
            )

        reddit_tickers: list[ScoredTicker] = []
        youtube_tickers: list[ScoredTicker] = []
        sec_13f_tickers: list[ScoredTicker] = []
        congress_tickers: list[ScoredTicker] = []
        rss_news_tickers: list[ScoredTicker] = []

        # ── Per-collector timeout (5 min) to prevent any single collector
        #    from blocking the entire discovery phase.
        COLLECTOR_TIMEOUT_SECS = 300  # 5 minutes

        async def _timed_collect(
            name: str,
            coro: Any,
        ) -> list[ScoredTicker]:
            """Run a collector with timeout and timing."""
            t0 = time.time()
            try:
                result = await asyncio.wait_for(coro, timeout=COLLECTOR_TIMEOUT_SECS)
                elapsed = time.time() - t0
                logger.info(
                    "[Discovery] %s returned %d tickers in %.1fs",
                    name,
                    len(result),
                    elapsed,
                )
                return result
            except TimeoutError:
                elapsed = time.time() - t0
                logger.error(
                    "[Discovery] %s TIMED OUT after %.1fs — skipping",
                    name,
                    elapsed,
                )
                return []
            except Exception as e:
                elapsed = time.time() - t0
                logger.error(
                    "[Discovery] %s failed after %.1fs: %s",
                    name,
                    elapsed,
                    e,
                )
                return []

        # ── Run ALL enabled collectors IN PARALLEL ────────────────
        async def _collect_reddit() -> list[ScoredTicker]:
            if not enable_reddit:
                return []
            return await self.reddit.collect()

        async def _collect_youtube() -> list[ScoredTicker]:
            if not enable_youtube:
                return []
            try:
                market_transcripts = await self.yt_collector.collect_general_market()
                logger.info(
                    "[Discovery] General market: %d new transcripts scraped",
                    len(market_transcripts),
                )
            except Exception as e:
                logger.error("[Discovery] General market scrape failed: %s", e)
            result = await self.youtube.scan_recent_transcripts(
                hours=youtube_hours,
            )
            return result

        async def _collect_sec_13f() -> list[ScoredTicker]:
            if not enable_sec_13f:
                return []
            return await self.sec_13f.collect_recent_holdings()

        async def _collect_congress() -> list[ScoredTicker]:
            if not enable_congress:
                return []
            return await self.congress.collect_recent_trades()

        async def _collect_rss_news() -> list[ScoredTicker]:
            if not enable_rss_news:
                return []
            await self.rss_news.scrape_all_feeds()
            return await self.rss_news.get_discovery_tickers()

        logger.info("[Discovery] Running all collectors in PARALLEL …")
        (
            reddit_tickers,
            youtube_tickers,
            sec_13f_tickers,
            congress_tickers,
            rss_news_tickers,
        ) = await asyncio.gather(
            _timed_collect("Reddit", _collect_reddit()),
            _timed_collect("YouTube", _collect_youtube()),
            _timed_collect("SEC 13F", _collect_sec_13f()),
            _timed_collect("Congress", _collect_congress()),
            _timed_collect("RSS News", _collect_rss_news()),
        )

        # ── Restore original per-source limits ──
        _cfg.YOUTUBE_MAX_VIDEOS = _saved_yt
        _cfg.REDDIT_MAX_POSTS_PER_SUB = _saved_reddit
        _cfg.NEWS_FETCH_LIMIT = _saved_news
        self.reddit.MAX_POSTS_PER_SUB = _saved_reddit

        # Merge scores from ALL sources
        merged = self._merge_scores(
            reddit_tickers,
            youtube_tickers,
            sec_13f_tickers,
            congress_tickers,
            rss_news_tickers,
        )

        # ── Exclude tickers already on the active watchlist ──────
        # This ensures the top N are always NEW stocks, not the same
        # mega-caps (AAPL, MSFT, NVDA) that dominate every data source.
        try:
            from app.services.watchlist_manager import WatchlistManager

            wm = WatchlistManager()
            active_tickers = set(wm.get_active_tickers())
            if active_tickers:
                before_count = len(merged)
                merged = [t for t in merged if t.ticker not in active_tickers]
                excluded = before_count - len(merged)
                if excluded:
                    logger.info(
                        "[Discovery] Excluded %d watchlist tickers (%s), %d remain",
                        excluded,
                        ", ".join(sorted(active_tickers)),
                        len(merged),
                    )
        except Exception as exc:
            logger.warning("[Discovery] Watchlist exclusion failed: %s", exc)

        # Cap results if max_tickers is set (for faster debugging)
        if max_tickers and len(merged) > max_tickers:
            logger.info("[Discovery] Capping results from %d to %d", len(merged), max_tickers)
            merged = merged[:max_tickers]

        # Persist to DuckDB
        self._save_to_db(merged)

        # NEW: Scrape one YouTube transcript per discovered ticker
        transcript_count = await self._collect_transcripts(merged)

        elapsed = time.time() - start
        result = DiscoveryResult(
            tickers=merged,
            reddit_count=len(reddit_tickers),
            youtube_count=len(youtube_tickers),
            sec_13f_count=len(sec_13f_tickers),
            congress_count=len(congress_tickers),
            rss_news_count=len(rss_news_tickers),
            transcript_count=transcript_count,
            run_at=datetime.now(),
            duration_seconds=elapsed,
        )

        logger.info("=" * 70)
        logger.info(
            "[Discovery] Complete: %d unique tickers in %.1fs",
            len(merged),
            elapsed,
        )
        logger.info(
            "[Discovery]   Reddit: %d, YouTube: %d, SEC 13F: %d, "
            "Congress: %d, RSS News: %d, Transcripts: %d",
            result.reddit_count,
            result.youtube_count,
            result.sec_13f_count,
            result.congress_count,
            result.rss_news_count,
            transcript_count,
        )
        for t in merged[:10]:
            logger.info(
                "[Discovery]   $%s: %.1f pts (source: %s)",
                t.ticker,
                t.discovery_score,
                t.source,
            )
        logger.info("=" * 70)

        self._running = False
        self._last_run_at = datetime.now()
        return result

    def status(self) -> dict:
        """Return bot vitals: running state, last run, aggregate stats."""
        db = get_db()

        # Total unique tickers
        total_row = db.execute("SELECT COUNT(*) FROM ticker_scores").fetchone()
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
        last_row = db.execute("SELECT MAX(discovered_at) FROM discovered_tickers").fetchone()

        return {
            "is_running": self._running,
            "last_run_at": (
                self._last_run_at.isoformat()
                if self._last_run_at
                else (str(last_row[0]) if last_row and last_row[0] else None)
            ),
            "total_discovered": total_discovered,
            "reddit_total": reddit_row[0] if reddit_row else 0,
            "youtube_total": youtube_row[0] if youtube_row else 0,
            "top_ticker": {
                "ticker": top_row[0],
                "score": top_row[1],
            }
            if top_row
            else None,
        }

    def get_latest_scores(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Get latest aggregated scores from DuckDB with pagination.

        Returns:
            dict with "scores" list, "total" count, "limit", and "offset".
        """
        db = get_db()

        # Total count (for pagination controls)
        total_row = db.execute("SELECT COUNT(*) FROM ticker_scores").fetchone()
        total = total_row[0] if total_row else 0

        rows = db.execute(
            """
            SELECT ticker, total_score, youtube_score, reddit_score,
                   mention_count, first_seen, last_seen, sentiment_hint
            FROM ticker_scores
            ORDER BY total_score DESC
            LIMIT ? OFFSET ?
            """,
            [limit, offset],
        ).fetchall()

        scores = [
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
        return {
            "scores": scores,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_discovery_history(self, limit: int = 50) -> list[dict]:
        """Get raw discovery history from DuckDB."""
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, source, source_detail, discovery_score,
                   sentiment_hint, context_snippet, discovered_at,
                   source_url
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
                "source_url": r[7] if r[7] else "",
            }
            for r in rows
        ]

    # ── Private: transcript collection ───────────────────────────────

    async def _collect_transcripts(
        self,
        tickers: list[ScoredTicker],
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

        sem = asyncio.Semaphore(3)  # Limit concurrent YouTube lookups

        async def _fetch_one(scored: ScoredTicker) -> int:
            async with sem:
                tk = scored.ticker
                try:
                    transcripts = await self.yt_collector.collect(
                        tk,
                        max_videos=1,
                        discovery_mode=True,
                    )
                    count = len(transcripts)
                    if count:
                        logger.info(
                            "[Discovery]   $%s: collected %d transcript(s) — '%s'",
                            tk,
                            count,
                            transcripts[0].title[:60] if transcripts else "",
                        )
                    else:
                        logger.info(
                            "[Discovery]   $%s: no transcript found",
                            tk,
                        )
                    return count
                except Exception as e:
                    logger.error(
                        "[Discovery]   $%s: transcript collection failed: %s",
                        tk,
                        e,
                    )
                    return 0

        results = await asyncio.gather(*[_fetch_one(s) for s in tickers])
        total_collected = sum(results)

        logger.info(
            "[Discovery] Transcript collection done: %d/%d tickers got transcripts",
            total_collected,
            len(tickers),
        )
        return total_collected

    # ── Private: merge + persist ────────────────────────────────────

    def _merge_scores(
        self,
        *ticker_lists: list[ScoredTicker],
    ) -> list[ScoredTicker]:
        """Merge scores from all sources. Same ticker gets combined score."""
        combined: dict[str, ScoredTicker] = {}

        all_tickers: list[ScoredTicker] = []
        for lst in ticker_lists:
            all_tickers.extend(lst)

        for t in all_tickers:
            if t.ticker in combined:
                existing = combined[t.ticker]
                # Combine sources into a descriptive label
                sources = {existing.source, t.source}
                if len(sources) > 2 or "multi" in sources:
                    merged_source = "multi"
                else:
                    merged_source = "+".join(sorted(sources))  # type: ignore[arg-type]
                    # Ensure it's a valid Literal value
                    valid = {
                        "youtube",
                        "reddit",
                        "reddit+youtube",
                        "sec_13f",
                        "congress",
                        "rss_news",
                        "multi",
                    }
                    if merged_source not in valid:
                        merged_source = "multi"

                combined[t.ticker] = ScoredTicker(
                    ticker=t.ticker,
                    discovery_score=existing.discovery_score + t.discovery_score,
                    source=merged_source,  # type: ignore[arg-type]
                    source_detail=f"{existing.source_detail}, {t.source_detail}",
                    sentiment_hint=t.sentiment_hint,
                    context_snippets=existing.context_snippets + t.context_snippets,
                    source_urls=existing.source_urls + t.source_urls,
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

        # ── Filter pipeline: block junk symbols before DB write ──
        from app.services.symbol_filter import get_filter_pipeline

        pipeline = get_filter_pipeline()
        valid_tickers = []
        for t in tickers:
            r = pipeline.run(
                t.ticker,
                {"source": t.source},
            )
            if r.passed:
                valid_tickers.append(t)
            else:
                logger.info(
                    "[Discovery] Filtered out %s (%s)",
                    t.ticker,
                    r.reason,
                )
        if not valid_tickers:
            logger.info("[Discovery] All %d tickers filtered out", len(tickers))
            return
        logger.info(
            "[Discovery] %d/%d tickers passed filter",
            len(valid_tickers),
            len(tickers),
        )
        tickers = valid_tickers

        db = get_db()
        now = datetime.now()

        for t in tickers:
            # ── Same-day dedup guard: skip if this ticker+source was
            #    already discovered today to prevent duplicate rows.
            already_today = db.execute(
                "SELECT 1 FROM discovered_tickers "
                "WHERE ticker = ? AND source = ? "
                "AND discovered_at >= CURRENT_DATE",
                [t.ticker, t.source],
            ).fetchone()
            if already_today:
                logger.debug(
                    "[Discovery] Skipping duplicate insert for %s (source=%s, already today)",
                    t.ticker,
                    t.source,
                )
            else:
                # Save raw discovery record
                db.execute(
                    """
                    INSERT INTO discovered_tickers
                        (ticker, source, source_detail, discovery_score,
                         sentiment_hint, context_snippet, source_url, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        t.ticker,
                        t.source,
                        t.source_detail,
                        t.discovery_score,
                        t.sentiment_hint,
                        t.context_snippets[0] if t.context_snippets else "",
                        t.source_urls[0] if t.source_urls else "",
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
                        t.discovery_score,
                        reddit_score,
                        youtube_score,
                        now,
                        t.sentiment_hint,
                        now,
                        t.ticker,
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
                        t.ticker,
                        t.discovery_score,
                        youtube_score,
                        reddit_score,
                        now,
                        now,
                        t.sentiment_hint,
                        now,
                    ],
                )

        logger.info("[Discovery] Saved %d tickers to DuckDB", len(tickers))
