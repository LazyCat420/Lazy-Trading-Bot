"""Tests for RSS News Collector.

Run: .\\venv\\Scripts\\activate; python -m pytest tests/test_rss_news.py -v -s
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


from app.collectors.rss_news_collector import RSSNewsCollector

# ── Logging setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


# Sample RSS feed entries (feedparser format)
MOCK_FEED_ENTRIES = [
    {
        "title": "Fed signals rate cuts despite inflation concerns",
        "link": "https://reuters.com/article/fed-rate-cuts-2025",
        "summary": "<p>Federal Reserve Chair Powell indicated possible rate cuts...</p>",
        "source": {"title": "Reuters"},
        "published_parsed": (2025, 2, 20, 12, 30, 0, 3, 51, 0),
    },
    {
        "title": "NVIDIA earnings beat Wall Street estimates",
        "link": "https://cnbc.com/article/nvda-earnings-2025",
        "summary": "NVIDIA reported Q4 earnings above expectations...",
        "source": {"title": "CNBC"},
        "published_parsed": (2025, 2, 20, 14, 0, 0, 3, 51, 0),
    },
    {
        "title": "",  # Empty title — should be skipped
        "link": "https://empty.com",
        "summary": "",
    },
]

MOCK_ARTICLE_CONTENT = (
    "Federal Reserve Chair Jerome Powell said on Friday the U.S. central bank "
    "is well positioned to cut rates later this year despite recent sticky inflation data. "
    "Speaking at a press conference after the FOMC meeting, Powell noted that the "
    "labor market remains strong and GDP growth continues. "
    "Stocks like $AAPL, $MSFT, and $NVDA rallied on the news. "
    "The S&P 500 and NASDAQ both hit new highs following the announcement. "
    "Bond yields fell across the curve as investors priced in rate cuts. " * 5
)


# ══════════════════════════════════════════════════════════════════
# 1. TICKER EXTRACTION TESTS
# ══════════════════════════════════════════════════════════════════


class TestTickerExtraction:
    """Tests for extracting stock tickers from article text."""

    def setup_method(self) -> None:
        self.collector = RSSNewsCollector()
        log.info("=== TestTickerExtraction setup ===")

    def test_dollar_sign_tickers(self) -> None:
        """Should find $TICKER patterns."""
        text = "Bought $AAPL and $MSFT today. Also watching $NVDA."
        tickers = self.collector._extract_tickers_from_text(text)
        log.info("Dollar tickers: %s", tickers)
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "NVDA" in tickers

    def test_excludes_common_words(self) -> None:
        """Should not include common false positives."""
        text = "THE FED will NOT raise rates FOR the US economy."
        tickers = self.collector._extract_tickers_from_text(text)
        log.info("Filtered tickers: %s", tickers)
        assert "THE" not in tickers
        assert "NOT" not in tickers
        assert "FOR" not in tickers
        assert "FED" not in tickers

    def test_mixed_content(self) -> None:
        """Should handle mixed dollar-sign and standalone tickers."""
        text = "Watch $TSLA surge as Tesla stock hits new highs. AMZN also rallying."
        tickers = self.collector._extract_tickers_from_text(text)
        log.info("Mixed tickers: %s", tickers)
        assert "TSLA" in tickers
        assert "AMZN" in tickers

    def test_empty_text(self) -> None:
        """Empty text should return no tickers."""
        tickers = self.collector._extract_tickers_from_text("")
        assert tickers == []

    def test_limits_to_10(self) -> None:
        """Should limit to top 10 tickers."""
        text = "$AAPL $MSFT $NVDA $AMZN $GOOGL $META $TSLA $JPM $BAC $WFC $GS $C"
        tickers = self.collector._extract_tickers_from_text(text)
        log.info("Limited tickers (%d): %s", len(tickers), tickers)
        assert len(tickers) <= 10


# ══════════════════════════════════════════════════════════════════
# 2. FEED PARSING TESTS
# ══════════════════════════════════════════════════════════════════


class TestFeedParsing:
    """Tests for RSS feed parsing and article extraction."""

    @patch("app.collectors.rss_news_collector.get_db")
    def test_scrape_feed_extracts_valid_entries(self, mock_get_db: MagicMock) -> None:
        """Should extract articles from valid feed entries."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None  # No existing
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()

        # Mock feedparser and article extraction
        with (
            patch("app.collectors.rss_news_collector.feedparser") as mock_fp,
            patch.object(collector, "_extract_article_content") as mock_extract,
        ):
            mock_feed = MagicMock()
            mock_feed.entries = MOCK_FEED_ENTRIES
            mock_fp.parse.return_value = mock_feed

            # Return content for first 2 entries (3rd is empty title)
            mock_extract.side_effect = [
                MOCK_ARTICLE_CONTENT,
                "NVIDIA reported quarterly earnings of $1.2B..." * 20,
            ]

            feed_config = {"name": "reuters_test", "url": "https://test.rss/feed"}
            articles = collector._scrape_feed(feed_config, mock_db)

            log.info("Parsed %d articles:", len(articles))
            for a in articles:
                log.info("  %s (%d chars) — tickers: %s", a["title"][:40], a["content_length"], a["tickers_found"])

            assert len(articles) == 2
            assert articles[0]["publisher"] == "Reuters"
            assert articles[0]["content_length"] > 200

    @patch("app.collectors.rss_news_collector.get_db")
    def test_skips_short_content(self, mock_get_db: MagicMock) -> None:
        """Articles with < 200 chars should be skipped."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()

        with (
            patch("app.collectors.rss_news_collector.feedparser") as mock_fp,
            patch.object(collector, "_extract_article_content") as mock_extract,
        ):
            mock_feed = MagicMock()
            mock_feed.entries = [MOCK_FEED_ENTRIES[0]]
            mock_fp.parse.return_value = mock_feed
            mock_extract.return_value = "Short content"  # < 200 chars

            feed_config = {"name": "test", "url": "https://test.rss/feed"}
            articles = collector._scrape_feed(feed_config, mock_db)

            log.info("Short content articles: %d (should be 0)", len(articles))
            assert len(articles) == 0

    @patch("app.collectors.rss_news_collector.get_db")
    def test_deduplicates_by_hash(self, mock_get_db: MagicMock) -> None:
        """Already-seen articles should be skipped."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = (1,)  # Already exists
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()

        with patch("app.collectors.rss_news_collector.feedparser") as mock_fp:
            mock_feed = MagicMock()
            mock_feed.entries = MOCK_FEED_ENTRIES[:2]
            mock_fp.parse.return_value = mock_feed

            feed_config = {"name": "test", "url": "https://test.rss/feed"}
            articles = collector._scrape_feed(feed_config, mock_db)

            log.info("Deduped articles: %d (should be 0)", len(articles))
            assert len(articles) == 0


# ══════════════════════════════════════════════════════════════════
# 3. DB INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════


class TestRSSDBIntegration:
    """Tests for database operations."""

    @patch("app.collectors.rss_news_collector.get_db")
    def test_daily_guard(self, mock_get_db: MagicMock) -> None:
        """Should skip scraping if already collected today."""
        import asyncio

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = (25,)  # 25 today
        mock_db.execute.return_value.fetchall.return_value = [
            ("Fed News", "https://url", "Reuters", "2025-02-20 12:00", "Summary", "Content " * 100, 700, "AAPL,MSFT", "reuters"),
        ]
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()
        result = asyncio.get_event_loop().run_until_complete(
            collector.scrape_all_feeds()
        )

        log.info("Daily guard result: %d articles (should use cache)", len(result))
        assert len(result) > 0  # Returns cached data

    @patch("app.collectors.rss_news_collector.get_db")
    def test_get_articles_for_ticker(self, mock_get_db: MagicMock) -> None:
        """Should return articles mentioning a specific ticker."""
        import asyncio

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            ("AAPL Earnings Beat", "https://url", "Reuters", "2025-02-20", "Summary",
             "Apple reported strong Q4 earnings...", 500, "reuters"),
        ]
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()
        result = asyncio.get_event_loop().run_until_complete(
            collector.get_articles_for_ticker("AAPL")
        )

        log.info("Articles for AAPL: %d", len(result))
        assert len(result) == 1
        assert result[0]["title"] == "AAPL Earnings Beat"
        assert result[0]["content"] == "Apple reported strong Q4 earnings..."

    @patch("app.collectors.rss_news_collector.get_db")
    def test_get_discovery_tickers(self, mock_get_db: MagicMock) -> None:
        """Should generate scored tickers from article mentions."""
        import asyncio

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            ("AAPL,MSFT,NVDA",),
            ("AAPL,GOOGL",),
            ("NVDA,TSLA",),
        ]
        mock_get_db.return_value = mock_db

        collector = RSSNewsCollector()
        tickers = asyncio.get_event_loop().run_until_complete(
            collector.get_discovery_tickers()
        )

        log.info("Discovery tickers: %d", len(tickers))
        for t in tickers:
            log.info("  %s: %.1f pts — %s", t.ticker, t.discovery_score, t.source_detail)

        # AAPL appears twice, NVDA twice
        aapl = next((t for t in tickers if t.ticker == "AAPL"), None)
        assert aapl is not None
        assert aapl.discovery_score == 2.0  # 2 articles × 1.0

        nvda = next((t for t in tickers if t.ticker == "NVDA"), None)
        assert nvda is not None
        assert nvda.discovery_score == 2.0


# ══════════════════════════════════════════════════════════════════
# 4. YOUTUBE DURATION FILTER TESTS
# ══════════════════════════════════════════════════════════════════


class TestYouTubeDurationFilter:
    """Tests for the 15+ minute duration filter."""

    def test_duration_filter_logic(self) -> None:
        """Videos < 900s should be filtered out."""
        videos = [
            {"id": "short1", "duration": 120, "title": "Short clip"},
            {"id": "long1", "duration": 1800, "title": "30min analysis"},
            {"id": "short2", "duration": 300, "title": "5min recap"},
            {"id": "long2", "duration": 900, "title": "15min exactly"},
            {"id": "no_dur", "duration": 0, "title": "No duration"},
        ]

        min_duration = 900
        long_videos = [v for v in videos if v.get("duration", 0) >= min_duration]

        log.info("Filtered %d/%d videos:", len(long_videos), len(videos))
        for v in long_videos:
            log.info("  %s (%ds)", v["title"], v["duration"])

        assert len(long_videos) == 2
        assert long_videos[0]["id"] == "long1"
        assert long_videos[1]["id"] == "long2"
