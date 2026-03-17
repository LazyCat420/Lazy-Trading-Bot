"""Tests for phase toggle system — ensures dev-tools toggles are respected
across ALL pipeline entry points.

Covers:
1. Toggle state persistence across AutonomousLoop re-creation
2. run_full_loop() respects toggles for each phase
3. run_shared_phases() respects toggles for each phase
4. run_llm_only_loop() respects toggles for each phase
5. Module-level _phase_toggles dict in main.py survives _loop re-creation
6. Scheduler copies toggles to new loop instances
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# =====================================================================
# Test 1: AutonomousLoop toggle basics
# =====================================================================

class TestToggleBasics:
    """Verify that the toggle API works correctly on AutonomousLoop."""

    def test_default_toggles_all_enabled(self):
        """All phases default to True."""
        from app.services.autonomous_loop import AutonomousLoop

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        toggles = loop.get_phase_toggles()

        for phase in ("discovery", "import", "collection", "embedding", "analysis", "trading"):
            assert toggles[phase] is True, f"{phase} should default to True"

    def test_set_phase_toggles(self):
        """set_phase_toggles updates the specified phases."""
        from app.services.autonomous_loop import AutonomousLoop

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({"discovery": False, "trading": False})

        toggles = loop.get_phase_toggles()
        assert toggles["discovery"] is False
        assert toggles["trading"] is False
        # Unset phases remain True
        assert toggles["import"] is True
        assert toggles["collection"] is True
        assert toggles["embedding"] is True
        assert toggles["analysis"] is True

    def test_is_phase_enabled_reflects_toggles(self):
        """_is_phase_enabled returns the correct state for each phase."""
        from app.services.autonomous_loop import AutonomousLoop

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({
            "discovery": False,
            "import": False,
            "collection": True,
            "embedding": False,
            "analysis": True,
            "trading": False,
        })

        assert loop._is_phase_enabled("discovery") is False
        assert loop._is_phase_enabled("import") is False
        assert loop._is_phase_enabled("collection") is True
        assert loop._is_phase_enabled("embedding") is False
        assert loop._is_phase_enabled("analysis") is True
        assert loop._is_phase_enabled("trading") is False

    def test_unknown_phase_defaults_to_true(self):
        """Unknown phase names default to True (fail-open)."""
        from app.services.autonomous_loop import AutonomousLoop

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        assert loop._is_phase_enabled("nonexistent") is True


# =====================================================================
# Test 2: run_full_loop respects toggles
# =====================================================================

class TestRunFullLoopToggles:
    """Verify run_full_loop skips disabled phases."""

    @pytest.mark.asyncio
    @patch("app.services.autonomous_loop.AutonomousLoop._do_discovery", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_import", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_collection", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_embedding", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_deep_analysis", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_trading", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.unload_all_ollama_models", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.verify_and_warm_ollama_model", new_callable=AsyncMock)
    async def test_discovery_skipped_when_toggled_off(
        self, mock_warm, mock_unload,
        mock_trading, mock_analysis, mock_embedding,
        mock_collection, mock_import, mock_discovery,
    ):
        """Discovery phase should be skipped when toggled off."""
        from app.services.autonomous_loop import AutonomousLoop

        mock_warm.return_value = {"pre_warmed": True, "vram_bytes": 1024, "recommended_ctx": 4096}
        mock_unload.return_value = 0
        mock_discovery.return_value = MagicMock(
            tickers=[], reddit_count=0, youtube_count=0,
            sec_13f_count=0, congress_count=0, rss_news_count=0,
            transcript_count=0, duration_seconds=0,
        )
        mock_import.return_value = {"total_imported": 0}
        mock_collection.return_value = {}
        mock_embedding.return_value = {"total_chunks": 0}
        mock_analysis.return_value = {"analyzed": 0, "total": 0}
        mock_trading.return_value = {"orders": 0}

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({"discovery": False})

        report = await loop.run_full_loop()

        # Discovery should NOT have been called
        mock_discovery.assert_not_called()
        # But other phases should still run
        mock_import.assert_called_once()
        mock_collection.assert_called_once()
        mock_embedding.assert_called_once()
        mock_analysis.assert_called_once()
        mock_trading.assert_called_once()

        assert report["phases"]["discovery"]["status"] == "skipped"

    @pytest.mark.asyncio
    @patch("app.services.autonomous_loop.AutonomousLoop._do_discovery", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_import", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_collection", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_embedding", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_deep_analysis", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_trading", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.unload_all_ollama_models", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.verify_and_warm_ollama_model", new_callable=AsyncMock)
    async def test_all_phases_skipped_when_all_toggled_off(
        self, mock_warm, mock_unload,
        mock_trading, mock_analysis, mock_embedding,
        mock_collection, mock_import, mock_discovery,
    ):
        """All phases should be skipped when all toggled off."""
        from app.services.autonomous_loop import AutonomousLoop

        mock_warm.return_value = {"pre_warmed": True, "vram_bytes": 1024, "recommended_ctx": 4096}
        mock_unload.return_value = 0

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({
            "discovery": False,
            "import": False,
            "collection": False,
            "embedding": False,
            "analysis": False,
            "trading": False,
        })

        report = await loop.run_full_loop()

        # None of the phase methods should have been called
        mock_discovery.assert_not_called()
        mock_import.assert_not_called()
        mock_collection.assert_not_called()
        mock_embedding.assert_not_called()
        mock_analysis.assert_not_called()
        mock_trading.assert_not_called()

        # All phases should report "skipped"
        for phase in ("discovery", "import", "collection", "embedding", "analysis", "trading"):
            assert report["phases"][phase]["status"] == "skipped", (
                f"{phase} should report 'skipped' when toggled off"
            )


# =====================================================================
# Test 3: run_shared_phases respects toggles
# =====================================================================

class TestRunSharedPhasesToggles:
    """Verify run_shared_phases skips disabled phases."""

    @pytest.mark.asyncio
    @patch("app.services.autonomous_loop.AutonomousLoop._do_discovery", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_import", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_collection", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_embedding", new_callable=AsyncMock)
    async def test_shared_phases_skips_disabled(
        self, mock_embedding, mock_collection, mock_import, mock_discovery,
    ):
        """run_shared_phases should skip phases that are toggled off."""
        from app.services.autonomous_loop import AutonomousLoop

        mock_discovery.return_value = MagicMock(
            tickers=[], reddit_count=0, youtube_count=0,
            sec_13f_count=0, congress_count=0, rss_news_count=0,
            transcript_count=0, duration_seconds=0,
        )
        mock_import.return_value = {"total_imported": 0}
        mock_collection.return_value = {}
        mock_embedding.return_value = {"total_chunks": 0}

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({
            "discovery": False,
            "collection": False,
        })

        report = await loop.run_shared_phases()

        # Discovery and collection should NOT have been called
        mock_discovery.assert_not_called()
        mock_collection.assert_not_called()
        # Import and embedding SHOULD have been called
        mock_import.assert_called_once()
        mock_embedding.assert_called_once()

        assert report["phases"]["discovery"]["status"] == "skipped"
        assert report["phases"]["collection"]["status"] == "skipped"


# =====================================================================
# Test 4: run_llm_only_loop respects toggles
# =====================================================================

class TestRunLLMOnlyLoopToggles:
    """Verify run_llm_only_loop skips disabled phases."""

    @pytest.mark.asyncio
    @patch("app.services.autonomous_loop.AutonomousLoop._do_import", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_deep_analysis", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.AutonomousLoop._do_trading", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.unload_all_ollama_models", new_callable=AsyncMock)
    @patch("app.services.autonomous_loop.LLMService.verify_and_warm_ollama_model", new_callable=AsyncMock)
    async def test_llm_only_skips_disabled_analysis(
        self, mock_warm, mock_unload,
        mock_trading, mock_analysis, mock_import,
    ):
        """run_llm_only_loop should skip analysis when toggled off."""
        from app.services.autonomous_loop import AutonomousLoop

        mock_warm.return_value = {"pre_warmed": True, "vram_bytes": 1024, "recommended_ctx": 4096}
        mock_unload.return_value = 0
        mock_import.return_value = {"total_imported": 0}
        mock_analysis.return_value = {"analyzed": 0, "total": 0}
        mock_trading.return_value = {"orders": 0}

        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        loop.set_phase_toggles({"analysis": False})

        report = await loop.run_llm_only_loop()

        # Analysis should NOT have been called
        mock_analysis.assert_not_called()
        # Import and trading should still run
        mock_import.assert_called_once()
        mock_trading.assert_called_once()

        assert report["phases"]["analysis"]["status"] == "skipped"


# =====================================================================
# Test 5: Module-level _phase_toggles persistence in main.py
# =====================================================================

class TestModuleLevelTogglePersistence:
    """Verify that the module-level toggle dict survives _loop re-creation."""

    def test_phase_toggles_dict_exists(self):
        """main.py should have a module-level _phase_toggles dict."""
        from app.main import _phase_toggles
        assert isinstance(_phase_toggles, dict)
        assert "discovery" in _phase_toggles

    def test_set_toggles_persists_to_module_level(self):
        """Setting toggles via API should persist to module-level dict."""
        from app.main import _phase_toggles

        # Store original values
        originals = dict(_phase_toggles)

        try:
            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app)
            response = client.put(
                "/api/bot/phase-toggles",
                json={"phases": {"discovery": False, "trading": False}},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["phases"]["discovery"] is False
            assert data["phases"]["trading"] is False

            # Module-level dict should also be updated
            assert _phase_toggles["discovery"] is False
            assert _phase_toggles["trading"] is False
        finally:
            # Restore original values
            _phase_toggles.update(originals)

    def test_get_toggles_returns_current_state(self):
        """GET endpoint should return current toggle state."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/api/bot/phase-toggles")
        assert response.status_code == 200
        data = response.json()
        assert "phases" in data
        assert isinstance(data["phases"], dict)
        assert len(data["phases"]) == 6


# =====================================================================
# Test 6: Toggle propagation in code paths
# =====================================================================

class TestTogglePropagation:
    """Verify that all code paths that create new AutonomousLoop instances
    copy the toggle state."""

    def test_run_full_loop_preserves_toggles(self):
        """run_full_loop API should preserve toggles across _loop re-creation."""
        import inspect
        from app.main import run_full_loop

        source = inspect.getsource(run_full_loop)
        assert "set_phase_toggles" in source, (
            "run_full_loop must call set_phase_toggles on the new _loop"
        )
        assert "_phase_toggles" in source, (
            "run_full_loop must reference _phase_toggles module-level dict"
        )

    def test_run_bot_loop_propagates_toggles(self):
        """Per-bot run endpoint should propagate toggles."""
        import inspect
        from app.main import run_bot_loop

        source = inspect.getsource(run_bot_loop)
        assert "set_phase_toggles" in source, (
            "run_bot_loop must call set_phase_toggles on the new loop"
        )

    def test_shared_phases_has_toggle_checks(self):
        """run_shared_phases must check _is_phase_enabled for each phase."""
        import inspect
        from app.services.autonomous_loop import AutonomousLoop

        source = inspect.getsource(AutonomousLoop.run_shared_phases)
        assert source.count("_is_phase_enabled") >= 4, (
            "run_shared_phases must check _is_phase_enabled for "
            "discovery, import, collection, and embedding"
        )

    def test_llm_only_loop_has_toggle_checks(self):
        """run_llm_only_loop must check _is_phase_enabled for each phase."""
        import inspect
        from app.services.autonomous_loop import AutonomousLoop

        source = inspect.getsource(AutonomousLoop.run_llm_only_loop)
        assert source.count("_is_phase_enabled") >= 3, (
            "run_llm_only_loop must check _is_phase_enabled for "
            "import, analysis, and trading"
        )

    def test_scheduler_midday_propagates_toggles(self):
        """Scheduler midday re-analysis must copy toggles."""
        import inspect
        from app.services.scheduler import TradingScheduler

        source = inspect.getsource(TradingScheduler._midday_reanalysis)
        assert "set_phase_toggles" in source, (
            "_midday_reanalysis must copy toggles from self._loop"
        )
        assert "get_phase_toggles" in source, (
            "_midday_reanalysis must read toggles from self._loop"
        )

    def test_scheduler_scoreboard_propagates_toggles(self):
        """Scheduler scoreboard sweep must copy toggles."""
        import inspect
        from app.services.scheduler import TradingScheduler

        source = inspect.getsource(TradingScheduler._scoreboard_sweep)
        assert "set_phase_toggles" in source, (
            "_scoreboard_sweep must copy toggles from self._loop"
        )
        assert "_is_phase_enabled" in source, (
            "_scoreboard_sweep must gate phase calls on toggles"
        )
