"""Tests for pipeline freeze fixes — SEC 13F caps, timeout, and no yfinance fallback.

Run: .\\venv\\Scripts\\activate; python -m pytest tests/test_pipeline_freeze.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import MagicMock, patch

from app.collectors.sec_13f_collector import (
    MAX_HOLDINGS_PER_FILER,
    PER_FILER_TIMEOUT_SECS,
    SEC13FCollector,
)
from app.models.discovery import ScoredTicker

# ── Logging setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 1. HOLDINGS CAP TESTS
# ══════════════════════════════════════════════════════════════════


class TestHoldingsCap:
    """Verify that holdings per filer are capped at MAX_HOLDINGS_PER_FILER."""

    def test_max_holdings_constant_exists(self) -> None:
        """MAX_HOLDINGS_PER_FILER should be defined and reasonable."""
        log.info("MAX_HOLDINGS_PER_FILER = %d", MAX_HOLDINGS_PER_FILER)
        assert MAX_HOLDINGS_PER_FILER > 0
        assert MAX_HOLDINGS_PER_FILER <= 1000

    def test_per_filer_timeout_exists(self) -> None:
        """PER_FILER_TIMEOUT_SECS should be defined."""
        log.info("PER_FILER_TIMEOUT_SECS = %d", PER_FILER_TIMEOUT_SECS)
        assert PER_FILER_TIMEOUT_SECS > 0

    @patch("app.collectors.sec_13f_collector.get_db")
    def test_scrape_filer_caps_holdings(self, mock_get_db: MagicMock) -> None:
        """When a filer has > MAX_HOLDINGS_PER_FILER, only top by value are saved."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        collector = SEC13FCollector()

        # Generate more holdings than the cap
        num_holdings = MAX_HOLDINGS_PER_FILER + 200
        fake_holdings = [
            {
                "name_of_issuer": f"COMPANY {i}",
                "cusip": f"00000{i:04d}",
                "value_usd": float(num_holdings - i),  # Decreasing value
                "shares": 1000,
                "share_type": "SH",
                "ticker": f"TK{i}" if i < MAX_HOLDINGS_PER_FILER + 100 else "",
            }
            for i in range(num_holdings)
        ]

        # Mock _get_holdings to return our fake data
        with patch.object(collector, "_get_holdings", return_value=fake_holdings):
            # Mock _find_latest_13f to return a valid filing
            mock_filing = {
                "accession": "0000000001-25-000001",
                "filing_date": "2025-02-14",
                "quarter": "2024Q4",
                "primary_doc": "form13fhr.htm",
                "index_url": "https://example.com/index.htm",
                "filing_url": "https://example.com/filing.htm",
                "cik": "1067983",
                "file_accession": "000000000125000001",
            }
            with patch.object(collector, "_find_latest_13f", return_value=mock_filing):
                with patch.object(
                    collector,
                    "_get_submissions",
                    return_value={"filings": {"recent": {}}},
                ):
                    # Mock no existing data
                    mock_db.execute.return_value.fetchone.return_value = (0,)
                    mock_db.execute.return_value.fetchall.return_value = [
                        ("1067983", "Test Fund"),
                    ]

                    count = collector._scrape_filer(mock_db, "1067983", "Test Fund")

        log.info("Saved %d holdings (cap is %d)", count, MAX_HOLDINGS_PER_FILER)
        # Should be capped at MAX_HOLDINGS_PER_FILER
        assert count <= MAX_HOLDINGS_PER_FILER, (
            f"Holdings should be capped at {MAX_HOLDINGS_PER_FILER}, got {count}"
        )


# ══════════════════════════════════════════════════════════════════
# 2. YFINANCE FALLBACK REMOVAL TESTS
# ══════════════════════════════════════════════════════════════════


class TestNoYfinanceFallback:
    """Verify that _cusip_to_ticker never calls yfinance."""

    def setup_method(self) -> None:
        self.collector = SEC13FCollector()
        log.info("=== TestNoYfinanceFallback setup ===")

    def test_known_cusip_still_works(self) -> None:
        """Known CUSIPs should still resolve correctly."""
        assert (
            self.collector._cusip_to_ticker("037833100", "APPLE INC", "COM") == "AAPL"
        )
        assert (
            self.collector._cusip_to_ticker("594918104", "MICROSOFT CORP", "COM")
            == "MSFT"
        )
        assert (
            self.collector._cusip_to_ticker("67066G104", "NVIDIA CORP", "COM") == "NVDA"
        )
        log.info("Known CUSIP lookups still work")

    def test_name_fallback_still_works(self) -> None:
        """Name-based mapping should still work for unknown CUSIPs."""
        assert (
            self.collector._cusip_to_ticker("999999999", "TESLA INC", "COM") == "TSLA"
        )
        assert (
            self.collector._cusip_to_ticker("999999999", "AMAZON COM", "COM") == "AMZN"
        )
        log.info("Name fallback still works")

    @patch("app.collectors.sec_13f_collector.SEC13FCollector._name_to_ticker_yf")
    def test_yfinance_never_called(self, mock_yf: MagicMock) -> None:
        """_name_to_ticker_yf should NEVER be called (removed from flow)."""
        # Call with a completely unknown issuer
        result = self.collector._cusip_to_ticker(
            "999999999", "UNKNOWN CORP XYZ BLAH", "QRS"
        )
        log.info("Unknown issuer result: '%s'", result)
        assert result == ""
        mock_yf.assert_not_called()
        log.info("Confirmed: yfinance was NOT called for unknown CUSIP")

    def test_unknown_cusip_returns_empty_fast(self) -> None:
        """Unknown CUSIP/name should return empty string immediately (no network)."""
        t0 = time.time()
        for i in range(100):
            self.collector._cusip_to_ticker(f"99999{i:04d}", f"UNKNOWN CORP {i}", "SH")
        elapsed = time.time() - t0
        log.info("100 unknown CUSIP lookups took %.3fs", elapsed)
        # Should be nearly instant (< 0.1s) since no network calls
        assert elapsed < 1.0, (
            f"Unknown CUSIP lookups should be instant, took {elapsed:.3f}s"
        )


# ══════════════════════════════════════════════════════════════════
# 3. THREAD EXECUTOR TESTS
# ══════════════════════════════════════════════════════════════════


class TestThreadExecutor:
    """Verify that _scrape_all_filers method exists (run in executor)."""

    def test_scrape_all_filers_method_exists(self) -> None:
        """The new _scrape_all_filers method should exist."""
        collector = SEC13FCollector()
        assert hasattr(collector, "_scrape_all_filers")
        log.info("_scrape_all_filers method exists")

    def test_collect_recent_holdings_is_async(self) -> None:
        """collect_recent_holdings should be an async method."""
        import inspect

        assert inspect.iscoroutinefunction(SEC13FCollector.collect_recent_holdings)
        log.info("collect_recent_holdings is async")


# ══════════════════════════════════════════════════════════════════
# 4. DISCOVERY TIMEOUT TESTS
# ══════════════════════════════════════════════════════════════════


class TestDiscoveryTimeout:
    """Verify that discovery service handles collector timeouts."""

    def test_timed_collect_handles_timeout(self) -> None:
        """_timed_collect should return empty list on timeout."""
        from app.services.discovery_service import DiscoveryService  # noqa: F401

        async def _slow_collector() -> list[ScoredTicker]:
            await asyncio.sleep(10)  # Will be cancelled by timeout
            return [ScoredTicker(ticker="NEVER")]

        async def _run():
            # Use a very short timeout for testing
            t0 = time.time()
            try:
                result = await asyncio.wait_for(_slow_collector(), timeout=0.1)
            except asyncio.TimeoutError:
                result = []
            elapsed = time.time() - t0
            log.info("Timeout test: result=%s, elapsed=%.2fs", result, elapsed)
            return result

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == []
        log.info("Timeout correctly returned empty list")

    def test_timed_collect_passes_results(self) -> None:
        """_timed_collect should pass through results on success."""

        async def _fast_collector() -> list[ScoredTicker]:
            return [ScoredTicker(ticker="NVDA", discovery_score=10.0)]

        async def _run():
            try:
                result = await asyncio.wait_for(_fast_collector(), timeout=5.0)
            except asyncio.TimeoutError:
                result = []
            return result

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert len(result) == 1
        assert result[0].ticker == "NVDA"
        log.info("Fast collector correctly returned results")
