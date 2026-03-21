"""Finnhub Collector — real-time quotes, earnings surprises, insider sentiment,
analyst recommendations, and company news from Finnhub.

Fills gaps that yFinance / OpenBB don't cover well:
  • Real-time quotes (sub-second via REST, WebSocket planned)
  • Earnings surprise history (EPS beat/miss %, revenue surprise)
  • Aggregated insider sentiment (MSPR score)
  • Granular analyst recommendation changes over time
  • Category-tagged company news with sentiment

Rate limit: Free tier = 60 calls/min. We use a 1s delay between calls.
Auth: API key via settings.FINNHUB_API_KEY.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.config import settings
from app.database import get_db
from app.services.unified_logger import track_class_telemetry
from app.utils.logger import logger

# Lazy-init to avoid import-time errors if key is missing
_client = None


def _get_client():
    """Lazy-load the Finnhub client."""
    global _client
    if _client is None:
        import finnhub

        api_key = settings.FINNHUB_API_KEY
        if not api_key:
            raise RuntimeError(
                "FINNHUB_API_KEY not set. Add it to .env or config."
            )
        _client = finnhub.Client(api_key=api_key)
        logger.info("[Finnhub] Client initialized")
    return _client


def _run_sync(fn, *args, **kwargs):
    """Run a synchronous Finnhub call in a thread to avoid blocking asyncio."""
    import functools

    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


RATE_LIMIT_SECS = 1.0  # 60 calls/min free tier


@track_class_telemetry
class FinnhubCollector:
    """Collects supplemental data from Finnhub that other sources lack."""

    # ── Real-time Quote ─────────────────────────────────────────────

    async def get_quote(self, ticker: str) -> dict[str, Any] | None:
        """Fetch real-time quote (current price, change, high/low).

        No daily guard — quotes are always fresh.
        """
        try:
            client = _get_client()
            data = await _run_sync(client.quote, ticker)
            if not data or data.get("c", 0) == 0:
                logger.warning("[Finnhub] No quote data for %s", ticker)
                return None

            quote = {
                "ticker": ticker,
                "current_price": data.get("c", 0),
                "change": data.get("d", 0),
                "change_pct": data.get("dp", 0),
                "high": data.get("h", 0),
                "low": data.get("l", 0),
                "open": data.get("o", 0),
                "prev_close": data.get("pc", 0),
                "timestamp": datetime.fromtimestamp(data.get("t", 0)),
            }
            logger.info(
                "[Finnhub] Quote for %s: $%.2f (%+.2f%%)",
                ticker,
                quote["current_price"],
                quote["change_pct"],
            )
            return quote
        except Exception as e:
            logger.warning("[Finnhub] Quote failed for %s: %s", ticker, e)
            return None

    # ── Recommendation Trends ───────────────────────────────────────

    async def get_recommendation_trends(
        self, ticker: str,
    ) -> list[dict[str, Any]]:
        """Fetch analyst recommendation trends over time.

        Returns monthly snapshots of buy/sell/hold/strongBuy/strongSell counts.
        Daily guard: skip if already collected today.
        """
        db = get_db()
        today = date.today()

        # Daily guard
        existing = db.execute(
            "SELECT COUNT(*) FROM finnhub_recommendations "
            "WHERE ticker = ? AND collected_date = ?",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info(
                "[Finnhub] Recommendations for %s already collected today",
                ticker,
            )
            return self._get_stored_recommendations(ticker)

        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            data = await _run_sync(client.recommendation_trends, ticker)
            if not data:
                return []

            rows = []
            for item in data[:12]:  # Last 12 months
                row = {
                    "ticker": ticker,
                    "period": item.get("period", ""),
                    "strong_buy": item.get("strongBuy", 0),
                    "buy": item.get("buy", 0),
                    "hold": item.get("hold", 0),
                    "sell": item.get("sell", 0),
                    "strong_sell": item.get("strongSell", 0),
                }
                rows.append(row)

            # Persist
            self._save_recommendations(db, ticker, rows, today)
            logger.info(
                "[Finnhub] Saved %d recommendation snapshots for %s",
                len(rows),
                ticker,
            )
            return rows

        except Exception as e:
            logger.warning(
                "[Finnhub] Recommendations failed for %s: %s", ticker, e,
            )
            return []

    # ── Earnings Surprises ──────────────────────────────────────────

    async def get_earnings_surprises(
        self, ticker: str,
    ) -> list[dict[str, Any]]:
        """Fetch historical earnings surprise data (EPS beat/miss).

        Returns quarterly actual vs estimate EPS with surprise percentage.
        Daily guard: skip if already collected today.
        """
        db = get_db()
        today = date.today()

        existing = db.execute(
            "SELECT COUNT(*) FROM finnhub_earnings "
            "WHERE ticker = ? AND collected_date = ?",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info(
                "[Finnhub] Earnings for %s already collected today", ticker,
            )
            return self._get_stored_earnings(ticker)

        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            data = await _run_sync(client.company_earnings, ticker, limit=8)
            if not data:
                return []

            rows = []
            for item in data:
                actual = item.get("actual", 0) or 0
                estimate = item.get("estimate", 0) or 0
                surprise = item.get("surprise", 0) or 0
                surprise_pct = item.get("surprisePercent", 0) or 0

                row = {
                    "ticker": ticker,
                    "period": item.get("period", ""),
                    "actual_eps": actual,
                    "estimate_eps": estimate,
                    "surprise": surprise,
                    "surprise_pct": surprise_pct,
                    "quarter": item.get("quarter", 0),
                    "year": item.get("year", 0),
                }
                rows.append(row)

            self._save_earnings(db, ticker, rows, today)
            logger.info(
                "[Finnhub] Saved %d earnings surprises for %s",
                len(rows),
                ticker,
            )
            return rows

        except Exception as e:
            logger.warning(
                "[Finnhub] Earnings failed for %s: %s", ticker, e,
            )
            return []

    # ── Insider Sentiment (MSPR) ────────────────────────────────────

    async def get_insider_sentiment(
        self, ticker: str,
    ) -> dict[str, Any] | None:
        """Fetch aggregated insider sentiment (MSPR score).

        MSPR = Monthly Share Purchase Ratio.
        Positive = net buying, Negative = net selling.
        Daily guard.
        """
        db = get_db()
        today = date.today()

        existing = db.execute(
            "SELECT COUNT(*) FROM finnhub_insider_sentiment "
            "WHERE ticker = ? AND collected_date = ?",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info(
                "[Finnhub] Insider sentiment for %s already collected today",
                ticker,
            )
            return self._get_stored_insider_sentiment(ticker)

        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            from_date = (datetime.now() - timedelta(days=90)).strftime(
                "%Y-%m-%d",
            )
            to_date = datetime.now().strftime("%Y-%m-%d")
            data = await _run_sync(
                client.stock_insider_sentiment,
                ticker,
                from_date,
                to_date,
            )
            if not data or not data.get("data"):
                return None

            # Aggregate all months
            total_mspr = 0.0
            total_change = 0.0
            months = data.get("data", [])
            for month in months:
                total_mspr += month.get("mspr", 0)
                total_change += month.get("change", 0)

            avg_mspr = total_mspr / len(months) if months else 0

            result = {
                "ticker": ticker,
                "symbol": data.get("symbol", ticker),
                "avg_mspr": round(avg_mspr, 4),
                "total_change": total_change,
                "months_tracked": len(months),
                "sentiment": (
                    "bullish"
                    if avg_mspr > 0
                    else "bearish" if avg_mspr < 0 else "neutral"
                ),
            }

            self._save_insider_sentiment(db, ticker, result, today)
            logger.info(
                "[Finnhub] Insider sentiment for %s: MSPR=%.4f (%s)",
                ticker,
                avg_mspr,
                result["sentiment"],
            )
            return result

        except Exception as e:
            logger.warning(
                "[Finnhub] Insider sentiment failed for %s: %s", ticker, e,
            )
            return None

    # ── Company News ────────────────────────────────────────────────

    async def get_company_news(
        self, ticker: str, days_back: int = 7,
    ) -> list[dict[str, Any]]:
        """Fetch category-tagged company news.

        Finnhub news includes category, sentiment, and related tickers.
        Daily guard.
        """
        db = get_db()
        today = date.today()

        existing = db.execute(
            "SELECT COUNT(*) FROM finnhub_news "
            "WHERE ticker = ? AND collected_date = ?",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info(
                "[Finnhub] News for %s already collected today", ticker,
            )
            return self._get_stored_news(ticker)

        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            from_date = (
                datetime.now() - timedelta(days=days_back)
            ).strftime("%Y-%m-%d")
            to_date = datetime.now().strftime("%Y-%m-%d")

            data = await _run_sync(
                client.company_news, ticker, _from=from_date, to=to_date,
            )
            if not data:
                return []

            articles = []
            for item in data[:30]:  # Cap at 30 articles
                article = {
                    "ticker": ticker,
                    "headline": item.get("headline", ""),
                    "summary": item.get("summary", ""),
                    "source": item.get("source", ""),
                    "url": item.get("url", ""),
                    "category": item.get("category", ""),
                    "related": item.get("related", ""),
                    "datetime": datetime.fromtimestamp(
                        item.get("datetime", 0),
                    ),
                    "image": item.get("image", ""),
                }
                articles.append(article)

            self._save_news(db, ticker, articles, today)
            logger.info(
                "[Finnhub] Saved %d news articles for %s",
                len(articles),
                ticker,
            )
            return articles

        except Exception as e:
            logger.warning(
                "[Finnhub] News failed for %s: %s", ticker, e,
            )
            return []

    # ── Peers ───────────────────────────────────────────────────────

    async def get_peers(self, ticker: str) -> list[str]:
        """Fetch similar/peer companies for a ticker."""
        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            data = await _run_sync(client.company_peers, ticker)
            peers = [p for p in (data or []) if p != ticker]
            logger.info("[Finnhub] %d peers for %s", len(peers), ticker)
            return peers
        except Exception as e:
            logger.warning("[Finnhub] Peers failed for %s: %s", ticker, e)
            return []

    # ── Basic Financials (52w, beta, volume) ────────────────────────

    async def get_basic_financials(
        self, ticker: str,
    ) -> dict[str, Any] | None:
        """Fetch basic financial metrics: 52-week high/low, beta, avg volume.

        These complement yFinance fundamentals without duplicating them.
        """
        try:
            client = _get_client()
            time.sleep(RATE_LIMIT_SECS)
            data = await _run_sync(
                client.company_basic_financials, ticker, "all",
            )
            if not data or not data.get("metric"):
                return None

            m = data["metric"]
            result = {
                "ticker": ticker,
                "52_week_high": m.get("52WeekHigh", 0),
                "52_week_low": m.get("52WeekLow", 0),
                "52_week_high_date": m.get("52WeekHighDate", ""),
                "52_week_low_date": m.get("52WeekLowDate", ""),
                "beta": m.get("beta", 0),
                "10d_avg_volume": m.get("10DayAverageTradingVolume", 0),
                "3m_avg_volume": m.get("3MonthAverageTradingVolume", 0),
                "market_cap": m.get("marketCapitalization", 0),
                "dividend_yield_ttm": m.get(
                    "dividendYieldIndicatedAnnual", 0,
                ),
                "pe_annual": m.get("peBasicExclExtraTTM", 0),
                "pb_annual": m.get("pbAnnual", 0),
                "ps_annual": m.get("psAnnual", 0),
                "revenue_per_share_annual": m.get(
                    "revenuePerShareAnnual", 0,
                ),
                "roe_ttm": m.get("roeTTM", 0),
                "roi_ttm": m.get("roiTTM", 0),
            }
            logger.info(
                "[Finnhub] Basic financials for %s: 52w=%s-%s, beta=%.2f",
                ticker,
                result["52_week_low"],
                result["52_week_high"],
                result["beta"] or 0,
            )
            return result

        except Exception as e:
            logger.warning(
                "[Finnhub] Basic financials failed for %s: %s", ticker, e,
            )
            return None

    # ── Convenience: collect all for a ticker ────────────────────────

    async def collect_all(self, ticker: str) -> dict[str, Any]:
        """Run all Finnhub methods in parallel for one ticker."""
        logger.info("[Finnhub] Collecting ALL data for %s", ticker)
        t0 = datetime.now()

        tasks = {
            "quote": self.get_quote(ticker),
            "recommendations": self.get_recommendation_trends(ticker),
            "earnings": self.get_earnings_surprises(ticker),
            "insider_sentiment": self.get_insider_sentiment(ticker),
            "news": self.get_company_news(ticker),
            "peers": self.get_peers(ticker),
            "basic_financials": self.get_basic_financials(ticker),
        }

        results = {}
        gathered = await asyncio.gather(
            *[self._named_task(k, v) for k, v in tasks.items()],
            return_exceptions=True,
        )

        ok_count = 0
        for name, data in gathered:
            if isinstance(data, Exception):
                logger.warning("[Finnhub] collect_all %s failed: %s", name, data)
                results[name] = None
            else:
                results[name] = data
                if data is not None and data != []:
                    ok_count += 1

        elapsed = (datetime.now() - t0).total_seconds()
        logger.info(
            "[Finnhub] Collected %d/%d sources for %s in %.1fs",
            ok_count,
            len(tasks),
            ticker,
            elapsed,
        )
        return results

    @staticmethod
    async def _named_task(name: str, coro):
        """Wrap a coroutine to return (name, result)."""
        try:
            result = await coro
            return (name, result)
        except Exception as e:
            return (name, e)

    # ── DB Persistence Helpers ──────────────────────────────────────

    def _save_recommendations(
        self,
        db: Any,
        ticker: str,
        rows: list[dict],
        collected_date: date,
    ) -> None:
        for row in rows:
            try:
                rid = hashlib.md5(
                    f"{ticker}_{row['period']}".encode(),
                ).hexdigest()[:16]
                db.execute(
                    """
                    INSERT INTO finnhub_recommendations
                        (id, ticker, period, strong_buy, buy, hold, sell,
                         strong_sell, collected_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        strong_buy = EXCLUDED.strong_buy,
                        buy = EXCLUDED.buy,
                        hold = EXCLUDED.hold,
                        sell = EXCLUDED.sell,
                        strong_sell = EXCLUDED.strong_sell,
                        collected_date = EXCLUDED.collected_date
                    """,
                    [
                        rid,
                        ticker,
                        row["period"],
                        row["strong_buy"],
                        row["buy"],
                        row["hold"],
                        row["sell"],
                        row["strong_sell"],
                        collected_date,
                    ],
                )
            except Exception as e:
                logger.debug(
                    "[Finnhub] Recommendation insert failed: %s", e,
                )

    def _save_earnings(
        self,
        db: Any,
        ticker: str,
        rows: list[dict],
        collected_date: date,
    ) -> None:
        for row in rows:
            try:
                eid = hashlib.md5(
                    f"{ticker}_{row['period']}".encode(),
                ).hexdigest()[:16]
                db.execute(
                    """
                    INSERT INTO finnhub_earnings
                        (id, ticker, period, actual_eps, estimate_eps,
                         surprise, surprise_pct, quarter, year, collected_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        actual_eps = EXCLUDED.actual_eps,
                        surprise_pct = EXCLUDED.surprise_pct,
                        collected_date = EXCLUDED.collected_date
                    """,
                    [
                        eid,
                        ticker,
                        row["period"],
                        row["actual_eps"],
                        row["estimate_eps"],
                        row["surprise"],
                        row["surprise_pct"],
                        row["quarter"],
                        row["year"],
                        collected_date,
                    ],
                )
            except Exception as e:
                logger.debug("[Finnhub] Earnings insert failed: %s", e)

    def _save_insider_sentiment(
        self,
        db: Any,
        ticker: str,
        result: dict,
        collected_date: date,
    ) -> None:
        try:
            sid = hashlib.md5(
                f"{ticker}_{collected_date}".encode(),
            ).hexdigest()[:16]
            db.execute(
                """
                INSERT INTO finnhub_insider_sentiment
                    (id, ticker, avg_mspr, total_change, months_tracked,
                     sentiment, collected_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    avg_mspr = EXCLUDED.avg_mspr,
                    total_change = EXCLUDED.total_change,
                    collected_date = EXCLUDED.collected_date
                """,
                [
                    sid,
                    ticker,
                    result["avg_mspr"],
                    result["total_change"],
                    result["months_tracked"],
                    result["sentiment"],
                    collected_date,
                ],
            )
        except Exception as e:
            logger.debug("[Finnhub] Insider sentiment insert failed: %s", e)

    def _save_news(
        self,
        db: Any,
        ticker: str,
        articles: list[dict],
        collected_date: date,
    ) -> None:
        for art in articles:
            try:
                nid = hashlib.md5(
                    f"{art['url']}".encode(),
                ).hexdigest()[:16]
                db.execute(
                    """
                    INSERT INTO finnhub_news
                        (id, ticker, headline, summary, source, url,
                         category, related, published_at, collected_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [
                        nid,
                        ticker,
                        art["headline"],
                        art["summary"][:1000],
                        art["source"],
                        art["url"],
                        art["category"],
                        art["related"],
                        art["datetime"],
                        collected_date,
                    ],
                )
            except Exception as e:
                logger.debug("[Finnhub] News insert failed: %s", e)

    # ── DB Read Helpers ─────────────────────────────────────────────

    def _get_stored_recommendations(self, ticker: str) -> list[dict]:
        db = get_db()
        rows = db.execute(
            "SELECT period, strong_buy, buy, hold, sell, strong_sell "
            "FROM finnhub_recommendations WHERE ticker = ? "
            "ORDER BY period DESC",
            [ticker],
        ).fetchall()
        return [
            {
                "ticker": ticker,
                "period": r[0],
                "strong_buy": r[1],
                "buy": r[2],
                "hold": r[3],
                "sell": r[4],
                "strong_sell": r[5],
            }
            for r in rows
        ]

    def _get_stored_earnings(self, ticker: str) -> list[dict]:
        db = get_db()
        rows = db.execute(
            "SELECT period, actual_eps, estimate_eps, surprise, "
            "surprise_pct, quarter, year "
            "FROM finnhub_earnings WHERE ticker = ? "
            "ORDER BY period DESC",
            [ticker],
        ).fetchall()
        return [
            {
                "ticker": ticker,
                "period": r[0],
                "actual_eps": r[1],
                "estimate_eps": r[2],
                "surprise": r[3],
                "surprise_pct": r[4],
                "quarter": r[5],
                "year": r[6],
            }
            for r in rows
        ]

    def _get_stored_insider_sentiment(self, ticker: str) -> dict | None:
        db = get_db()
        row = db.execute(
            "SELECT avg_mspr, total_change, months_tracked, sentiment "
            "FROM finnhub_insider_sentiment WHERE ticker = ? "
            "ORDER BY collected_date DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if not row:
            return None
        return {
            "ticker": ticker,
            "avg_mspr": row[0],
            "total_change": row[1],
            "months_tracked": row[2],
            "sentiment": row[3],
        }

    def _get_stored_news(self, ticker: str) -> list[dict]:
        db = get_db()
        rows = db.execute(
            "SELECT headline, summary, source, url, category, "
            "related, published_at "
            "FROM finnhub_news WHERE ticker = ? "
            "ORDER BY published_at DESC LIMIT 30",
            [ticker],
        ).fetchall()
        return [
            {
                "ticker": ticker,
                "headline": r[0],
                "summary": r[1],
                "source": r[2],
                "url": r[3],
                "category": r[4],
                "related": r[5],
                "datetime": r[6],
            }
            for r in rows
        ]


# Singleton instance
finnhub_collector = FinnhubCollector()
