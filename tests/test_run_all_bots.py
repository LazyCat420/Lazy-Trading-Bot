import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app, _run_all_state, run_all_bots
from app.services.bot_registry import BotRegistry

client = TestClient(app)

@pytest.fixture(autouse=True)
def reset_run_all_state():
    """Reset the run-all state before each test."""
    # Reset dictionary directly
    _run_all_state["running"] = False
    _run_all_state["total_bots"] = 0
    _run_all_state["completed"] = 0
    _run_all_state["current_bot"] = None
    _run_all_state["current_phase"] = None
    _run_all_state["results"] = []
    _run_all_state["log"] = []
    _run_all_state["started_at"] = ""
    yield

class TestRunAllBotsEndpoint:
    def test_run_all_blocks_concurrent_runs(self):
        """Test that /api/bots/run-all returns 409 if already running."""
        _run_all_state["running"] = True
        response = client.post("/api/bots/run-all")
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]

    @patch("app.main.BotRegistry.list_bots")
    def test_run_all_no_bots(self, mock_list_bots):
        """Test returning 400 when no bots exist."""
        mock_list_bots.return_value = []
        response = client.post("/api/bots/run-all")
        assert response.status_code == 400
        assert "No active bots" in response.json()["detail"]

    @patch("app.main.BotRegistry.list_bots")
    @patch("app.main._run_all_task")
    @patch("asyncio.create_task")
    def test_run_all_starts_task(self, mock_create_task, mock_task, mock_list_bots):
        """Test that starting a run resets state and creates asyncio task."""
        mock_list_bots.return_value = [
            {"bot_id": "b1", "model_name": "model_1", "display_name": "Bot 1"}
        ]
        
        response = client.post("/api/bots/run-all?max_tickers=5")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert _run_all_state["running"] is True
        assert _run_all_state["total_bots"] == 1
        assert _run_all_state["completed"] == 0
        mock_create_task.assert_called_once()
    
    def test_run_all_status(self):
        """Test the status endpoint."""
        _run_all_state["running"] = True
        _run_all_state["total_bots"] = 3
        _run_all_state["completed"] = 1
        
        response = client.get("/api/bots/run-all/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True
        assert data["total_bots"] == 3
        assert data["completed"] == 1

    @pytest.mark.asyncio
    @patch("app.main.BotRegistry.list_bots")
    @patch("app.main.LLMService.unload_all_ollama_models", new_callable=AsyncMock)
    @patch("app.main.LLMService.verify_and_warm_ollama_model", new_callable=AsyncMock)
    @patch("app.main.AutonomousLoop.run_full_loop", new_callable=AsyncMock)
    @patch("app.main.BotRegistry.record_run")
    @patch("app.main.BotRegistry.update_stats")
    async def test_run_all_task_execution(self, mock_update, mock_record, mock_loop, mock_warm, mock_unload, mock_list):
        """Test the _run_all async task behavior."""
        mock_list.return_value = [
            {"bot_id": "b1", "model_name": "Llama-3", "display_name": "Bot 1", "context_length": 4096}
        ]
        mock_unload.return_value = 1
        mock_warm.return_value = {"pre_warmed": True, "vram_bytes": 1024, "recommended_ctx": 4096}
        mock_loop.return_value = None

        # We need to test the inner task created by the route
        from app.main import run_all_bots, _run_all_state
        
        # Override the FastAPI route behaviour directly by calling the logic and grabbing the created task
        with patch("asyncio.create_task") as mock_create_task:
            await run_all_bots(max_tickers=5)
            # The async task that actually does the work
            task = mock_create_task.call_args[0][0]
        # By the end of the generator, the run_all function restores whatever was in settings initially
        # mock saving config to make sure it doesn't try actually writing to file in testing env
        with patch("app.config.settings.update_llm_config") as mock_save:
            await task
        
        # Check that it called correct unloading, warming and loop execution
        mock_unload.assert_called()
        mock_warm.assert_called_once()
        mock_loop.assert_called_once()
        mock_update.assert_called_once_with("b1")
        mock_record.assert_called_once_with("b1")
        
        assert _run_all_state["completed"] == 1
        assert _run_all_state["running"] is False
