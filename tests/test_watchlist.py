"""Tests for Phase 2 — WatchlistManager.

Tests the core CRUD operations and import-from-discovery logic.
Analysis tests mock PipelineService to avoid hitting real LLM.
Run: .\\venv\\Scripts\\activate; python -m pytest tests/test_watchlist.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock, patch

from app.models.watchlist import WatchlistEntry, WatchlistSummary

# ── Logging setup — all test output visible with -s flag ─────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 1. MODEL TESTS
# ══════════════════════════════════════════════════════════════════


class TestWatchlistModels:
    """Tests for the Pydantic watchlist models."""

    def test_entry_defaults(self) -> None:
        """WatchlistEntry should have sensible defaults."""
        entry = WatchlistEntry(ticker="NVDA")
        log.info("WatchlistEntry defaults: signal=%s, confidence=%.1f, status=%s",
                 entry.signal, entry.confidence, entry.status)
        assert entry.signal == "PENDING"
        assert entry.confidence == 0.0
        assert entry.status == "active"
        assert entry.source == "manual"
        assert entry.analysis_count == 0

    def test_entry_custom_values(self) -> None:
        """WatchlistEntry should accept custom values."""
        entry = WatchlistEntry(
            ticker="TSLA",
            signal="BUY",
            confidence=0.85,
            source="discovery",
            discovery_score=12.5,
        )
        log.info("Custom entry: %s signal=%s conf=%.2f",
                 entry.ticker, entry.signal, entry.confidence)
        assert entry.ticker == "TSLA"
        assert entry.signal == "BUY"
        assert entry.confidence == 0.85
        assert entry.discovery_score == 12.5

    def test_summary_defaults(self) -> None:
        """WatchlistSummary should have sensible defaults."""
        summary = WatchlistSummary()
        log.info("Summary defaults: total=%d, active=%d", summary.total, summary.active)
        assert summary.total == 0
        assert summary.buy_count == 0
        assert summary.sell_count == 0
        assert summary.pending_count == 0

    def test_summary_serialization(self) -> None:
        """WatchlistSummary should serialize to dict."""
        summary = WatchlistSummary(
            total=5,
            active=5,
            buy_count=2,
            sell_count=1,
            hold_count=1,
            pending_count=1,
        )
        d = summary.model_dump()
        log.info("Serialized summary: %s", d)
        assert d["total"] == 5
        assert d["buy_count"] == 2


# ══════════════════════════════════════════════════════════════════
# 2. WATCHLIST MANAGER CRUD TESTS
# ══════════════════════════════════════════════════════════════════


class TestWatchlistManagerCRUD:
    """Tests for add/remove/clear operations."""

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_add_ticker(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Adding a new ticker should insert into DB."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None  # Not existing
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.add_ticker("NVDA")
        log.info("Add result: %s", result)
        assert result["status"] == "added"
        assert result["ticker"] == "NVDA"
        assert mock_db.execute.call_count >= 2  # SELECT + INSERT

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_add_duplicate(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Adding a ticker that's already active should return already_exists."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("NVDA", "active")
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.add_ticker("NVDA")
        log.info("Duplicate add result: %s", result)
        assert result["status"] == "already_exists"

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_add_reactivate(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Adding a previously removed ticker should reactivate it."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("NVDA", "removed")
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.add_ticker("NVDA")
        log.info("Reactivate result: %s", result)
        assert result["status"] == "reactivated"

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_add_empty_ticker(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Empty ticker string should return error."""
        from app.services.watchlist_manager import WatchlistManager

        wm = WatchlistManager()
        result = wm.add_ticker("")
        log.info("Empty ticker result: %s", result)
        assert "error" in result

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_remove_ticker(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Remove should set status to 'removed'."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = ("NVDA",)
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.remove_ticker("NVDA")
        log.info("Remove result: %s", result)
        assert result["status"] == "removed"

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_remove_not_found(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Removing a non-existent ticker should return error."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.remove_ticker("FAKE")
        log.info("Remove not found result: %s", result)
        assert result["error"] == "not_found"

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_clear(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Clear should delete all rows."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.clear()
        log.info("Clear result: %s", result)
        assert result["status"] == "cleared"
        # Verify DELETE was called
        delete_calls = [
            str(c) for c in mock_db.execute.call_args_list
            if "DELETE" in str(c)
        ]
        assert len(delete_calls) >= 1


# ══════════════════════════════════════════════════════════════════
# 3. IMPORT FROM DISCOVERY TESTS
# ══════════════════════════════════════════════════════════════════


class TestWatchlistImport:
    """Tests for import_from_discovery."""

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_import_from_discovery(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """Should import top-scoring tickers from ticker_scores."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()

        # First call: ticker_scores query returns 2 tickers
        # Subsequent calls: check for existing watchlist entries (None = not there)
        mock_db.execute.return_value.fetchall.return_value = [
            ("NVDA", 15.0, "bullish"),
            ("TSLA", 8.0, "neutral"),
        ]
        mock_db.execute.return_value.fetchone.return_value = None

        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.import_from_discovery(min_score=5.0, max_tickers=10)
        log.info("Import result: %s", result)
        assert result["total_imported"] == 2
        assert "NVDA" in result["imported"]
        assert "TSLA" in result["imported"]

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_import_empty(self, mock_pipeline: MagicMock, mock_get_db: MagicMock) -> None:
        """No qualifying tickers should import nothing."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = wm.import_from_discovery(min_score=100.0, max_tickers=5)
        log.info("Import empty result: %s", result)
        assert result["total_imported"] == 0


# ══════════════════════════════════════════════════════════════════
# 4. ANALYSIS TESTS (mock pipeline)
# ══════════════════════════════════════════════════════════════════


class TestWatchlistAnalysis:
    """Tests for analyze_ticker and analyze_all."""

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_analyze_ticker(self, mock_pipeline_cls: MagicMock, mock_get_db: MagicMock) -> None:
        """Analyze should call PipelineService.run and update watchlist row."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Mock pipeline result
        mock_result = MagicMock()
        mock_result.decision = MagicMock()
        mock_result.decision.signal = "BUY"
        mock_result.decision.confidence = 0.75
        mock_result.errors = []

        mock_pipeline = MagicMock()
        mock_pipeline.run = AsyncMock(return_value=mock_result)
        mock_pipeline_cls.return_value = mock_pipeline

        wm = WatchlistManager()
        result = asyncio.get_event_loop().run_until_complete(
            wm.analyze_ticker("NVDA")
        )
        log.info("Analyze result: %s", result)
        assert result["ticker"] == "NVDA"
        assert result["signal"] == "BUY"
        assert result["confidence"] == 0.75
        assert "elapsed_s" in result

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_analyze_ticker_error(self, mock_pipeline_cls: MagicMock, mock_get_db: MagicMock) -> None:
        """Pipeline error should return ERROR signal."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_pipeline = MagicMock()
        mock_pipeline.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        mock_pipeline_cls.return_value = mock_pipeline

        wm = WatchlistManager()
        result = asyncio.get_event_loop().run_until_complete(
            wm.analyze_ticker("FAKE")
        )
        log.info("Analyze error result: %s", result)
        assert result["signal"] == "ERROR"
        assert len(result["errors"]) > 0

    @patch("app.services.watchlist_manager.get_db")
    @patch("app.services.watchlist_manager.PipelineService")
    def test_analyze_all_empty(self, mock_pipeline_cls: MagicMock, mock_get_db: MagicMock) -> None:
        """Analyze-all with no active tickers should return empty."""
        from app.services.watchlist_manager import WatchlistManager

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_get_db.return_value = mock_db

        wm = WatchlistManager()
        result = asyncio.get_event_loop().run_until_complete(
            wm.analyze_all()
        )
        log.info("Analyze-all empty result: %s", result)
        assert result["results"] == []
        assert "No active tickers" in result.get("message", "")
