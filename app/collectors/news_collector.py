"""News collector — fetches financial news from yfinance, Google News RSS,
and SEC EDGAR filings.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser

from app.database import get_db
from app.models.market_data import NewsArticle
from app.utils.logger import logger


class NewsCollector:
    """Collects news articles for a ticker from multiple sources."""

    # Google News RSS base URL
    GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    # SEC EDGAR full-text search RSS
    SEC_EDGAR_RSS = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt={start}&enddt={end}&forms=10-K,10-Q,8-K&from=0&size=10"

    async def collect(self, ticker: str, limit: int = 30) -> list[NewsArticle]:
        """Fetch news from all sources and persist to DuckDB.

        Sources: yfinance, Google News RSS, SEC EDGAR RSS.
        """
        logger.info("Collecting news for %s from all sources", ticker)

        articles: list[NewsArticle] = []

        # Source 1: yfinance built-in news
        articles.extend(await self._fetch_yfinance(ticker, limit))

        # Source 2: Google News RSS
        articles.extend(await self._fetch_google_news(ticker, limit))

        # Source 3: SEC EDGAR RSS (filings only)
        articles.extend(await self._fetch_sec_edgar(ticker, limit=5))

        if not articles:
            logger.warning("No news articles collected for %s", ticker)
            return []

        # Persist (deduped by article_hash)
        db = get_db()
        new_count = 0
        for a in articles:
            existing = db.execute(
                "SELECT 1 FROM news_articles WHERE ticker = ? AND article_hash = ?",
                [a.ticker, a.article_hash],
            ).fetchone()
            if not existing:
                db.execute(
                    """
                    INSERT INTO news_articles
                        (ticker, article_hash, title, publisher, url,
                         published_at, summary, thumbnail_url, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [a.ticker, a.article_hash, a.title, a.publisher, a.url,
                     a.published_at, a.summary, a.thumbnail_url, a.source],
                )
                new_count += 1

        logger.info(
            "Collected %d articles for %s (%d new)", len(articles), ticker, new_count
        )
        return articles

    async def get_all_historical(
        self, ticker: str, limit: int = 200
    ) -> list[NewsArticle]:
        """Retrieve ALL stored news articles for a ticker from the database.

        Returns the full accumulated history — agents receive every article
        ever collected, not just the latest scrape.

        Args:
            ticker: Stock ticker symbol
            limit: Max articles to return (most recent first)
        """
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, article_hash, title, publisher, url,
                   published_at, summary, thumbnail_url, source
            FROM news_articles
            WHERE ticker = ?
            ORDER BY published_at DESC NULLS LAST
            LIMIT ?
            """,
            [ticker, limit],
        ).fetchall()

        articles = [
            NewsArticle(
                ticker=r[0],
                article_hash=r[1],
                title=r[2],
                publisher=r[3],
                url=r[4],
                published_at=r[5],
                summary=r[6],
                thumbnail_url=r[7] or "",
                source=r[8] or "yfinance",
            )
            for r in rows
        ]

        logger.info(
            "Retrieved %d historical news articles for %s from DB",
            len(articles),
            ticker,
        )
        return articles

    # ------------------------------------------------------------------
    # Source 1: yfinance
    # ------------------------------------------------------------------
    async def _fetch_yfinance(self, ticker: str, limit: int) -> list[NewsArticle]:
        """Fetch news from yfinance .news property."""
        articles: list[NewsArticle] = []
        try:
            import yfinance as yf

            t = yf.Ticker(ticker)
            news_list = t.news or []

            for item in news_list[:limit]:
                title = item.get("title", "").strip()
                if not title:
                    continue

                publisher = item.get("publisher", "")
                link = item.get("link", "") or item.get("url", "")
                pub_ts = item.get("providerPublishTime")
                thumbnail = ""

                # Extract thumbnail if available
                thumbs = item.get("thumbnail", {})
                if isinstance(thumbs, dict):
                    resolutions = thumbs.get("resolutions", [])
                    if resolutions:
                        thumbnail = resolutions[0].get("url", "")

                # Parse publish time
                published_at = None
                if pub_ts:
                    try:
                        if isinstance(pub_ts, int | float):
                            published_at = datetime.fromtimestamp(
                                pub_ts, tz=timezone.utc
                            )
                        else:
                            published_at = datetime.fromisoformat(str(pub_ts))
                    except (ValueError, OSError):
                        pass

                # Create hash for dedup
                article_hash = hashlib.md5(
                    f"{title}|{publisher}".encode()
                ).hexdigest()

                articles.append(
                    NewsArticle(
                        ticker=ticker,
                        article_hash=article_hash,
                        title=title,
                        publisher=publisher,
                        url=link,
                        published_at=published_at,
                        summary=item.get("summary", ""),
                        thumbnail_url=thumbnail,
                        source="yfinance",
                    )
                )

        except Exception as e:
            logger.error("yfinance news fetch failed for %s: %s", ticker, e)

        logger.info("yfinance: %d articles for %s", len(articles), ticker)
        return articles

    # ------------------------------------------------------------------
    # Source 2: Google News RSS
    # ------------------------------------------------------------------
    async def _fetch_google_news(self, ticker: str, limit: int) -> list[NewsArticle]:
        """Fetch financial news from Google News RSS feed."""
        articles: list[NewsArticle] = []
        try:
            query = f"{ticker} stock market"
            url = self.GOOGLE_NEWS_RSS.format(query=quote_plus(query))
            feed = feedparser.parse(url)

            for entry in feed.entries[:limit]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                link = entry.get("link", "")
                publisher = entry.get("source", {}).get("title", "Google News")
                summary = entry.get("summary", "")

                # Parse publish time
                published_at = None
                pub_parsed = entry.get("published_parsed")
                if pub_parsed:
                    try:
                        published_at = datetime(
                            *pub_parsed[:6], tzinfo=timezone.utc
                        )
                    except (TypeError, ValueError):
                        pass

                article_hash = hashlib.md5(
                    f"gnews|{title}|{publisher}".encode()
                ).hexdigest()

                articles.append(
                    NewsArticle(
                        ticker=ticker,
                        article_hash=article_hash,
                        title=title,
                        publisher=publisher,
                        url=link,
                        published_at=published_at,
                        summary=summary,
                        source="google_news",
                    )
                )

        except Exception as e:
            logger.error("Google News RSS failed for %s: %s", ticker, e)

        logger.info("Google News: %d articles for %s", len(articles), ticker)
        return articles

    # ------------------------------------------------------------------
    # Source 3: SEC EDGAR RSS (filings)
    # ------------------------------------------------------------------
    async def _fetch_sec_edgar(self, ticker: str, limit: int = 5) -> list[NewsArticle]:
        """Fetch recent SEC filings from EDGAR full-text search (EFTS API)."""
        import requests as req

        articles: list[NewsArticle] = []
        try:
            # EFTS full-text search API (modern, replaces cgi-bin)
            url = (
                f"https://efts.sec.gov/LATEST/search-index"
                f"?q=%22{quote_plus(ticker)}%22"
                f"&forms=10-K,10-Q,8-K"
                f"&from=0&size={limit}"
            )
            headers = {
                "User-Agent": "LazyTradingBot/1.0 trading-bot@example.com",
                "Accept": "application/json",
            }

            resp = req.get(url, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])

                for hit in hits[:limit]:
                    source = hit.get("_source", {})
                    title = source.get("display_names", [f"{ticker} SEC Filing"])[0] if source.get("display_names") else source.get("file_description", f"{ticker} SEC Filing")
                    form_type = source.get("form_type", "")
                    filed = source.get("file_date", "")
                    accession = source.get("accession_no", "").replace("-", "")

                    link = f"https://www.sec.gov/Archives/edgar/data/{accession}" if accession else ""
                    summary = f"Form {form_type}" if form_type else ""

                    published_at = None
                    if filed:
                        try:
                            published_at = datetime.strptime(filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass

                    article_hash = hashlib.md5(
                        f"sec|{form_type}|{filed}|{ticker}".encode()
                    ).hexdigest()

                    articles.append(
                        NewsArticle(
                            ticker=ticker,
                            article_hash=article_hash,
                            title=f"{ticker} {form_type}: {title}" if form_type else title,
                            publisher="SEC EDGAR",
                            url=link,
                            published_at=published_at,
                            summary=summary,
                            source="sec_edgar",
                        )
                    )
            else:
                logger.warning(
                    "SEC EDGAR API returned %d for %s", resp.status_code, ticker
                )

        except Exception as e:
            logger.error("SEC EDGAR fetch failed for %s: %s", ticker, e)

        logger.info("SEC EDGAR: %d filings for %s", len(articles), ticker)
        return articles

    # ------------------------------------------------------------------
    # Public: get recent stored articles
    # ------------------------------------------------------------------
    async def get_recent(self, ticker: str, limit: int = 30) -> list[NewsArticle]:
        """Retrieve the most recent stored articles for a ticker."""
        db = get_db()
        rows = db.execute(
            """
            SELECT ticker, article_hash, title, publisher, url,
                   published_at, summary, thumbnail_url,
                   COALESCE(source, 'yfinance') as source
            FROM news_articles
            WHERE ticker = ?
            ORDER BY published_at DESC NULLS LAST
            LIMIT ?
            """,
            [ticker, limit],
        ).fetchall()

        return [
            NewsArticle(
                ticker=r[0],
                article_hash=r[1],
                title=r[2],
                publisher=r[3],
                url=r[4],
                published_at=r[5],
                summary=r[6],
                thumbnail_url=r[7],
                source=r[8],
            )
            for r in rows
        ]
