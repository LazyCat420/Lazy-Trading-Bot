"""Data Source Router — central dispatcher that selects the optimal data
source for each data type to avoid redundant/duplicate API calls.

Strategy:
  • OHLCV price history  → yFinance (free, unlimited history)
  • Real-time quotes     → Finnhub (sub-second)
  • Company fundamentals → yFinance (richest .info dict)
  • Financial statements → yFinance (multi-year built-in)
  • Analyst recs         → Finnhub (granular monthly changes)
  • Earnings surprises   → Finnhub (beat/miss history)
  • Insider sentiment    → Finnhub (MSPR) + yFinance (raw txns)
  • Company news         → Finnhub (categorized) + RSS
  • Peers                → Finnhub → fallback peer_fetcher
  • Intl / Commodities   → OpenBB (future Phase 5)
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.utils.logger import logger


class DataSourceRouter:
    """Decides which collector to call for each data type."""

    def __init__(self) -> None:
        # Lazy-import to avoid circular deps and startup cost
        self._yfin = None
        self._finnhub = None
        self._openbb = None

    @property
    def yfin(self):
        if self._yfin is None:
            from app.services.yfinance_service import YFinanceCollector
            self._yfin = YFinanceCollector()
        return self._yfin

    @property
    def finnhub(self):
        if self._finnhub is None:
            from app.services.finnhub_service import FinnhubCollector
            self._finnhub = FinnhubCollector()
        return self._finnhub

    @property
    def openbb(self):
        if self._openbb is None:
            from app.services.openbb_service import OpenBBCollector
            self._openbb = OpenBBCollector()
        return self._openbb

    def _has_finnhub(self) -> bool:
        """Check if Finnhub API key is configured."""
        return bool(settings.FINNHUB_API_KEY)

    # ── Price ──────────────────────────────────────────────────────

    async def get_price_history(
        self, ticker: str, period: str = "max", interval: str = "1d",
    ) -> Any:
        """OHLCV history → yFinance (free, unlimited)."""
        return await self.yfin.collect_price_history(
            ticker, period=period, interval=interval,
        )

    async def get_quote(self, ticker: str) -> dict | None:
        """Real-time quote → Finnhub (sub-second) → fallback yFinance."""
        if self._has_finnhub():
            result = await self.finnhub.get_quote(ticker)
            if result:
                return result
        # Fallback: yFinance .info current price
        try:
            snap = await self.yfin.collect_fundamentals(ticker)
            if snap and snap.market_cap > 0:
                return {
                    "ticker": ticker,
                    "current_price": snap.trailing_eps * snap.trailing_pe
                    if snap.trailing_pe > 0
                    else 0,
                    "source": "yfinance_fallback",
                }
        except Exception:
            pass
        return None

    # ── Fundamentals ───────────────────────────────────────────────

    async def get_fundamentals(self, ticker: str) -> Any:
        """Full fundamentals → yFinance (richest data)."""
        return await self.yfin.collect_fundamentals(ticker)

    async def get_financial_history(self, ticker: str) -> Any:
        """Multi-year income statement → yFinance."""
        return await self.yfin.collect_financial_history(ticker)

    async def get_balance_sheet(self, ticker: str) -> Any:
        """Multi-year balance sheet → yFinance."""
        return await self.yfin.collect_balance_sheet(ticker)

    async def get_cashflow(self, ticker: str) -> Any:
        """Multi-year cash flow → yFinance."""
        return await self.yfin.collect_cashflow(ticker)

    # ── Analyst ────────────────────────────────────────────────────

    async def get_analyst_data(self, ticker: str) -> dict | None:
        """Analyst price targets → yFinance.
        Analyst recommendation trends → Finnhub (more granular).
        Returns both merged into one dict.
        """
        result: dict[str, Any] = {}

        # yFinance: price targets + broad counts
        try:
            yf_data = await self.yfin.collect_analyst_data(ticker)
            if yf_data:
                result["price_targets"] = {
                    "target_mean": yf_data.target_mean,
                    "target_median": yf_data.target_median,
                    "target_high": yf_data.target_high,
                    "target_low": yf_data.target_low,
                    "num_analysts": yf_data.num_analysts,
                }
                result["consensus"] = {
                    "strong_buy": yf_data.strong_buy,
                    "buy": yf_data.buy,
                    "hold": yf_data.hold,
                    "sell": yf_data.sell,
                    "strong_sell": yf_data.strong_sell,
                }
        except Exception as e:
            logger.debug("[Router] yFinance analyst failed: %s", e)

        # Finnhub: monthly recommendation trend changes
        if self._has_finnhub():
            try:
                trends = await self.finnhub.get_recommendation_trends(ticker)
                if trends:
                    result["recommendation_trends"] = trends
            except Exception as e:
                logger.debug("[Router] Finnhub recommendations failed: %s", e)

        return result if result else None

    # ── Earnings ───────────────────────────────────────────────────

    async def get_earnings(self, ticker: str) -> dict[str, Any]:
        """Earnings → yFinance (calendar) + Finnhub (surprise history)."""
        result: dict[str, Any] = {}

        # yFinance: next earnings date
        try:
            yf_data = await self.yfin.collect_earnings_calendar(ticker)
            if yf_data:
                result["calendar"] = {
                    "next_earnings_date": str(yf_data.next_earnings_date)
                    if yf_data.next_earnings_date
                    else None,
                    "days_until": yf_data.days_until_earnings,
                }
        except Exception as e:
            logger.debug("[Router] yFinance earnings calendar failed: %s", e)

        # Finnhub: beat/miss history (UNIQUE to Finnhub)
        if self._has_finnhub():
            try:
                surprises = await self.finnhub.get_earnings_surprises(ticker)
                if surprises:
                    result["surprises"] = surprises
            except Exception as e:
                logger.debug("[Router] Finnhub earnings failed: %s", e)

        return result

    # ── Insider ────────────────────────────────────────────────────

    async def get_insider_data(self, ticker: str) -> dict[str, Any]:
        """Insider activity → yFinance (raw txns) + Finnhub (MSPR score)."""
        result: dict[str, Any] = {}

        # yFinance: raw insider transactions
        try:
            yf_data = await self.yfin.collect_insider_activity(ticker)
            if yf_data:
                result["raw_activity"] = {
                    "net_buying_90d": yf_data.net_insider_buying_90d,
                    "institutional_pct": yf_data.institutional_ownership_pct,
                }
        except Exception as e:
            logger.debug("[Router] yFinance insider failed: %s", e)

        # Finnhub: aggregated MSPR (UNIQUE to Finnhub)
        if self._has_finnhub():
            try:
                sentiment = await self.finnhub.get_insider_sentiment(ticker)
                if sentiment:
                    result["mspr_sentiment"] = sentiment
            except Exception as e:
                logger.debug("[Router] Finnhub insider sentiment failed: %s", e)

        return result

    # ── News ───────────────────────────────────────────────────────

    async def get_news(self, ticker: str) -> list[dict[str, Any]]:
        """Company news → Finnhub (categorized) → fallback yFinance."""
        articles = []

        # Finnhub: category-tagged, real-time
        if self._has_finnhub():
            try:
                fh_news = await self.finnhub.get_company_news(ticker)
                for art in fh_news:
                    articles.append({
                        "title": art.get("headline", ""),
                        "summary": art.get("summary", ""),
                        "source": art.get("source", "finnhub"),
                        "url": art.get("url", ""),
                        "category": art.get("category", ""),
                        "published_at": art.get("datetime"),
                        "provider": "finnhub",
                    })
            except Exception as e:
                logger.debug("[Router] Finnhub news failed: %s", e)

        # If Finnhub not available or empty, use yFinance
        if not articles:
            try:
                from app.database import get_db
                db = get_db()
                rows = db.execute(
                    "SELECT title, summary, publisher, url, published_at "
                    "FROM news_articles WHERE ticker = ? "
                    "ORDER BY published_at DESC LIMIT 20",
                    [ticker],
                ).fetchall()
                for r in rows:
                    articles.append({
                        "title": r[0],
                        "summary": r[1],
                        "source": r[2],
                        "url": r[3],
                        "published_at": r[4],
                        "provider": "yfinance",
                    })
            except Exception as e:
                logger.debug("[Router] yFinance news failed: %s", e)

        return articles

    # ── Peers ──────────────────────────────────────────────────────

    async def get_peers(self, ticker: str) -> list[str]:
        """Similar companies → Finnhub → fallback peer_fetcher."""
        if self._has_finnhub():
            try:
                peers = await self.finnhub.get_peers(ticker)
                if peers:
                    return peers
            except Exception as e:
                logger.debug("[Router] Finnhub peers failed: %s", e)

        # Fallback: existing peer_fetcher
        try:
            from app.services.peer_fetcher import fetch_peers
            peers = await fetch_peers(ticker)
            return peers or []
        except Exception:
            return []

    # ── Convenience: collect all unique data ───────────────────────

    async def collect_all(self, ticker: str) -> dict[str, Any]:
        """Collect all data for a ticker, dispatching to optimal sources.

        Returns a dict keyed by data category.
        """
        import asyncio

        logger.info("[Router] Collecting all data for %s", ticker)

        tasks = {
            "fundamentals": self.get_fundamentals(ticker),
            "analyst": self.get_analyst_data(ticker),
            "earnings": self.get_earnings(ticker),
            "insider": self.get_insider_data(ticker),
            "news": self.get_news(ticker),
            "peers": self.get_peers(ticker),
        }

        # Only add Finnhub-specific tasks if key is available
        if self._has_finnhub():
            tasks["quote"] = self.get_quote(ticker)
            tasks["basic_financials"] = self.finnhub.get_basic_financials(
                ticker,
            )

        results = {}
        gathered = await asyncio.gather(
            *tasks.values(),
            return_exceptions=True,
        )
        for key, result in zip(tasks.keys(), gathered, strict=True):
            if isinstance(result, Exception):
                logger.warning("[Router] %s failed: %s", key, result)
                results[key] = None
            else:
                results[key] = result

        ok = sum(1 for v in results.values() if v is not None and v != [])
        logger.info(
            "[Router] Collected %d/%d data categories for %s",
            ok,
            len(tasks),
            ticker,
        )
        return results


# Singleton
data_source_router = DataSourceRouter()
