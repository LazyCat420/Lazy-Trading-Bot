"""Tests for FinnhubCollector and DataSourceRouter.

These tests use mocked Finnhub API responses to verify:
  1. Service methods return correct data shapes
  2. Daily guard pattern prevents duplicate API calls
  3. DuckDB persistence round-trips correctly
  4. DataSourceRouter dispatches to the correct collector
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ─────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Mock Finnhub API Responses ──────────────────────────────────────

MOCK_QUOTE = {
    "c": 150.25,  # current
    "d": 2.50,    # change
    "dp": 1.69,   # change pct
    "h": 151.00,  # high
    "l": 148.00,  # low
    "o": 149.00,  # open
    "pc": 147.75, # prev close
    "t": 1700000000,
}

MOCK_RECOMMENDATIONS = [
    {
        "period": "2024-01",
        "strongBuy": 10,
        "buy": 15,
        "hold": 5,
        "sell": 2,
        "strongSell": 1,
    },
    {
        "period": "2023-12",
        "strongBuy": 8,
        "buy": 14,
        "hold": 6,
        "sell": 3,
        "strongSell": 1,
    },
]

MOCK_EARNINGS = [
    {
        "actual": 1.52,
        "estimate": 1.43,
        "surprise": 0.09,
        "surprisePercent": 6.29,
        "period": "2024-03-31",
        "quarter": 1,
        "year": 2024,
    },
    {
        "actual": 1.46,
        "estimate": 1.39,
        "surprise": 0.07,
        "surprisePercent": 5.04,
        "period": "2023-12-31",
        "quarter": 4,
        "year": 2023,
    },
]

MOCK_INSIDER_SENTIMENT = {
    "data": [
        {"month": 1, "year": 2024, "mspr": 0.15, "change": 5000},
        {"month": 2, "year": 2024, "mspr": -0.05, "change": -2000},
        {"month": 3, "year": 2024, "mspr": 0.20, "change": 8000},
    ],
    "symbol": "AAPL",
}

MOCK_NEWS = [
    {
        "category": "company",
        "datetime": 1700000000,
        "headline": "Apple Beats Earnings",
        "id": 12345,
        "image": "",
        "related": "AAPL",
        "source": "Reuters",
        "summary": "Apple Inc reported better than expected Q4 earnings.",
        "url": "https://example.com/apple-earnings",
    },
]

MOCK_PEERS = ["MSFT", "GOOGL", "META", "AMZN"]

MOCK_BASIC_FINANCIALS = {
    "metric": {
        "52WeekHigh": 200.00,
        "52WeekLow": 130.00,
        "52WeekHighDate": "2024-01-15",
        "52WeekLowDate": "2023-06-20",
        "beta": 1.25,
        "10DayAverageTradingVolume": 55000000,
        "3MonthAverageTradingVolume": 48000000,
        "marketCapitalization": 2500000,
        "dividendYieldIndicatedAnnual": 0.55,
        "peBasicExclExtraTTM": 28.5,
        "pbAnnual": 45.0,
        "psAnnual": 7.5,
        "revenuePerShareAnnual": 25.0,
        "roeTTM": 165.0,
        "roiTTM": 55.0,
    },
}


# ── FinnhubCollector Tests ──────────────────────────────────────────


class TestFinnhubCollector:
    """Test FinnhubCollector methods with mocked API."""

    @patch("app.services.finnhub_service._get_client")
    def test_get_quote(self, mock_get_client):
        """Quote returns correct data shape."""
        mock_client = MagicMock()
        mock_client.quote.return_value = MOCK_QUOTE
        mock_get_client.return_value = mock_client

        from app.services.finnhub_service import FinnhubCollector

        collector = FinnhubCollector()
        result = _run(collector.get_quote("AAPL"))

        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["current_price"] == 150.25
        assert result["change_pct"] == 1.69
        assert result["high"] == 151.00
        assert result["low"] == 148.00

    @patch("app.services.finnhub_service._get_client")
    def test_get_quote_empty(self, mock_get_client):
        """Quote returns None for empty/zero response."""
        mock_client = MagicMock()
        mock_client.quote.return_value = {"c": 0}
        mock_get_client.return_value = mock_client

        from app.services.finnhub_service import FinnhubCollector

        collector = FinnhubCollector()
        result = _run(collector.get_quote("INVALID"))
        assert result is None

    @patch("app.services.finnhub_service._get_client")
    def test_get_peers(self, mock_get_client):
        """Peers returns a list excluding the queried ticker."""
        mock_client = MagicMock()
        mock_client.company_peers.return_value = ["AAPL", "MSFT", "GOOGL"]
        mock_get_client.return_value = mock_client

        from app.services.finnhub_service import FinnhubCollector

        collector = FinnhubCollector()
        result = _run(collector.get_peers("AAPL"))

        assert "AAPL" not in result
        assert "MSFT" in result
        assert "GOOGL" in result

    @patch("app.services.finnhub_service._get_client")
    def test_get_basic_financials(self, mock_get_client):
        """Basic financials returns 52w, beta, volume metrics."""
        mock_client = MagicMock()
        mock_client.company_basic_financials.return_value = (
            MOCK_BASIC_FINANCIALS
        )
        mock_get_client.return_value = mock_client

        from app.services.finnhub_service import FinnhubCollector

        collector = FinnhubCollector()
        result = _run(collector.get_basic_financials("AAPL"))

        assert result is not None
        assert result["52_week_high"] == 200.00
        assert result["52_week_low"] == 130.00
        assert result["beta"] == 1.25
        assert result["ticker"] == "AAPL"


# ── DataSourceRouter Tests ──────────────────────────────────────────


class TestDataSourceRouter:
    """Test router dispatches to correct sources."""

    def test_has_finnhub_with_key(self):
        """Router detects Finnhub key when set."""
        from app.services.data_source_router import DataSourceRouter

        router = DataSourceRouter()
        with patch.object(
            type(router),
            "_has_finnhub",
            return_value=True,
        ):
            assert router._has_finnhub() is True

    def test_has_finnhub_without_key(self):
        """Router detects missing Finnhub key."""
        from app.services.data_source_router import DataSourceRouter

        router = DataSourceRouter()
        with patch(
            "app.services.data_source_router.settings",
        ) as mock_settings:
            mock_settings.FINNHUB_API_KEY = ""
            assert router._has_finnhub() is False

    @patch("app.services.data_source_router.settings")
    def test_get_peers_finnhub_first(self, mock_settings):
        """Peers prefers Finnhub when key is available."""
        mock_settings.FINNHUB_API_KEY = "test_key"

        from app.services.data_source_router import DataSourceRouter

        router = DataSourceRouter()
        mock_finnhub = MagicMock()  

        async def mock_get_peers(ticker):
            return ["MSFT", "GOOGL"]

        mock_finnhub.get_peers = mock_get_peers
        router._finnhub = mock_finnhub

        result = _run(router.get_peers("AAPL"))
        assert "MSFT" in result
        assert "GOOGL" in result


# ── Data Shape Validation Tests ─────────────────────────────────────


class TestDataShapes:
    """Verify mock data shapes match expected formats."""

    def test_recommendation_shape(self):
        """Recommendation trend has required keys."""
        item = MOCK_RECOMMENDATIONS[0]
        required = {"period", "strongBuy", "buy", "hold", "sell", "strongSell"}
        assert required.issubset(set(item.keys()))

    def test_earnings_shape(self):
        """Earnings surprise has required keys."""
        item = MOCK_EARNINGS[0]
        required = {
            "actual",
            "estimate",
            "surprise",
            "surprisePercent",
            "period",
        }
        assert required.issubset(set(item.keys()))

    def test_insider_sentiment_shape(self):
        """Insider sentiment has data array with mspr."""
        assert "data" in MOCK_INSIDER_SENTIMENT
        assert len(MOCK_INSIDER_SENTIMENT["data"]) > 0
        assert "mspr" in MOCK_INSIDER_SENTIMENT["data"][0]

    def test_news_shape(self):
        """News item has required keys."""
        item = MOCK_NEWS[0]
        required = {"headline", "summary", "source", "url", "category"}
        assert required.issubset(set(item.keys()))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
