"""Integration test for DiscoveryService.clear_data().

Verifies that clear_data actually deletes rows from both tables
and returns the correct status.

Run: .\venv\Scripts\activate; python -m pytest tests/test_clear_data.py -v -s
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger(__name__)


class TestClearData:
    """Tests for DiscoveryService.clear_data()."""

    @patch("app.services.discovery_service.get_db")
    def test_clear_returns_cleared(self, mock_get_db: MagicMock) -> None:
        """clear_data should return status='cleared' when tables are emptied."""
        from app.services.discovery_service import DiscoveryService

        mock_db = MagicMock()

        # Simulate: before clear has rows, after clear has 0
        count_calls = iter([
            (5,),   # discovered_tickers count before
            (3,),   # ticker_scores count before
            (0,),   # discovered_tickers count after
            (0,),   # ticker_scores count after
        ])
        mock_db.execute.return_value.fetchone.side_effect = (
            lambda: next(count_calls)
        )
        mock_get_db.return_value = mock_db

        svc = DiscoveryService()
        result = svc.clear_data()
        log.info("Result: %s", result)

        assert result["status"] == "cleared"
        assert result["remaining"] == 0

        # Verify DELETE statements were called
        call_strings = [str(c) for c in mock_db.execute.call_args_list]
        delete_calls = [c for c in call_strings if "DELETE" in c]
        log.info("DELETE calls: %s", delete_calls)
        assert len(delete_calls) >= 2, f"Expected 2 DELETEs, got {delete_calls}"

    @patch("app.services.discovery_service.get_db")
    def test_clear_handles_error(self, mock_get_db: MagicMock) -> None:
        """clear_data should return status='error' if DELETE fails."""
        from app.services.discovery_service import DiscoveryService

        mock_db = MagicMock()

        # Before counts succeed
        count_calls = iter([(5,), (3,)])
        mock_db.execute.return_value.fetchone.side_effect = (
            lambda: next(count_calls)
        )
        # Make the 3rd execute call (first DELETE) raise
        original_execute = mock_db.execute
        call_count = [0]

        def patched_execute(sql, *a, **kw):
            call_count[0] += 1
            if "DELETE" in str(sql):
                raise RuntimeError("DB locked")
            return original_execute(sql, *a, **kw)

        mock_db.execute = patched_execute
        mock_get_db.return_value = mock_db

        svc = DiscoveryService()
        result = svc.clear_data()
        log.info("Error result: %s", result)

        assert result["status"] == "error"
        assert "DB locked" in result["error"]

    @patch("app.services.discovery_service.get_db")
    def test_clear_partial(self, mock_get_db: MagicMock) -> None:
        """clear_data should return status='partial' if rows remain."""
        from app.services.discovery_service import DiscoveryService

        mock_db = MagicMock()

        # Before: 5+3, After: 2+0 (partial)
        count_calls = iter([(5,), (3,), (2,), (0,)])
        mock_db.execute.return_value.fetchone.side_effect = (
            lambda: next(count_calls)
        )
        mock_get_db.return_value = mock_db

        svc = DiscoveryService()
        result = svc.clear_data()
        log.info("Partial result: %s", result)

        assert result["status"] == "partial"
        assert result["remaining"] == 2

    @patch("app.services.discovery_service.get_db")
    def test_clear_empty_tables(self, mock_get_db: MagicMock) -> None:
        """clear_data on already-empty tables should still succeed."""
        from app.services.discovery_service import DiscoveryService

        mock_db = MagicMock()

        # All counts are 0
        count_calls = iter([(0,), (0,), (0,), (0,)])
        mock_db.execute.return_value.fetchone.side_effect = (
            lambda: next(count_calls)
        )
        mock_get_db.return_value = mock_db

        svc = DiscoveryService()
        result = svc.clear_data()
        log.info("Empty tables result: %s", result)

        assert result["status"] == "cleared"
        assert result["remaining"] == 0
