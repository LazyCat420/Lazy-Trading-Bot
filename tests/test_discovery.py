"""Tests for Phase 1 — Ticker Discovery Pipeline.

Each test has logging/print statements so you can audit exactly what happened.
Run: .\\venv\\Scripts\\activate; python -m pytest tests/test_discovery.py -v -s
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.collectors.ticker_validator import TickerValidator
from app.collectors.reddit_collector import RedditCollector
from app.collectors.ticker_scanner import TickerScanner
from app.models.discovery import DiscoveryResult, ScoredTicker
from app.services.discovery_service import DiscoveryService

# ── Logging setup — all test output visible with -s flag ─────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 1. TICKER VALIDATOR TESTS
# ══════════════════════════════════════════════════════════════════


class TestTickerValidator:
    """Tests for the three-layer ticker validator."""

    def setup_method(self) -> None:
        self.validator = TickerValidator()
        log.info("=== TestTickerValidator setup ===")

    def test_exclusion_list_rejects_jargon(self) -> None:
        """Common words and finance jargon should be rejected."""
        jargon = ["YOLO", "DD", "ATH", "IMO", "CEO", "SEC", "AI", "TLDR"]
        log.info("Testing exclusion list with: %s", jargon)
        for word in jargon:
            result = self.validator.validate(word)
            log.info("  %s → rejected=%s", word, not result)
            assert not result, f"{word} should be rejected by exclusion list"

    def test_exclusion_list_rejects_common_words(self) -> None:
        """Common English words should be rejected."""
        words = ["NOT", "FOR", "AND", "THE", "BUT", "GOOD", "WELL"]
        log.info("Testing common words: %s", words)
        for word in words:
            result = self.validator.validate(word)
            log.info("  %s → rejected=%s", word, not result)
            assert not result, f"{word} should be rejected"

    def test_length_validation(self) -> None:
        """Too short/long strings should be rejected."""
        log.info("Testing length validation")
        assert not self.validator.validate("")   # too short
        assert not self.validator.validate("ABCDEF")  # too long (> 5 chars)
        log.info("  Empty and 'ABCDEF' correctly rejected")
        # Note: 'A' is a valid 1-char ticker (Agilent Technologies)

    @patch("app.collectors.ticker_validator.yf.Ticker")
    def test_yfinance_valid_ticker(self, mock_ticker_cls: MagicMock) -> None:
        """A real ticker with price data should pass."""
        mock_fi = MagicMock()
        mock_fi.last_price = 125.50
        mock_ticker_cls.return_value.fast_info = mock_fi

        result = self.validator.validate("NVDA")
        log.info("NVDA validation with mocked price $125.50: %s", result)
        assert result is True

    @patch("app.collectors.ticker_validator.yf.Ticker")
    def test_yfinance_no_price(self, mock_ticker_cls: MagicMock) -> None:
        """A ticker with no price data should fail."""
        mock_fi = MagicMock()
        mock_fi.last_price = None
        mock_ticker_cls.return_value.fast_info = mock_fi

        result = self.validator.validate("FAKE")
        log.info("FAKE validation with no price: %s", result)
        assert result is False

    @patch("app.collectors.ticker_validator.yf.Ticker")
    def test_caching(self, mock_ticker_cls: MagicMock) -> None:
        """Validated results should be cached."""
        mock_fi = MagicMock()
        mock_fi.last_price = 100.00
        mock_ticker_cls.return_value.fast_info = mock_fi

        # First call – hits yfinance
        self.validator.validate("TSLA")
        # Second call – should use cache
        result = self.validator.validate("TSLA")
        log.info("TSLA cached validation: %s (yfinance called %d times)",
                 result, mock_ticker_cls.call_count)
        # yfinance should only be called once
        assert mock_ticker_cls.call_count == 1, "Should use cache on second call"
        assert result is True

    @patch("app.collectors.ticker_validator.yf.Ticker")
    def test_batch_validate(self, mock_ticker_cls: MagicMock) -> None:
        """Batch validation should filter correct results."""
        mock_fi = MagicMock()
        mock_fi.last_price = 50.0
        mock_ticker_cls.return_value.fast_info = mock_fi

        tickers = ["NVDA", "YOLO", "DD", "AAPL", "CEO", "GOOG"]
        valid = self.validator.validate_batch(tickers)
        log.info("Batch input: %s → valid: %s", tickers, valid)
        # YOLO, DD, CEO should be excluded by exclusion list
        assert "YOLO" not in valid
        assert "DD" not in valid
        assert "CEO" not in valid
        assert "NVDA" in valid
        assert "AAPL" in valid
        assert "GOOG" in valid


# ══════════════════════════════════════════════════════════════════
# 2. REDDIT COLLECTOR TESTS
# ══════════════════════════════════════════════════════════════════


class TestRedditCollector:
    """Tests for the Reddit scraping pipeline."""

    def setup_method(self) -> None:
        self.collector = RedditCollector()
        log.info("=== TestRedditCollector setup ===")

    def test_extract_tickers_basic(self) -> None:
        """Should extract uppercase 2-5 char words."""
        text = "I'm all in on $NVDA and TSLA, YOLO on this play"
        result = self.collector.extract_tickers(text)
        log.info("Input: '%s'", text)
        log.info("Extracted: %s", result)
        assert "NVDA" in result
        assert "TSLA" in result
        # YOLO should be filtered by exclusion list
        assert "YOLO" not in result

    def test_extract_tickers_dollar_sign(self) -> None:
        """$TICKER format should be recognized."""
        text = "Looking at $AAPL and $GOOG today"
        result = self.collector.extract_tickers(text)
        log.info("Input: '%s' → %s", text, result)
        assert "AAPL" in result
        assert "GOOG" in result

    def test_extract_tickers_empty(self) -> None:
        """Empty or no-ticker text should return empty list."""
        assert self.collector.extract_tickers("") == []
        assert self.collector.extract_tickers("no tickers here") == []
        log.info("Empty string and lowercase text both returned []")

    def test_extract_tickers_deduplicates(self) -> None:
        """Same ticker mentioned multiple times should appear once."""
        text = "NVDA is great, buy NVDA, NVDA to the moon"
        result = self.collector.extract_tickers(text)
        log.info("Input with 3x NVDA → %s", result)
        assert result.count("NVDA") == 1

    @patch("app.collectors.reddit_collector.requests.get")
    def test_fetch_subreddit_success(self, mock_get: MagicMock) -> None:
        """Successful subreddit fetch returns parsed posts."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "NVDA earnings blowout!",
                            "subreddit": "wallstreetbets",
                            "permalink": "/r/wallstreetbets/comments/abc123/nvda/",
                            "score": 500,
                            "selftext": "Full DD on NVDA",
                            "stickied": False,
                            "id": "abc123",
                        }
                    }
                ]
            }
        }
        mock_get.return_value = mock_response

        posts = self.collector._fetch_subreddit("wallstreetbets", "hot", 5)
        log.info("Fetched %d posts", len(posts))
        log.info("First post: %s", posts[0] if posts else "none")
        assert len(posts) == 1
        assert posts[0]["title"] == "NVDA earnings blowout!"

    @patch("app.collectors.reddit_collector.requests.get")
    def test_fetch_subreddit_rate_limit(self, mock_get: MagicMock) -> None:
        """Rate limited (429) should retry once."""
        first_response = MagicMock()
        first_response.status_code = 429

        second_response = MagicMock()
        second_response.status_code = 200
        second_response.json.return_value = {"data": {"children": []}}

        mock_get.side_effect = [first_response, second_response]

        posts = self.collector._fetch_subreddit("stocks", "hot", 5)
        log.info("Rate-limited fetch result: %d posts, %d calls",
                 len(posts), mock_get.call_count)
        assert mock_get.call_count == 2

    @patch("app.collectors.reddit_collector.requests.get")
    def test_get_thread_data(self, mock_get: MagicMock) -> None:
        """Thread scraping should extract title, body, and comments."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "data": {
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "title": "NVDA DD",
                                "selftext": "My analysis of NVDA...",
                            },
                        }
                    ]
                }
            },
            {
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {"body": "Great DD! $NVDA to $200"},
                        },
                        {
                            "kind": "t1",
                            "data": {"body": "Also bullish on TSLA"},
                        },
                    ]
                }
            },
        ]
        mock_get.return_value = mock_response

        title, body, comments = self.collector.get_thread_data(
            "/r/wallstreetbets/comments/abc123/"
        )
        log.info("Thread data: title='%s', body='%s', comments=%d",
                 title, body[:30], len(comments))
        assert title == "NVDA DD"
        assert "analysis" in body
        assert len(comments) == 2


# ══════════════════════════════════════════════════════════════════
# 3. YOUTUBE SCANNER TESTS
# ══════════════════════════════════════════════════════════════════


class TestTickerScanner:
    """Tests for the YouTube transcript scanner."""

    def setup_method(self) -> None:
        self.scanner = TickerScanner()
        log.info("=== TestTickerScanner setup ===")

    def test_extract_tickers(self) -> None:
        """Regex should find tickers in text."""
        text = "We're bullish on NVDA and TSLA. Also watch AAPL."
        result = self.scanner._extract_tickers(text)
        log.info("Extracted from '%s': %s", text[:40], result)
        assert "NVDA" in result
        assert "TSLA" in result
        assert "AAPL" in result

    def test_extract_tickers_filters_noise(self) -> None:
        """Exclusion list words should not appear."""
        text = "NVDA IS THE BEST AI CEO PICK FOR YOLO"
        result = self.scanner._extract_tickers(text)
        log.info("Filtered extraction from '%s': %s", text[:40], result)
        assert "NVDA" in result
        assert "BEST" not in result
        assert "YOLO" not in result
        assert "CEO" not in result

    def test_context_snippet(self) -> None:
        """Should return text window around first ticker mention."""
        text = "xxxx " * 20 + "NVDA is amazing" + " xxxx" * 20
        snippet = self.scanner._get_context_snippet(text, "NVDA")
        log.info("Snippet for NVDA: '%s'", snippet)
        assert "NVDA" in snippet
        assert len(snippet) < len(text)

    @patch("app.collectors.ticker_scanner.get_db")
    def test_scan_no_transcripts(self, mock_get_db: MagicMock) -> None:
        """No transcripts in DB should return empty list."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_get_db.return_value = mock_db

        result = self.scanner.scan_recent_transcripts(hours=24)
        log.info("No transcripts → %d results", len(result))
        assert result == []

    @patch("app.collectors.ticker_scanner.TickerValidator.validate_batch")
    @patch("app.collectors.ticker_scanner.get_db")
    def test_scan_with_transcript(
        self, mock_get_db: MagicMock, mock_validate: MagicMock
    ) -> None:
        """Should extract and score tickers from a transcript."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            (
                "NVDA",  # ticker column
                "video123",
                "NVDA Earnings Analysis",
                "FinanceChannel",
                "Buy NVDA because their GPU sales are incredible. "
                "NVDA revenue up 200%. Also look at TSLA.",
            ),
        ]
        mock_get_db.return_value = mock_db
        mock_validate.return_value = ["NVDA", "TSLA"]

        result = self.scanner.scan_recent_transcripts(hours=24)
        log.info("Scan result: %d tickers", len(result))
        for t in result:
            log.info("  $%s: %.0f pts — %s",
                     t.ticker, t.discovery_score,
                     t.context_snippets[0] if t.context_snippets else "no context")
        assert len(result) >= 1
        # NVDA should score higher (title mention + pipeline ticker + transcript)
        nvda = next((t for t in result if t.ticker == "NVDA"), None)
        assert nvda is not None
        assert nvda.discovery_score > 0


# ══════════════════════════════════════════════════════════════════
# 4. DISCOVERY SERVICE TESTS
# ══════════════════════════════════════════════════════════════════


class TestDiscoveryService:
    """Tests for the discovery orchestrator."""

    def test_merge_scores_basic(self) -> None:
        """Should combine scores for same ticker from different sources."""
        service = DiscoveryService()
        reddit = [
            ScoredTicker(ticker="NVDA", discovery_score=5.0, source="reddit"),
        ]
        youtube = [
            ScoredTicker(ticker="NVDA", discovery_score=3.0, source="youtube"),
            ScoredTicker(ticker="TSLA", discovery_score=2.0, source="youtube"),
        ]
        merged = service._merge_scores(reddit, youtube)
        log.info("Merge result:")
        for t in merged:
            log.info("  $%s: %.1f pts (source: %s)", t.ticker, t.discovery_score, t.source)

        nvda = next(t for t in merged if t.ticker == "NVDA")
        assert nvda.discovery_score == 8.0  # 5 + 3
        assert "reddit" in nvda.source and "youtube" in nvda.source

        tsla = next(t for t in merged if t.ticker == "TSLA")
        assert tsla.discovery_score == 2.0

    def test_merge_sorted_by_score(self) -> None:
        """Merged results should be sorted by score descending."""
        service = DiscoveryService()
        tickers = [
            ScoredTicker(ticker="AA", discovery_score=1.0, source="reddit"),
            ScoredTicker(ticker="BB", discovery_score=5.0, source="reddit"),
            ScoredTicker(ticker="CC", discovery_score=3.0, source="reddit"),
        ]
        merged = service._merge_scores(tickers, [])
        log.info("Sorted: %s", [f"{t.ticker}={t.discovery_score}" for t in merged])
        assert merged[0].ticker == "BB"
        assert merged[1].ticker == "CC"
        assert merged[2].ticker == "AA"

    @patch("app.services.discovery_service.get_db")
    def test_save_to_db_inserts(self, mock_get_db: MagicMock) -> None:
        """Should call DuckDB insert for each ticker."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None  # No existing record
        mock_get_db.return_value = mock_db

        service = DiscoveryService()
        tickers = [
            ScoredTicker(ticker="NVDA", discovery_score=10.0, source="reddit"),
        ]
        service._save_to_db(tickers)

        # Should have called execute at least twice (insert discovered + insert score)
        call_count = mock_db.execute.call_count
        log.info("DuckDB execute called %d times", call_count)
        assert call_count >= 2, "Should insert into both tables"


# ══════════════════════════════════════════════════════════════════
# 5. MODEL TESTS
# ══════════════════════════════════════════════════════════════════


class TestDiscoveryModels:
    """Tests for the Pydantic discovery models."""

    def test_scored_ticker_defaults(self) -> None:
        """ScoredTicker should have sensible defaults."""
        t = ScoredTicker(ticker="NVDA")
        log.info("ScoredTicker defaults: score=%.1f, source=%s, sentiment=%s",
                 t.discovery_score, t.source, t.sentiment_hint)
        assert t.discovery_score == 0.0
        assert t.source == "reddit"
        assert t.sentiment_hint == "neutral"
        assert t.context_snippets == []

    def test_discovery_result_defaults(self) -> None:
        """DiscoveryResult should have sensible defaults."""
        r = DiscoveryResult()
        log.info("DiscoveryResult defaults: tickers=%d, reddit=%d, youtube=%d",
                 len(r.tickers), r.reddit_count, r.youtube_count)
        assert r.tickers == []
        assert r.reddit_count == 0
        assert r.youtube_count == 0
        assert r.duration_seconds == 0.0

    def test_scored_ticker_serialization(self) -> None:
        """ScoredTicker should serialize to dict properly."""
        t = ScoredTicker(
            ticker="NVDA",
            discovery_score=15.5,
            source="reddit",
            sentiment_hint="bullish",
            context_snippets=["Great earnings report"],
        )
        d = t.model_dump()
        log.info("Serialized: %s", d)
        assert d["ticker"] == "NVDA"
        assert d["discovery_score"] == 15.5
        assert d["sentiment_hint"] == "bullish"
