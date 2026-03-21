"""OpenBB unified data collector — replaces scattered yfinance/scraper calls.

Uses the OpenBB Platform (pip install openbb) to fetch data from ~100
providers through a single API.  Falls back to yfinance on failure.

Usage:
    collector = OpenBBCollector()
    prices = await collector.get_price_history("AAPL")
    fundamentals = await collector.get_fundamentals("AAPL")
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from app.database import get_db
from app.utils.logger import logger

# Lazy-init OpenBB to avoid import-time slowdown
_obb = None


def _get_obb():
    """Lazy-load the OpenBB SDK (first call takes ~2s to build extensions)."""
    global _obb
    if _obb is None:
        from openbb import obb

        _obb = obb
        logger.info("OpenBB SDK loaded (extensions built)")
    return _obb


def _run_sync(fn, *args, **kwargs):
    """Run a synchronous OpenBB call in a thread to avoid blocking asyncio."""
    import functools

    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def _safe_dict(obj) -> dict:
    """Convert an OpenBB result row to a plain dict, handling NaN/None."""
    import math

    if hasattr(obj, "__dict__"):
        d = obj.__dict__.copy()
    elif isinstance(obj, dict):
        d = obj.copy()
    else:
        return {}
    # Clean NaN values
    for k, v in list(d.items()):
        if isinstance(v, float) and math.isnan(v):
            d[k] = None
    return d


class OpenBBCollector:
    """Unified financial data collector backed by OpenBB Platform."""

    # ── Price History ────────────────────────────────────────────

    async def get_price_history(
        self, ticker: str, period: str = "10y", provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch OHLCV price history. Returns list of dicts."""
        try:
            obb = _get_obb()
            # Calculate start date from period string
            years = int(period.replace("y", "")) if period.endswith("y") else 5
            start = (date.today() - timedelta(days=years * 365)).isoformat()

            result = await _run_sync(
                obb.equity.price.historical,
                ticker,
                provider=provider,
                start_date=start,
            )
            df = result.to_dataframe()
            if df.empty:
                logger.warning("OpenBB price history empty for %s", ticker)
                return []

            rows = []
            for idx, row in df.iterrows():
                dt = idx.date() if hasattr(idx, "date") else idx
                rows.append({
                    "date": str(dt),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": int(row.get("volume", 0)),
                })
            logger.info("OpenBB: %d price rows for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB price history failed for %s: %s", ticker, e)
            return []

    # ── Fundamentals ─────────────────────────────────────────────

    async def get_fundamentals(
        self, ticker: str, provider: str = "yfinance"
    ) -> dict | None:
        """Fetch key fundamental metrics."""
        try:
            obb = _get_obb()
            # Try profile first
            profile_result = await _run_sync(
                obb.equity.profile, ticker, provider=provider
            )
            profile_data = profile_result.to_dataframe()

            if profile_data.empty:
                return None

            row = profile_data.iloc[0]
            data = {}
            field_map = {
                "sector": "sector",
                "industry": "industry",
                "market_cap": "market_cap",
                "beta": "beta",
                "currency": "currency",
                "name": "name",
                "exchange": "exchange",
            }
            for obb_key, our_key in field_map.items():
                if obb_key in row.index:
                    val = row[obb_key]
                    data[our_key] = val if val == val else None  # NaN check

            logger.info("OpenBB: fundamentals for %s (%s)", ticker, data.get("name", "?"))
            return data
        except Exception as e:
            logger.warning("OpenBB fundamentals failed for %s: %s", ticker, e)
            return None

    # ── Financial Statements ─────────────────────────────────────

    async def get_income_statement(
        self, ticker: str, period: str = "annual", limit: int = 5, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch income statement (revenue, net income, margins)."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.fundamental.income,
                ticker,
                period=period,
                limit=limit,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = []
            for _, row in df.iterrows():
                rows.append(_safe_dict(row.to_dict()))
            logger.info("OpenBB: %d income stmt rows for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB income stmt failed for %s: %s", ticker, e)
            return []

    async def get_balance_sheet(
        self, ticker: str, period: str = "annual", limit: int = 5, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch balance sheet data."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.fundamental.balance,
                ticker,
                period=period,
                limit=limit,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d balance sheet rows for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB balance sheet failed for %s: %s", ticker, e)
            return []

    async def get_cashflow(
        self, ticker: str, period: str = "annual", limit: int = 5, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch cash flow statement."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.fundamental.cash,
                ticker,
                period=period,
                limit=limit,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d cashflow rows for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB cashflow failed for %s: %s", ticker, e)
            return []

    # ── Analyst Data ──────────────────────────────────────────────

    async def get_analyst_data(
        self, ticker: str, provider: str = "yfinance"
    ) -> dict | None:
        """Fetch analyst estimates and consensus."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.estimates.consensus, ticker, provider=provider
            )
            df = result.to_dataframe()
            if df.empty:
                return None

            row = df.iloc[0].to_dict()
            data = _safe_dict(row)
            logger.info("OpenBB: analyst data for %s", ticker)
            return data
        except Exception as e:
            logger.warning("OpenBB analyst data failed for %s: %s", ticker, e)
            return None

    # ── Insider Activity ──────────────────────────────────────────

    async def get_insider_activity(
        self, ticker: str, limit: int = 20, provider: str = "sec"
    ) -> list[dict]:
        """Fetch insider trading activity from SEC."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.ownership.insider_trading,
                ticker,
                limit=limit,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d insider trades for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB insider activity failed for %s: %s", ticker, e)
            return []

    # ── Earnings Calendar ─────────────────────────────────────────

    async def get_earnings_calendar(
        self, ticker: str, provider: str = "yfinance"
    ) -> dict | None:
        """Fetch upcoming earnings dates."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.calendar.earnings,
                ticker,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return None

            # Return next earnings date info
            row = df.iloc[0].to_dict()
            data = _safe_dict(row)
            logger.info("OpenBB: earnings calendar for %s", ticker)
            return data
        except Exception as e:
            logger.warning("OpenBB earnings calendar failed for %s: %s", ticker, e)
            return None

    # ── News ──────────────────────────────────────────────────────

    async def get_news(
        self, ticker: str, limit: int = 20, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch company news from multiple providers."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.news.company,
                ticker,
                limit=limit,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            articles = []
            for _, row in df.iterrows():
                articles.append({
                    "title": str(row.get("title", "")),
                    "url": str(row.get("url", "")),
                    "published": str(row.get("date", "")),
                    "source": str(row.get("source", provider)),
                    "summary": str(row.get("text", ""))[:500],
                })
            logger.info("OpenBB: %d news articles for %s", len(articles), ticker)
            return articles
        except Exception as e:
            logger.warning("OpenBB news failed for %s: %s", ticker, e)
            return []

    # ── SEC Filings ───────────────────────────────────────────────

    async def get_sec_filings(
        self, ticker: str, filing_type: str = "", limit: int = 10
    ) -> list[dict]:
        """Fetch SEC EDGAR filings."""
        try:
            obb = _get_obb()
            kwargs: dict[str, Any] = {
                "symbol": ticker,
                "limit": limit,
                "provider": "sec",
            }
            if filing_type:
                kwargs["type"] = filing_type

            result = await _run_sync(
                obb.equity.fundamental.filings, **kwargs
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d SEC filings for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB SEC filings failed for %s: %s", ticker, e)
            return []

    # ── Institutional Holders (13F) ───────────────────────────────

    async def get_institutional_holders(
        self, ticker: str, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch institutional holders from SEC 13F filings."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.ownership.institutional,
                ticker,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d institutional holders for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB inst holders failed for %s: %s", ticker, e)
            return []

    # ── Congressional Trades ──────────────────────────────────────

    async def get_congressional_trades(
        self, ticker: str, provider: str = "congress_gov"
    ) -> list[dict]:
        """Fetch congressional stock trading disclosures."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.equity.discovery.filings,
                provider="government_us",
            )
            df = result.to_dataframe()

            # Filter for our ticker
            ticker_upper = ticker.upper()
            if "ticker" in df.columns:
                df = df[df["ticker"].str.upper() == ticker_upper]
            elif "symbol" in df.columns:
                df = df[df["symbol"].str.upper() == ticker_upper]

            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d congressional trades for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB congressional trades failed for %s: %s", ticker, e)
            return []

    # ── Economic Indicators (NEW capability) ──────────────────────

    async def get_economic_data(
        self, series_id: str = "GDP", provider: str = "fred"
    ) -> list[dict]:
        """Fetch FRED economic data series (GDP, CPI, interest rates, etc.)."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.economy.fred_series,
                symbol=series_id,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.iterrows()]
            logger.info("OpenBB: %d FRED data points for %s", len(rows), series_id)
            return rows
        except Exception as e:
            logger.warning("OpenBB FRED data failed for %s: %s", series_id, e)
            return []

    # ── Options Chain (NEW capability) ────────────────────────────

    async def get_options_chain(
        self, ticker: str, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch options chain data."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.derivatives.options.chains,
                ticker,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.head(50).iterrows()]
            logger.info("OpenBB: %d option contracts for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB options failed for %s: %s", ticker, e)
            return []

    # ── ETF Holdings (NEW capability) ─────────────────────────────

    async def get_etf_holdings(
        self, ticker: str, provider: str = "yfinance"
    ) -> list[dict]:
        """Fetch ETF holdings (if ticker is an ETF)."""
        try:
            obb = _get_obb()
            result = await _run_sync(
                obb.etf.holdings,
                ticker,
                provider=provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return []

            rows = [_safe_dict(row.to_dict()) for _, row in df.head(30).iterrows()]
            logger.info("OpenBB: %d ETF holdings for %s", len(rows), ticker)
            return rows
        except Exception as e:
            logger.warning("OpenBB ETF holdings failed for %s: %s", ticker, e)
            return []

    # ── Convenience: collect all data for a ticker ────────────────

    async def collect_all(self, ticker: str) -> dict[str, Any]:
        """Run all data collection methods in parallel for one ticker.
        Returns dict keyed by data category.
        """
        logger.info("OpenBB: collecting ALL data for %s", ticker)
        t0 = datetime.now()

        tasks = {
            "price_history": self.get_price_history(ticker),
            "fundamentals": self.get_fundamentals(ticker),
            "income_statement": self.get_income_statement(ticker),
            "balance_sheet": self.get_balance_sheet(ticker),
            "cashflow": self.get_cashflow(ticker),
            "analyst_data": self.get_analyst_data(ticker),
            "insider_activity": self.get_insider_activity(ticker),
            "earnings_calendar": self.get_earnings_calendar(ticker),
            "news": self.get_news(ticker),
            "sec_filings": self.get_sec_filings(ticker),
            "institutional_holders": self.get_institutional_holders(ticker),
        }

        results = {}
        gathered = await asyncio.gather(
            *[self._named_task(k, v) for k, v in tasks.items()],
            return_exceptions=True,
        )

        ok_count = 0
        for name, data in gathered:
            if isinstance(data, Exception):
                logger.warning("OpenBB collect_all %s failed: %s", name, data)
                results[name] = None
            else:
                results[name] = data
                if data is not None and data != []:
                    ok_count += 1

        elapsed = (datetime.now() - t0).total_seconds()
        logger.info(
            "OpenBB: collected %d/%d sources for %s in %.1fs",
            ok_count, len(tasks), ticker, elapsed,
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


# Singleton instance
openbb_collector = OpenBBCollector()
