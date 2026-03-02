"""Integration tests for the data collection pipeline.

Tests the scrape→persist→retrieve-all pattern:
  1. YouTube 24h filter logic
  2. Data persists to DuckDB
  3. get_all_historical() returns accumulated data
  4. News collector historical retrieval
  5. Agent context receives full history
  6. New Phase 8 DB tables exist
  7. RiskComputer can be instantiated
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.database import get_db
from app.models.market_data import NewsArticle, YouTubeTranscript


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    """Get a database connection with schema migration applied."""
    import app.database as db_module
    # Reset singleton to force re-init (triggers migration)
    db_module._connection = None
    return get_db()


@pytest.fixture()
def _clean_test_data(db):
    """Clean up test data before and after each test."""
    # Use a unique ticker prefix for test isolation
    db.execute("DELETE FROM youtube_transcripts WHERE ticker LIKE 'TEST_%'")
    db.execute("DELETE FROM news_articles WHERE ticker LIKE 'TEST_%'")
    yield
    db.execute("DELETE FROM youtube_transcripts WHERE ticker LIKE 'TEST_%'")
    db.execute("DELETE FROM news_articles WHERE ticker LIKE 'TEST_%'")


# ──────────────────────────────────────────────────────────────
# Phase 8 DB Tables
# ──────────────────────────────────────────────────────────────

class TestPhase8DBTables:
    """Verify all Phase 8 database tables exist."""

    def test_all_new_tables_exist(self, db) -> None:
        tables = db.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]

        # Phase 8 tables
        assert "risk_metrics" in table_names
        assert "balance_sheet" in table_names
        assert "cash_flows" in table_names
        assert "analyst_data" in table_names
        assert "insider_activity" in table_names
        assert "earnings_calendar" in table_names

        # Original tables still exist
        assert "price_history" in table_names
        assert "fundamentals" in table_names
        assert "technicals" in table_names
        assert "news_articles" in table_names
        assert "youtube_transcripts" in table_names

    def test_news_articles_has_source_column(self, db) -> None:
        """Verify the source column exists for multi-source news."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'news_articles'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "source" in col_names

    def test_technicals_has_expanded_columns(self, db) -> None:
        """Verify the technicals table has the new expanded columns."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'technicals'"
        ).fetchall()
        col_names = [c[0] for c in cols]

        # Check key Phase 8 expanded columns
        assert "ema_9" in col_names
        assert "adx" in col_names
        assert "all_indicators_json" in col_names


# ──────────────────────────────────────────────────────────────
# YouTube 24h Filter
# ──────────────────────────────────────────────────────────────

class TestYouTube24hFilter:
    """Test the 24-hour recency filter on YouTube collection."""

    def test_curated_channels_exist(self) -> None:
        from app.services.youtube_service import YouTubeCollector
        collector = YouTubeCollector()
        assert len(collector.CURATED_CHANNELS) >= 10
        assert "CNBC" in collector.CURATED_CHANNELS
        assert "Bloomberg Television" in collector.CURATED_CHANNELS

    def test_no_truncation_constant(self) -> None:
        """MAX_TRANSCRIPT_CHARS should no longer exist — full transcripts."""
        from app.services import youtube_service
        assert not hasattr(youtube_service, "MAX_TRANSCRIPT_CHARS"), (
            "MAX_TRANSCRIPT_CHARS should be removed — no truncation"
        )

    def test_full_transcript_stored(self) -> None:
        """Verify transcripts are NOT truncated."""
        from app.services.youtube_service import YouTubeCollector
        collector = YouTubeCollector()

        long_text = "X" * 50_000  # 50K chars — would have been truncated before
        with patch.object(
            collector, "_get_transcript_library", return_value=long_text
        ):
            result = collector._get_transcript("test_long")
            assert len(result) == 50_000
            assert "truncated" not in result

    @pytest.mark.asyncio()
    async def test_24h_filter_skips_old_videos(self) -> None:
        """Videos older than 24h should be skipped."""
        from app.services.youtube_service import YouTubeCollector
        collector = YouTubeCollector()

        old_date = datetime.now(tz=timezone.utc) - timedelta(hours=48)
        recent_date = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        mock_videos = [
            {"id": "old_vid", "title": "Old Video", "published_at": old_date},
            {"id": "new_vid", "title": "New Video", "published_at": recent_date},
        ]

        with (
            patch.object(collector, "_search_videos", return_value=mock_videos),
            patch.object(collector, "_get_transcript", return_value="Full transcript text for testing"),
            patch("app.services.youtube_service.get_db") as mock_db,
        ):
            # Mock DB to say no videos exist yet
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = None
            mock_db.return_value = mock_conn

            result = await collector.collect("TEST_AAPL")

            # Only the recent video should have been processed
            assert len(result) == 1
            assert result[0].video_id == "new_vid"


# ──────────────────────────────────────────────────────────────
# YouTube DB Persistence + Historical Retrieval
# ──────────────────────────────────────────────────────────────

class TestYouTubeHistoricalRetrieval:
    """Test that transcripts persist and accumulate in the database."""

    @pytest.mark.usefixtures("_clean_test_data")
    def test_insert_and_retrieve_transcripts(self, db) -> None:
        """Insert transcripts directly and verify get_all_historical finds them."""
        from app.services.youtube_service import YouTubeCollector
        collector = YouTubeCollector()

        # Insert 3 test transcripts manually
        for i in range(3):
            db.execute(
                """
                INSERT INTO youtube_transcripts
                    (ticker, video_id, title, channel, published_at,
                     duration_seconds, raw_transcript)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST_NVDA",
                    f"test_vid_{i}",
                    f"Video {i}",
                    "TestChannel",
                    datetime.now(tz=timezone.utc) - timedelta(days=i),
                    600,
                    f"Full transcript content for video {i} " * 100,
                ],
            )

        # Retrieve all historical
        result = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_NVDA")
        )

        assert len(result) == 3
        # Should be ordered by published_at DESC
        assert result[0].title == "Video 0"  # Most recent
        assert result[2].title == "Video 2"  # Oldest
        # Full content preserved
        assert len(result[0].raw_transcript) > 500

    @pytest.mark.usefixtures("_clean_test_data")
    def test_accumulation_across_runs(self, db) -> None:
        """Verify data accumulates — multiple inserts create a growing dataset."""
        from app.services.youtube_service import YouTubeCollector
        collector = YouTubeCollector()

        # Simulate "run 1" — insert 2 transcripts
        for i in range(2):
            db.execute(
                """
                INSERT INTO youtube_transcripts
                    (ticker, video_id, title, channel, published_at,
                     duration_seconds, raw_transcript)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST_TSLA",
                    f"run1_vid_{i}",
                    f"Run1 Video {i}",
                    "Channel1",
                    datetime.now(tz=timezone.utc) - timedelta(days=i),
                    300,
                    f"Transcript from run 1, video {i}",
                ],
            )

        result1 = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_TSLA")
        )
        assert len(result1) == 2

        # Simulate "run 2" — insert 1 more
        db.execute(
            """
            INSERT INTO youtube_transcripts
                (ticker, video_id, title, channel, published_at,
                 duration_seconds, raw_transcript)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "TEST_TSLA",
                "run2_vid_0",
                "Run2 Video 0",
                "Channel2",
                datetime.now(tz=timezone.utc),
                450,
                "Transcript from run 2",
            ],
        )

        # get_all_historical should now return 3 (accumulated)
        result2 = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_TSLA")
        )
        assert len(result2) == 3
        assert result2[0].title == "Run2 Video 0"  # Most recent


# ──────────────────────────────────────────────────────────────
# News Historical Retrieval
# ──────────────────────────────────────────────────────────────

class TestNewsHistoricalRetrieval:
    """Test that news articles persist and accumulate in the database."""

    @pytest.mark.usefixtures("_clean_test_data")
    def test_insert_and_retrieve_news(self, db) -> None:
        """Insert news articles and verify get_all_historical finds them."""
        from app.services.news_service import NewsCollector
        collector = NewsCollector()

        # Insert test articles from different sources
        sources = ["yfinance", "google_news", "sec_edgar"]
        for i, source in enumerate(sources):
            db.execute(
                """
                INSERT INTO news_articles
                    (ticker, article_hash, title, publisher, url,
                     published_at, summary, thumbnail_url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST_AAPL",
                    f"hash_{source}_{i}",
                    f"Article from {source}",
                    f"Publisher {i}",
                    f"https://example.com/{i}",
                    datetime.now(tz=timezone.utc) - timedelta(hours=i),
                    f"Summary for {source} article",
                    "",
                    source,
                ],
            )

        result = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_AAPL")
        )

        assert len(result) == 3
        # Check source tracking works
        sources_found = {a.source for a in result}
        assert "yfinance" in sources_found
        assert "google_news" in sources_found
        assert "sec_edgar" in sources_found

    @pytest.mark.usefixtures("_clean_test_data")
    def test_news_accumulation(self, db) -> None:
        """Verify news accumulates across multiple collection runs."""
        from app.services.news_service import NewsCollector
        collector = NewsCollector()

        # Day 1: 2 articles
        for i in range(2):
            db.execute(
                """
                INSERT INTO news_articles
                    (ticker, article_hash, title, publisher, url,
                     published_at, summary, thumbnail_url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST_MSFT",
                    f"day1_hash_{i}",
                    f"Day1 Article {i}",
                    "Publisher",
                    f"https://day1.com/{i}",
                    datetime.now(tz=timezone.utc) - timedelta(days=1),
                    "Day 1 summary",
                    "",
                    "yfinance",
                ],
            )

        r1 = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_MSFT")
        )
        assert len(r1) == 2

        # Day 2: 3 more articles
        for i in range(3):
            db.execute(
                """
                INSERT INTO news_articles
                    (ticker, article_hash, title, publisher, url,
                     published_at, summary, thumbnail_url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST_MSFT",
                    f"day2_hash_{i}",
                    f"Day2 Article {i}",
                    "Publisher",
                    f"https://day2.com/{i}",
                    datetime.now(tz=timezone.utc),
                    "Day 2 summary",
                    "",
                    "google_news",
                ],
            )

        r2 = asyncio.get_event_loop().run_until_complete(
            collector.get_all_historical("TEST_MSFT")
        )
        assert len(r2) == 5  # Accumulated!


# ──────────────────────────────────────────────────────────────
# Agent Context Receives Historical Data
# ──────────────────────────────────────────────────────────────

class TestAgentContextHistoricalData:
    """Agents were deleted in refactor — these tests are skipped."""

    @pytest.mark.skip(reason="Agents deleted in refactor — data formatting moved to DataDistiller")
    def test_sentiment_agent_formats_multi_source_news(self) -> None:
        pass

    @pytest.mark.skip(reason="Agents deleted in refactor")
    def test_sentiment_agent_handles_empty_data(self) -> None:
        pass


# ──────────────────────────────────────────────────────────────
# RiskComputer
# ──────────────────────────────────────────────────────────────

class TestRiskComputer:
    """Test the RiskComputer can be instantiated and has correct constants."""

    def test_instantiation(self) -> None:
        from app.services.risk_service import RiskComputer
        rc = RiskComputer()
        assert rc.RISK_FREE_RATE > 0
        assert rc.TRADING_DAYS_PER_YEAR == 252

    def test_risk_metrics_dataclass(self) -> None:
        from dataclasses import asdict

        from app.services.risk_service import RiskMetrics
        metrics = RiskMetrics(ticker="NVDA", computed_date=date.today())
        d = asdict(metrics)
        assert d["ticker"] == "NVDA"
        assert "sharpe_ratio" in d
        assert "var_95" in d
        assert "beta" in d
        assert "max_drawdown" in d


# ──────────────────────────────────────────────────────────────
# Fundamental Agent New Data
# ──────────────────────────────────────────────────────────────

class TestFundamentalAgentNewData:
    """Agents were deleted in refactor — these tests are skipped."""

    @pytest.mark.skip(reason="FundamentalAgent deleted in refactor")
    def test_formats_balance_sheet_data(self) -> None:
        pass

    @pytest.mark.skip(reason="FundamentalAgent deleted in refactor")
    def test_formats_analyst_data(self) -> None:
        pass


# ──────────────────────────────────────────────────────────────
# VTT Parser (migrated from old tests)
# ──────────────────────────────────────────────────────────────

class TestVTTParsing:
    """Tests for the VTT subtitle parser."""

    def test_basic_vtt(self) -> None:
        from app.services.youtube_service import YouTubeCollector
        vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:03.000
Hello everyone welcome to the show

00:00:03.000 --> 00:00:06.000
Today we are talking about NVDA
"""
        result = YouTubeCollector._parse_vtt(vtt)
        assert "Hello everyone welcome to the show" in result
        assert "Today we are talking about NVDA" in result

    def test_dedup_overlapping_segments(self) -> None:
        from app.services.youtube_service import YouTubeCollector
        vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Hello world

00:00:01.000 --> 00:00:04.000
Hello world

00:00:03.000 --> 00:00:06.000
This is a test
"""
        result = YouTubeCollector._parse_vtt(vtt)
        assert result.count("Hello world") == 1
        assert "This is a test" in result

    def test_empty_vtt(self) -> None:
        from app.services.youtube_service import YouTubeCollector
        result = YouTubeCollector._parse_vtt("WEBVTT\n\n")
        assert result == ""
