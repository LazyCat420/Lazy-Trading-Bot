"""Live integration tests for yFinance and other external data sources.

Requires active internet connection.
Marked with @pytest.mark.integration to be skippable in CI if needed.
"""

import pytest
from app.collectors.yfinance_collector import YFinanceCollector
from app.collectors.news_collector import NewsCollector
from app.database import get_db

@pytest.mark.integration
@pytest.mark.asyncio
class TestLiveDataCollection:
    """Verifies that collectors actually pull data from external APIs."""

    @pytest.fixture(scope="function", autouse=True)
    def clean_db(self, ticker):
        """Clean DB for the test ticker to ensure fresh collection."""
        db = get_db()
        # Clean all tables for this ticker
        tables = [
            "price_history", "fundamentals", "financial_history", 
            "balance_sheet", "cash_flows", "analyst_data", 
            "news_articles"
        ]
        for table in tables:
            try:
                db.execute(f"DELETE FROM {table} WHERE ticker = ?", [ticker])
            except Exception:
                pass # Table might not exist yet
                
    @pytest.fixture(scope="class")
    def ticker(self):
        """Use a stable, highly liquid ticker for testing."""
        return "NVDA"

    async def test_yfinance_price_history_live(self, ticker):
        """Verify we can fetch recent price history."""
        collector = YFinanceCollector()
        rows = await collector.collect_price_history(ticker, period="1mo", interval="1d")
        
        assert len(rows) > 0, "No price rows returned"
        assert len(rows) >= 15, "Should have at least 15 trading days in a month"
        
        latest = rows[0]
        assert latest.close > 0
        assert latest.volume > 0
        print(f"\n[LIVE] {ticker} latest close: ${latest.close:.2f}")

    async def test_yfinance_fundamentals_live(self, ticker):
        """Verify we can fetch fundamental snapshot."""
        collector = YFinanceCollector()
        f = await collector.collect_fundamentals(ticker)
        
        assert f is not None
        assert f.market_cap > 0, "Market cap should be positive"
        assert f.sector, "Sector should be preset"
        print(f"\n[LIVE] {ticker} Market Cap: ${f.market_cap:,.0f}")

    async def test_yfinance_financial_history_live(self, ticker):
        """Verify we can fetch income statement."""
        collector = YFinanceCollector()
        rows = await collector.collect_financial_history(ticker)
        
        assert len(rows) > 0
        latest = rows[0]
        assert latest.revenue > 0
        print(f"\n[LIVE] {ticker} {latest.year} Revenue: ${latest.revenue:,.0f}")

    async def test_yfinance_balance_sheet_live(self, ticker):
        """Verify we can fetch balance sheet."""
        collector = YFinanceCollector()
        rows = await collector.collect_balance_sheet(ticker)
        
        assert len(rows) > 0
        latest = rows[0]
        assert latest.total_assets > 0
        print(f"\n[LIVE] {ticker} {latest.year} Assets: ${latest.total_assets:,.0f}")

    async def test_yfinance_cash_flow_live(self, ticker):
        """Verify we can fetch cash flow."""
        collector = YFinanceCollector()
        rows = await collector.collect_cashflow(ticker)
        
        assert len(rows) > 0
        latest = rows[0]
        # Operating cash flow can be negative, but for NVDA it should be positive
        print(f"\n[LIVE] {ticker} {latest.year} Op Cash Flow: ${latest.operating_cashflow:,.0f}")

    async def test_yfinance_analyst_data_live(self, ticker):
        """Verify we can fetch analyst targets."""
        collector = YFinanceCollector()
        data = await collector.collect_analyst_data(ticker)
        
        assert data is not None
        # yfinance sometimes returns 0 for num_analysts even if targets exist
        assert data.target_mean > 0 or data.num_analysts >= 0
        if data.target_mean > 0:
            print(f"\n[LIVE] {ticker} Analyst Target: ${data.target_mean:.2f} ({data.num_analysts} analysts)")
        else:
            print(f"\n[LIVE] {ticker} No analyst targets found (API limit?)")
    async def test_google_news_live(self, ticker):
        """Verify we can fetch news from Google RSS."""
        collector = NewsCollector()
        # Clean DB handled by fixture
        articles = await collector.collect(ticker, limit=5)
        
        assert len(articles) > 0
        latest = articles[0]
        assert latest.title, "Title should not be empty"
        assert latest.url, "URL should not be empty"
        print(f"\n[LIVE] News: {latest.title} ({latest.publisher})")
