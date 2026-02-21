"""RSS News Collector — scrapes full article content via RSS + newspaper3k.

Fetches financial news article headlines from RSS feeds,
then uses newspaper3k to extract full article body text.

Data sources (free, no auth):
    - Reuters Business & Markets
    - CNBC Markets
    - MarketWatch Top Stories
    - Yahoo Finance
    - Seeking Alpha Market Currents

Rate limit: 1s between article extractions (respectful crawling).
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any

import feedparser

from app.database import get_db
from app.models.discovery import ScoredTicker
from app.utils.logger import logger

# ── RSS Feed Sources ─────────────────────────────────────────────
RSS_FEEDS: list[dict[str, str]] = [
    {
        "name": "reuters_business",
        "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    },
    {
        "name": "cnbc_markets",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    },
    {
        "name": "marketwatch_top",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories",
    },
    {
        "name": "yahoo_finance",
        "url": "https://finance.yahoo.com/news/rssindex",
    },
    {
        "name": "seeking_alpha",
        "url": "https://seekingalpha.com/market_currents.xml",
    },
    {
        "name": "google_news_finance",
        "url": "https://news.google.com/rss/search?q=stock+market+today&hl=en-US&gl=US&ceid=US:en",
    },
]

RATE_LIMIT_SECS = 1.0
MAX_ARTICLES_PER_FEED = 5
MIN_CONTENT_LENGTH = 200  # Skip articles with less content


class RSSNewsCollector:
    """Collects full news articles from financial RSS feeds using newspaper3k."""

    def __init__(self) -> None:
        self._newspaper_available: bool | None = None

    # ── Public: Discovery integration ────────────────────────────

    async def scrape_all_feeds(self) -> list[dict[str, Any]]:
        """Scrape all RSS feeds, extract full articles, persist to DB.

        Called during Discovery and/or per-ticker pipeline phase.
        Returns list of article dicts with full content.
        """
        db = get_db()

        # Daily guard: skip if we already scraped today
        row = db.execute(
            "SELECT COUNT(*) FROM news_full_articles "
            "WHERE collected_at >= CURRENT_DATE"
        ).fetchone()
        if row and row[0] > 0:
            logger.info(
                "[RSS News] Already collected today (%d articles), using cache",
                row[0],
            )
            return self._get_recent_from_db()

        logger.info("[RSS News] Starting full-article collection from %d feeds", len(RSS_FEEDS))

        all_articles: list[dict[str, Any]] = []
        for feed_config in RSS_FEEDS:
            try:
                articles = self._scrape_feed(feed_config, db)
                all_articles.extend(articles)
                logger.info(
                    "[RSS News] %s: %d articles extracted",
                    feed_config["name"], len(articles),
                )
            except Exception as e:
                logger.error(
                    "[RSS News] Feed %s failed: %s", feed_config["name"], e,
                )

        logger.info("[RSS News] Total: %d articles with full content", len(all_articles))
        return all_articles

    async def get_articles_for_ticker(self, ticker: str) -> list[dict[str, Any]]:
        """Get articles mentioning a specific ticker (pipeline step).

        Returns list of dicts with full article content.
        """
        db = get_db()
        rows = db.execute(
            """
            SELECT title, url, publisher, published_at, summary,
                   content, content_length, source_feed
            FROM news_full_articles
            WHERE tickers_found LIKE ?
            ORDER BY published_at DESC NULLS LAST
            LIMIT 10
            """,
            [f"%{ticker}%"],
        ).fetchall()

        return [
            {
                "title": r[0],
                "url": r[1],
                "publisher": r[2],
                "published_at": str(r[3]) if r[3] else None,
                "summary": r[4],
                "content": r[5],
                "content_length": r[6],
                "source_feed": r[7],
            }
            for r in rows
        ]

    async def get_discovery_tickers(self) -> list[ScoredTicker]:
        """Extract tickers from recent articles for discovery scoring."""
        db = get_db()
        rows = db.execute(
            """
            SELECT tickers_found
            FROM news_full_articles
            WHERE tickers_found != ''
              AND collected_at >= CURRENT_DATE - INTERVAL '7 days'
            """,
        ).fetchall()

        # Count ticker mentions across all articles
        ticker_counts: dict[str, int] = {}
        for (tickers_str,) in rows:
            for ticker in tickers_str.split(","):
                ticker = ticker.strip().upper()
                if ticker and len(ticker) <= 5:
                    ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

        tickers: list[ScoredTicker] = []
        for ticker, count in sorted(
            ticker_counts.items(), key=lambda x: x[1], reverse=True,
        )[:30]:
            tickers.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=float(count) * 1.0,
                    source="reddit",  # Use "reddit" as fallback since Literal is limited
                    source_detail=f"Mentioned in {count} news articles",
                    sentiment_hint="neutral",
                    context_snippets=[
                        f"Found in {count} recent financial news articles"
                    ],
                )
            )

        logger.info("[RSS News] Generated %d discovery tickers from articles", len(tickers))
        return tickers

    # ── Private: feed scraping ───────────────────────────────────

    def _scrape_feed(
        self, feed_config: dict[str, str], db: Any,
    ) -> list[dict[str, Any]]:
        """Scrape a single RSS feed and extract full articles."""
        feed_name = feed_config["name"]
        feed_url = feed_config["url"]

        logger.info("[RSS News] Parsing feed: %s", feed_name)

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.error("[RSS News] feedparser failed for %s: %s", feed_name, e)
            return []

        if not feed.entries:
            logger.info("[RSS News] No entries in feed: %s", feed_name)
            return []

        articles: list[dict[str, Any]] = []

        for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            if not title or not url:
                continue

            # Generate hash for dedup
            article_hash = hashlib.md5(
                f"{feed_name}|{title}|{url}".encode()
            ).hexdigest()[:16]

            # Skip if already in DB
            existing = db.execute(
                "SELECT 1 FROM news_full_articles WHERE article_hash = ?",
                [article_hash],
            ).fetchone()
            if existing:
                continue

            # Parse publish time
            published_at = None
            pub_parsed = entry.get("published_parsed")
            if pub_parsed:
                try:
                    published_at = datetime(*pub_parsed[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass

            publisher = entry.get("source", {}).get("title", "")
            if not publisher:
                publisher = feed_name.replace("_", " ").title()

            summary = entry.get("summary", "")
            # Strip HTML from summary
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

            # Extract full article content using newspaper3k
            content = self._extract_article_content(url)
            if not content or len(content) < MIN_CONTENT_LENGTH:
                logger.debug(
                    "[RSS News] Skipping %s (content too short: %d chars)",
                    title[:40], len(content) if content else 0,
                )
                continue

            # Find ticker mentions in full content
            tickers_found = self._extract_tickers_from_text(content)

            article = {
                "article_hash": article_hash,
                "title": title,
                "url": url,
                "publisher": publisher,
                "published_at": published_at,
                "summary": summary,
                "content": content,
                "content_length": len(content),
                "tickers_found": ",".join(tickers_found),
                "source_feed": feed_name,
            }

            # Persist to DB
            try:
                db.execute(
                    """
                    INSERT INTO news_full_articles
                        (article_hash, title, url, publisher, published_at,
                         summary, content, content_length, tickers_found, source_feed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (article_hash) DO NOTHING
                    """,
                    [
                        article["article_hash"],
                        article["title"],
                        article["url"],
                        article["publisher"],
                        article["published_at"],
                        article["summary"],
                        article["content"],
                        article["content_length"],
                        article["tickers_found"],
                        article["source_feed"],
                    ],
                )
            except Exception as e:
                logger.debug("[RSS News] Insert failed: %s", e)

            articles.append(article)

        return articles

    def _extract_article_content(self, url: str) -> str:
        """Use newspaper3k to extract full article text from URL."""
        if self._newspaper_available is None:
            try:
                import newspaper  # noqa: F401
                self._newspaper_available = True
            except ImportError:
                logger.warning(
                    "[RSS News] newspaper3k not installed — pip install newspaper3k"
                )
                self._newspaper_available = False

        if not self._newspaper_available:
            return ""

        time.sleep(RATE_LIMIT_SECS)

        try:
            from newspaper import Article

            article = Article(url)
            article.download()
            article.parse()

            content = article.text.strip()
            if content:
                logger.debug(
                    "[RSS News] Extracted %d chars from %s",
                    len(content), url[:60],
                )
            return content

        except Exception as e:
            logger.debug("[RSS News] Article extraction failed for %s: %s", url[:60], e)
            return ""

    def _extract_tickers_from_text(self, text: str) -> list[str]:
        """Find stock ticker patterns in article text.

        Looks for $TICKER notation and standalone uppercase 2-5 char words
        that could be tickers. Filters out common false positives.
        """
        # Common false positives to exclude
        exclusions = {
            "THE", "AND", "FOR", "BUT", "NOT", "ARE", "WAS", "HAS", "HAD",
            "WITH", "THIS", "THAT", "FROM", "WILL", "HAVE", "BEEN", "ALSO",
            "MORE", "OVER", "INTO", "JUST", "THAN", "THEM", "EACH", "MAKE",
            "LIKE", "VERY", "WHEN", "WHAT", "YOUR", "SOME", "THEN", "ITS",
            "ALL", "NEW", "NOW", "WAY", "MAY", "SAY", "SHE", "HER", "HIS",
            "HOW", "TOP", "BIG", "OLD", "FAR", "ONE", "TWO", "CEO", "SEC",
            "IPO", "GDP", "CPI", "FED", "NYSE", "ETF", "AI", "US", "USA",
            "UK", "EU", "CEO", "CFO", "CTO", "COO", "LLC", "INC", "CORP",
        }

        # Find $TICKER patterns (most reliable)
        dollar_tickers = set(re.findall(r"\$([A-Z]{2,5})\b", text))

        # Find standalone uppercase words that look like tickers
        # Only take words that appear near financial context
        word_tickers: set[str] = set()
        words = re.findall(r"\b([A-Z]{2,5})\b", text)
        for word in words:
            if word not in exclusions and len(word) >= 2:
                word_tickers.add(word)

        # Combine but prioritize explicitly marked ones
        all_tickers = dollar_tickers | word_tickers
        # Limit to top 10 most likely
        return sorted(all_tickers)[:10]

    def _get_recent_from_db(self) -> list[dict[str, Any]]:
        """Get recently collected articles from DB."""
        db = get_db()
        rows = db.execute(
            """
            SELECT title, url, publisher, published_at, summary,
                   content, content_length, tickers_found, source_feed
            FROM news_full_articles
            WHERE collected_at >= CURRENT_DATE
            ORDER BY published_at DESC NULLS LAST
            LIMIT 50
            """,
        ).fetchall()

        return [
            {
                "title": r[0],
                "url": r[1],
                "publisher": r[2],
                "published_at": str(r[3]) if r[3] else None,
                "summary": r[4],
                "content": r[5],
                "content_length": r[6],
                "tickers_found": r[7],
                "source_feed": r[8],
            }
            for r in rows
        ]
