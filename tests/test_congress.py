"""Tests for Congressional Trades Collector.

Run: .\\venv\\Scripts\\activate; python -m pytest tests/test_congress.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch


from app.collectors.congress_collector import CongressCollector
from app.models.discovery import ScoredTicker

# ── Logging setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


# Sample HTML landing page with CSRF token
MOCK_LANDING_HTML = """
<html>
<body>
<form>
    <input type="hidden" name="csrfmiddlewaretoken" value="test_csrf_token_12345">
    <input type="submit" value="I understand">
</form>
</body>
</html>
"""

# Sample report detail page with trade rows
MOCK_REPORT_HTML = """
<html>
<body>
<table>
<thead><tr><th>#</th><th>Date</th><th></th><th>Ticker</th><th>Asset</th><th>Type</th><th>Order</th><th>Amount</th></tr></thead>
<tbody>
<tr>
    <td>1</td>
    <td>01/15/2025</td>
    <td></td>
    <td>AAPL</td>
    <td>Apple Inc</td>
    <td>Stock</td>
    <td>Purchase</td>
    <td>$1,001 - $15,000</td>
</tr>
<tr>
    <td>2</td>
    <td>01/20/2025</td>
    <td></td>
    <td>MSFT</td>
    <td>Microsoft Corp</td>
    <td>Stock</td>
    <td>Sale (Full)</td>
    <td>$15,001 - $50,000</td>
</tr>
<tr>
    <td>3</td>
    <td>01/22/2025</td>
    <td></td>
    <td>--</td>
    <td>Some Bond Fund</td>
    <td>Municipal Bond</td>
    <td>Purchase</td>
    <td>$1,001 - $15,000</td>
</tr>
</tbody>
</table>
</body>
</html>
"""

# Sample reports API response
MOCK_REPORTS_DATA = {
    "data": [
        ["John", "Doe", "", '<a href="/search/view/annual/12345/">Report</a>', "02/01/2025"],
        ["Jane", "Smith", "", '<a href="/search/view/annual/67890/">Report</a>', "01/15/2025"],
        ["Bob", "Paper", "", '<a href="/search/view/paper/99999/">PDF Report</a>', "01/10/2025"],
    ]
}


# ══════════════════════════════════════════════════════════════════
# 1. CSRF TOKEN TESTS
# ══════════════════════════════════════════════════════════════════


class TestCongressCSRF:
    """Tests for CSRF token handling."""

    @patch("app.collectors.congress_collector.time.sleep")
    def test_csrf_extraction(self, mock_sleep: MagicMock) -> None:
        """Should extract CSRF token from landing page."""
        collector = CongressCollector()

        # Mock the session
        mock_response = MagicMock()
        mock_response.text = MOCK_LANDING_HTML
        mock_response.url = "https://efdsearch.senate.gov/search/home/"

        mock_post_response = MagicMock()
        mock_post_response.status_code = 200

        collector._session.get = MagicMock(return_value=mock_response)
        collector._session.post = MagicMock(return_value=mock_post_response)
        collector._session.cookies = {"csrftoken": "session_csrf_abc"}

        token = collector._get_csrf_token()
        log.info("Extracted CSRF token: %s", token)
        assert token == "session_csrf_abc"


# ══════════════════════════════════════════════════════════════════
# 2. REPORT PARSING TESTS
# ══════════════════════════════════════════════════════════════════


class TestCongressReportParsing:
    """Tests for parsing congressional trade reports."""

    def setup_method(self) -> None:
        self.collector = CongressCollector()
        log.info("=== TestCongressReportParsing setup ===")

    @patch("app.collectors.congress_collector.time.sleep")
    def test_parse_report_with_trades(self, mock_sleep: MagicMock) -> None:
        """Should extract stock trades from report detail page."""
        from datetime import datetime

        # Mock the HTTP request for the report detail page
        mock_response = MagicMock()
        mock_response.text = MOCK_REPORT_HTML
        mock_response.url = "https://efdsearch.senate.gov/search/view/annual/12345/"
        self.collector._session.get = MagicMock(return_value=mock_response)

        row = ["John", "Doe", "", '<a href="/search/view/annual/12345/">Report</a>', "02/01/2025"]
        cutoff = datetime(2024, 1, 1)

        trades = self.collector._parse_report(row, cutoff)
        log.info("Parsed %d trades:", len(trades))
        for t in trades:
            log.info("  %s: %s %s — %s", t["member_name"], t["tx_type"], t["ticker"], t["amount_range"])

        # Should have 2 stock trades (skipping the bond)
        assert len(trades) == 2

        aapl = next((t for t in trades if t["ticker"] == "AAPL"), None)
        assert aapl is not None
        assert aapl["tx_type"] == "Purchase"
        assert aapl["member_name"] == "John Doe"
        assert aapl["chamber"] == "senate"

        msft = next((t for t in trades if t["ticker"] == "MSFT"), None)
        assert msft is not None
        assert msft["tx_type"] == "Sale (Full)"

    def test_parse_report_pdf_skipped(self) -> None:
        """PDF-only reports should be skipped."""
        from datetime import datetime

        row = ["Bob", "Paper", "", '<a href="/search/view/paper/99999/">PDF</a>', "01/10/2025"]
        cutoff = datetime(2024, 1, 1)

        trades = self.collector._parse_report(row, cutoff)
        log.info("PDF report trades: %d (should be 0)", len(trades))
        assert len(trades) == 0

    def test_parse_report_short_row(self) -> None:
        """Short rows should return empty list."""
        from datetime import datetime

        trades = self.collector._parse_report(["A", "B"], datetime(2024, 1, 1))
        assert trades == []
        log.info("Short row correctly returned empty list")


# ══════════════════════════════════════════════════════════════════
# 3. DB INTEGRATION TESTS (MOCKED)
# ══════════════════════════════════════════════════════════════════


class TestCongressDBIntegration:
    """Tests for DB persistence and scored ticker generation."""

    @patch("app.collectors.congress_collector.get_db")
    def test_tickers_from_db(self, mock_get_db: MagicMock) -> None:
        """Should generate ScoredTicker from DB trades."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            ("AAPL", 5, 3, 4, 1),   # 5 trades, 3 members, 4 buys, 1 sell
            ("MSFT", 2, 1, 0, 2),   # 2 trades, 1 member, 0 buys, 2 sells
        ]
        mock_get_db.return_value = mock_db

        collector = CongressCollector()
        tickers = collector._tickers_from_db()

        log.info("Generated %d scored tickers:", len(tickers))
        for t in tickers:
            log.info(
                "  $%s: %.1f pts, sentiment=%s — %s",
                t.ticker, t.discovery_score, t.sentiment_hint, t.source_detail,
            )

        assert len(tickers) == 2

        aapl = next((t for t in tickers if t.ticker == "AAPL"), None)
        assert aapl is not None
        assert aapl.source == "congress"
        assert aapl.sentiment_hint == "bullish"  # 4 buys vs 1 sell = 80% buy ratio

        msft = next((t for t in tickers if t.ticker == "MSFT"), None)
        assert msft is not None
        assert msft.sentiment_hint == "bearish"  # 0 buys vs 2 sells = 0% buy ratio

    @patch("app.collectors.congress_collector.get_db")
    def test_daily_guard(self, mock_get_db: MagicMock) -> None:
        """Should skip scraping if already collected today."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = (50,)
        mock_db.execute.return_value.fetchall.return_value = []
        mock_get_db.return_value = mock_db

        collector = CongressCollector()
        result = asyncio.get_event_loop().run_until_complete(
            collector.collect_recent_trades()
        )

        log.info("Daily guard result: %d tickers (should use cache)", len(result))
        assert isinstance(result, list)

    @patch("app.collectors.congress_collector.get_db")
    def test_get_trades_for_ticker(self, mock_get_db: MagicMock) -> None:
        """Should return congressional trades for a specific ticker."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            ("John Doe", "senate", "Purchase", "2025-01-15", "2025-02-01", "$1,001 - $15,000", "Apple Inc"),
        ]
        mock_get_db.return_value = mock_db

        collector = CongressCollector()
        result = asyncio.get_event_loop().run_until_complete(
            collector.get_trades_for_ticker("AAPL")
        )

        log.info("Trades for AAPL: %s", result)
        assert len(result) == 1
        assert result[0]["member_name"] == "John Doe"
        assert result[0]["tx_type"] == "Purchase"

    @patch("app.collectors.congress_collector.get_db")
    def test_save_trades(self, mock_get_db: MagicMock) -> None:
        """Should persist trades to DuckDB."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        collector = CongressCollector()
        trades = [
            {
                "id": "abc123",
                "member_name": "Jane Smith",
                "chamber": "senate",
                "ticker": "NVDA",
                "asset_name": "NVIDIA Corp",
                "tx_type": "Purchase",
                "tx_date": "2025-01-20",
                "filed_date": "2025-02-05",
                "amount_range": "$15,001 - $50,000",
                "source_url": "https://efdsearch.senate.gov/search/view/annual/67890/",
            }
        ]

        collector._save_trades(mock_db, trades)

        # Should have called execute once
        assert mock_db.execute.call_count == 1
        log.info("Save trades called execute %d times", mock_db.execute.call_count)


# ══════════════════════════════════════════════════════════════════
# 4. MODEL INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════


class TestCongressModels:
    """Tests for model compatibility with new sources."""

    def test_scored_ticker_congress_source(self) -> None:
        """ScoredTicker should accept 'congress' source."""
        t = ScoredTicker(
            ticker="AAPL",
            discovery_score=5.0,
            source="congress",
            sentiment_hint="bullish",
        )
        log.info("Congress ScoredTicker: %s", t.model_dump())
        assert t.source == "congress"

    def test_scored_ticker_sec_13f_source(self) -> None:
        """ScoredTicker should accept 'sec_13f' source."""
        t = ScoredTicker(
            ticker="MSFT",
            discovery_score=8.0,
            source="sec_13f",
        )
        log.info("SEC 13F ScoredTicker: %s", t.model_dump())
        assert t.source == "sec_13f"

    def test_scored_ticker_multi_source(self) -> None:
        """ScoredTicker should accept 'multi' source."""
        t = ScoredTicker(
            ticker="NVDA",
            discovery_score=20.0,
            source="multi",
        )
        log.info("Multi ScoredTicker: %s", t.model_dump())
        assert t.source == "multi"
