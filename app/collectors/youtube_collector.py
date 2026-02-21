"""YouTube transcript collector — multi-strategy extraction with 24h filter.

Design philosophy:
  - SCRAPE only videos published in the last 24 hours (freshness)
  - PERSIST all transcripts to DuckDB (accumulation)
  - SERVE all historical transcripts to agents (leverage)

Strategy (mirrors the proven Youtube-News-Extracter approach):
  1. Library-first: youtube-transcript-api (pure Python, fast)
  2. yt-dlp fallback: --write-auto-subs to extract auto-generated captions
  3. Search: yt-dlp ytsearch for video discovery
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import get_db
from app.models.market_data import YouTubeTranscript
from app.utils.logger import logger


class YouTubeCollector:
    """Collects YouTube transcripts for stock analysis videos.

    Uses a multi-query search strategy and two-tier transcript extraction.

    Key behavior:
      - collect()              → scrape 24h only, persist, return only new
      - get_all_historical()   → return ALL stored transcripts for a ticker
    """

    SEARCH_QUERIES = [
        "{ticker} stock analysis",
        "{ticker} earnings",
        "{ticker} technical analysis",
    ]

    # Broader market queries for discovering NEW tickers
    GENERAL_MARKET_QUERIES = [
        "stock market news today",
        "stocks to buy now",
        "best stocks this week",
        "stock market analysis today",
        "top stocks to watch this week",
        "stock market recap today",
        "wall street week ahead",
        "market movers today stocks",
    ]

    # Curated financial channels — prioritized in search results
    CURATED_CHANNELS = [
        "CNBC",
        "Bloomberg Television",
        "Yahoo Finance",
        "Investor's Business Daily",
        "The Motley Fool",
        "Zacks Investment Research",
        "TD Ameritrade Network",
        "Schwab Network",
        "tastylive",
        "Meet Kevin",
        "Financial Education",
        "Stock Moe",
        "Tom Nash",
        "Everything Money",
        "Let's Talk Money",
    ]

    # ──────────────────────────────────────────────────────────────
    # Main API
    # ──────────────────────────────────────────────────────────────

    async def collect(
        self, ticker: str, max_videos: int = 3, *, discovery_mode: bool = False
    ) -> list[YouTubeTranscript]:
        """Scrape YouTube for NEW videos from the last 24 hours only.

        Pipeline:
          1. Search across multiple queries
          2. Filter to videos published within the last 24 hours
          3. Deduplicate against DB (skip already-collected video_ids)
          4. Extract transcripts (library-first, yt-dlp fallback)
          5. Persist NEW transcripts to DuckDB

        Returns only the newly collected transcripts (not historical).
        Use get_all_historical() to retrieve the full accumulated dataset.
        """
        # Daily guard — skip if already scraped today (skipped in discovery mode)
        db = get_db()
        if not discovery_mode:
            today_start = datetime.now(tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            existing = db.execute(
                "SELECT COUNT(*) FROM youtube_transcripts "
                "WHERE ticker = ? AND collected_at >= ?",
                [ticker, today_start],
            ).fetchone()
            if existing and existing[0] > 0:
                logger.info(
                    "YouTube for %s already scraped today (%d transcripts), skipping",
                    ticker, existing[0],
                )
                return []

        mode_label = "discovery" if discovery_mode else "24h filter"
        logger.info("Collecting YouTube transcripts for %s (%s)", ticker, mode_label)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

        # Step 1: Multi-query search
        all_videos: list[dict] = []
        seen_ids: set[str] = set()

        for query_template in self.SEARCH_QUERIES:
            query = query_template.format(ticker=ticker)
            results = self._search_videos(query, max_videos)
            for vid in results:
                vid_id = vid["id"]
                if vid_id not in seen_ids:
                    seen_ids.add(vid_id)
                    all_videos.append(vid)

        logger.info(
            "Found %d unique videos across %d queries for %s",
            len(all_videos),
            len(self.SEARCH_QUERIES),
            ticker,
        )

        # Step 2: Apply recency filter (skipped in discovery mode)
        if discovery_mode:
            recent_videos = all_videos
            logger.info(
                "Discovery mode: accepting all %d videos for %s",
                len(all_videos), ticker,
            )
        else:
            recent_videos = []
            for vid in all_videos:
                pub = vid.get("published_at")
                if pub is None:
                    # No publish date → include it (can't verify age)
                    recent_videos.append(vid)
                elif pub >= cutoff:
                    recent_videos.append(vid)
                else:
                    logger.debug(
                        "Skipping old video %s (published %s, cutoff %s)",
                        vid["id"],
                        pub.isoformat(),
                        cutoff.isoformat(),
                    )

            logger.info(
                "%d of %d videos are within 24h window for %s",
                len(recent_videos),
                len(all_videos),
                ticker,
            )

        if not recent_videos:
            logger.info("No recent YouTube videos found for %s", ticker)
            return []

        # Step 3: Filter out already-collected videos
        db = get_db()
        new_videos = []
        for vid in recent_videos:
            existing = db.execute(
                "SELECT 1 FROM youtube_transcripts WHERE ticker = ? AND video_id = ?",
                [ticker, vid["id"]],
            ).fetchone()
            if not existing:
                new_videos.append(vid)

        if not new_videos:
            logger.info("All recent YouTube videos for %s already collected", ticker)
            return []

        logger.info("%d new videos to process for %s", len(new_videos), ticker)

        # Step 4: Extract transcripts (two-tier) — NO truncation
        transcripts: list[YouTubeTranscript] = []
        for vid in new_videos:
            transcript_text = self._get_transcript(vid["id"])
            if not transcript_text:
                logger.info(
                    "No transcript available for video %s (%s)",
                    vid["id"],
                    vid.get("title", ""),
                )
                continue

            # Log preview for debugging
            logger.info(
                "── Transcript preview for [%s] ──\n%s\n── end preview ──",
                vid.get("title", vid["id"]),
                transcript_text[:500],
            )

            yt = YouTubeTranscript(
                ticker=ticker,
                video_id=vid["id"],
                title=vid.get("title", ""),
                channel=vid.get("channel", ""),
                published_at=vid.get("published_at"),
                duration_seconds=vid.get("duration", 0),
                raw_transcript=transcript_text,
            )
            transcripts.append(yt)

            # Persist to DB (accumulates over time)
            db.execute(
                """
                INSERT INTO youtube_transcripts
                    (ticker, video_id, title, channel, published_at,
                     duration_seconds, raw_transcript)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    yt.ticker,
                    yt.video_id,
                    yt.title,
                    yt.channel,
                    yt.published_at,
                    yt.duration_seconds,
                    yt.raw_transcript,
                ],
            )

        logger.info(
            "Collected %d NEW transcripts for %s (from %d candidates)",
            len(transcripts),
            ticker,
            len(new_videos),
        )
        return transcripts

    async def collect_general_market(
        self, max_videos: int = 5, min_duration_secs: int = 900,
    ) -> list[YouTubeTranscript]:
        """Scrape general market news videos to discover NEW tickers.

        Uses broad queries like 'stock market news today' instead of
        ticker-specific ones.  Transcripts are stored with
        ticker='__MARKET__' and later scanned by TickerScanner.

        Only accepts videos >= min_duration_secs (default 900 = 15 min)
        to ensure substantive analysis content rather than short clips.

        Has its own daily guard — skips if already scraped today.
        """
        db = get_db()

        # Daily guard for general market scrapes
        today_start = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        existing = db.execute(
            "SELECT COUNT(*) FROM youtube_transcripts "
            "WHERE ticker = '__MARKET__' AND collected_at >= ?",
            [today_start],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info(
                "General market YouTube already scraped today "
                "(%d transcripts), skipping",
                existing[0],
            )
            return []

        logger.info(
            "Collecting general market YouTube transcripts "
            "(%d queries, min duration %ds)...",
            len(self.GENERAL_MARKET_QUERIES),
            min_duration_secs,
        )

        # Step 1: Search across general market queries (full metadata)
        all_videos: list[dict] = []
        seen_ids: set[str] = set()

        for query in self.GENERAL_MARKET_QUERIES:
            results = self._search_videos_full(query, max_videos)
            for vid in results:
                vid_id = vid["id"]
                if vid_id not in seen_ids:
                    seen_ids.add(vid_id)
                    all_videos.append(vid)

        logger.info(
            "Found %d unique general market videos", len(all_videos),
        )

        if not all_videos:
            return []

        # Step 2: Filter by minimum duration (15+ min = in-depth content)
        long_videos = [
            v for v in all_videos
            if v.get("duration", 0) >= min_duration_secs
        ]
        short_count = len(all_videos) - len(long_videos)
        if short_count > 0:
            logger.info(
                "Filtered out %d short videos (< %ds), %d remain",
                short_count, min_duration_secs, len(long_videos),
            )
        if not long_videos:
            logger.info("No videos meet the %ds minimum duration", min_duration_secs)
            return []

        # Step 3: Filter out already-collected videos
        new_videos = []
        for vid in long_videos:
            existing_vid = db.execute(
                "SELECT 1 FROM youtube_transcripts "
                "WHERE ticker = '__MARKET__' AND video_id = ?",
                [vid["id"]],
            ).fetchone()
            if not existing_vid:
                new_videos.append(vid)

        if not new_videos:
            logger.info("All general market videos already collected")
            return []

        logger.info(
            "%d new general market videos to process (>= %ds)",
            len(new_videos), min_duration_secs,
        )

        # Step 4: Extract transcripts and persist
        transcripts: list[YouTubeTranscript] = []
        for vid in new_videos:
            transcript_text = self._get_transcript(vid["id"])
            if not transcript_text:
                continue

            yt = YouTubeTranscript(
                ticker="__MARKET__",
                video_id=vid["id"],
                title=vid.get("title", ""),
                channel=vid.get("channel", ""),
                published_at=vid.get("published_at"),
                duration_seconds=vid.get("duration", 0),
                raw_transcript=transcript_text,
            )
            transcripts.append(yt)

            db.execute(
                """
                INSERT INTO youtube_transcripts
                    (ticker, video_id, title, channel, published_at,
                     duration_seconds, raw_transcript)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    yt.ticker,
                    yt.video_id,
                    yt.title,
                    yt.channel,
                    yt.published_at,
                    yt.duration_seconds,
                    yt.raw_transcript,
                ],
            )

        logger.info(
            "Collected %d general market transcripts (>= %ds)",
            len(transcripts), min_duration_secs,
        )
        return transcripts

    async def get_all_historical(
        self, ticker: str, limit: int = 50
    ) -> list[YouTubeTranscript]:
        """Retrieve ALL stored transcripts for a ticker from the database.

        This is the key method for leveraging accumulated data — agents
        receive the full history, not just the latest scrape.

        Args:
            ticker: Stock ticker symbol
            limit: Max transcripts to return (most recent first)
        """
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, video_id, title, channel, published_at,
                   duration_seconds, raw_transcript
            FROM youtube_transcripts
            WHERE ticker = ?
            ORDER BY published_at DESC NULLS LAST
            LIMIT ?
            """,
            [ticker, limit],
        ).fetchall()

        transcripts = [
            YouTubeTranscript(
                ticker=r[0],
                video_id=r[1],
                title=r[2],
                channel=r[3],
                published_at=r[4],
                duration_seconds=r[5],
                raw_transcript=r[6],
            )
            for r in rows
        ]

        logger.info(
            "Retrieved %d historical transcripts for %s from DB",
            len(transcripts),
            ticker,
        )
        return transcripts

    # ──────────────────────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────────────────────

    def _search_videos(self, query: str, max_results: int) -> list[dict]:
        """Use yt-dlp to search YouTube and get video metadata.

        Uses --flat-playlist for speed (no duration data).
        For general market discovery, use _search_videos_full() instead.
        """
        search_term = f"ytsearch{max_results}:{query}"
        logger.debug("yt-dlp search: %s", search_term)

        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--flat-playlist",
                    "--print-json",
                    "--no-download",
                    search_term,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            return self._parse_yt_dlp_output(result.stdout)

        except FileNotFoundError:
            logger.warning(
                "yt-dlp not found — install it: pip install yt-dlp"
            )
            return []
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp search timed out for: %s", query)
            return []
        except Exception as e:
            logger.error("yt-dlp search failed: %s", e)
            return []

    def _search_videos_full(self, query: str, max_results: int) -> list[dict]:
        """Use yt-dlp with full metadata (includes duration).

        Slower than _search_videos but returns duration field needed
        for the 15+ minute filter in general market discovery.
        """
        search_term = f"ytsearch{max_results}:{query}"
        logger.debug("yt-dlp full search: %s", search_term)

        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--dump-json",
                    "--no-download",
                    "--no-warnings",
                    search_term,
                ],
                capture_output=True,
                text=True,
                timeout=60,  # Longer timeout — full metadata is slower
            )

            return self._parse_yt_dlp_output(result.stdout)

        except FileNotFoundError:
            logger.warning(
                "yt-dlp not found — install it: pip install yt-dlp"
            )
            return []
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp full search timed out for: %s", query)
            return []
        except Exception as e:
            logger.error("yt-dlp full search failed: %s", e)
            return []

    def _parse_yt_dlp_output(self, stdout: str) -> list[dict]:
        """Parse yt-dlp JSON output lines into video dicts."""
        videos = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                vid_id = data.get("id", data.get("url", ""))
                if not vid_id:
                    continue

                pub_date = None
                upload = data.get("upload_date")
                if upload:
                    try:
                        pub_date = datetime.strptime(upload, "%Y%m%d").replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        pass

                channel = data.get("channel", data.get("uploader", ""))
                duration = data.get("duration", 0) or 0

                videos.append(
                    {
                        "id": vid_id,
                        "title": data.get("title", ""),
                        "channel": channel,
                        "duration": duration,
                        "published_at": pub_date,
                        "view_count": data.get("view_count", 0) or 0,
                        "is_curated": channel in self.CURATED_CHANNELS,
                    }
                )
            except json.JSONDecodeError:
                continue

        # Prioritize curated channels
        videos.sort(key=lambda v: (not v.get("is_curated", False), 0))

        logger.debug("yt-dlp parsed %d videos", len(videos))
        return videos

    # ──────────────────────────────────────────────────────────────
    # Transcript Extraction (Two-Tier) — NO TRUNCATION
    # ──────────────────────────────────────────────────────────────

    def _get_transcript(self, video_id: str) -> str:
        """Extract transcript using two-tier strategy.

        Tier 1: youtube-transcript-api (pure Python, fast)
        Tier 2: yt-dlp --write-auto-subs (fallback for restricted transcripts)

        FULL transcripts are stored — no truncation.
        """
        # Tier 1: Library-first
        transcript = self._get_transcript_library(video_id)
        if transcript:
            return transcript

        # Tier 2: yt-dlp subtitle extraction fallback
        logger.info(
            "Library transcript failed for %s, trying yt-dlp subtitles...",
            video_id,
        )
        transcript = self._get_transcript_ytdlp(video_id)
        if transcript:
            return transcript

        return ""

    def _get_transcript_library(self, video_id: str) -> str:
        """Tier 1: Fetch transcript using youtube-transcript-api.

        Uses the v1.x API: YouTubeTranscriptApi() instance + .fetch().
        Snippet objects have .text attribute (not dict["text"]).
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)
            full_text = " ".join(
                snippet.text for snippet in transcript
            )
            full_text = full_text.replace("\n", " ").strip()

            if len(full_text) < 50:
                logger.debug(
                    "Transcript too short for %s (%d chars)", video_id, len(full_text)
                )
                return ""

            # NO TRUNCATION — store full transcript for historical value
            logger.info(
                "Library transcript OK for %s (%d chars)", video_id, len(full_text)
            )
            return full_text

        except ImportError:
            logger.warning(
                "youtube-transcript-api not installed — pip install youtube-transcript-api"
            )
            return ""
        except Exception as e:
            logger.debug(
                "Library transcript failed for %s: %s", video_id, e
            )
            return ""

    def _get_transcript_ytdlp(self, video_id: str) -> str:
        """Tier 2: Extract auto-generated subtitles using yt-dlp.

        This is the fallback when youtube-transcript-api fails (e.g. age-gated
        videos, geo-restricted content, or disabled manual captions).
        """
        url = f"https://www.youtube.com/watch?v={video_id}"

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_template = str(Path(tmpdir) / "subs")

                subprocess.run(
                    [
                        "yt-dlp",
                        "--skip-download",
                        "--write-auto-subs",
                        "--sub-lang", "en",
                        "--sub-format", "vtt",
                        "--output", output_template,
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                # Find the subtitle file
                sub_files = list(Path(tmpdir).glob("*.vtt"))
                if not sub_files:
                    logger.debug(
                        "yt-dlp found no subtitles for %s", video_id
                    )
                    return ""

                # Parse VTT to plain text
                vtt_content = sub_files[0].read_text(encoding="utf-8")
                transcript = self._parse_vtt(vtt_content)

                if len(transcript) < 50:
                    return ""

                # NO TRUNCATION — store full transcript
                logger.info(
                    "yt-dlp subtitle OK for %s (%d chars)",
                    video_id,
                    len(transcript),
                )
                return transcript

        except FileNotFoundError:
            logger.warning("yt-dlp not found for subtitle extraction")
            return ""
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp subtitle extraction timed out for %s", video_id)
            return ""
        except Exception as e:
            logger.error("yt-dlp subtitle extraction failed for %s: %s", video_id, e)
            return ""

    @staticmethod
    def _parse_vtt(vtt_content: str) -> str:
        """Parse a WebVTT file to plain text, removing timestamps and duplicates.

        VTT files from YouTube auto-captions often have overlapping segments.
        This deduplicates them while preserving order.
        """
        lines = vtt_content.split("\n")
        text_parts: list[str] = []
        seen_lines: set[str] = set()

        for line in lines:
            line = line.strip()

            # Skip VTT header, timestamps, empty lines, and position markers
            if not line:
                continue
            if line.startswith("WEBVTT"):
                continue
            if line.startswith("Kind:") or line.startswith("Language:"):
                continue
            if "-->" in line:
                continue
            if line.startswith("NOTE"):
                continue

            # Remove HTML-like tags (e.g. <c>, </c>, <00:01:02.000>)
            clean = re.sub(r"<[^>]+>", "", line).strip()

            if clean and clean not in seen_lines:
                seen_lines.add(clean)
                text_parts.append(clean)

        return " ".join(text_parts)
