"""Tests for LLM service — validates native Ollama API endpoint and payload format."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_llm_queue():
    """Reset the global LLM queue between tests."""
    import app.services.llm_service as mod
    mod._llm_queue = None
    mod._llm_queue_waiters = 0
    mod._shutdown_requested = False
    yield


class TestSendOllamaRequest:
    """Test that _send_ollama_request calls the correct Ollama endpoint."""

    @pytest.mark.asyncio
    async def test_uses_chat_endpoint(self):
        """Verify URL is /api/chat."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        # Mock the shared client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "model": "test-model",
            "message": {"role": "assistant", "content": "hello"},
            "eval_count": 10,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            result = await svc._send_ollama_request(
                messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hello"},
                ],
                response_format="text",
                max_tokens=100,
                temperature=0.3,
            )

        # Verify the URL used
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        assert "/api/chat" in url, (
            f"Expected /api/chat in URL, got {url}"
        )
        assert getattr(mock_client.post, 'call_count', 0) == 1

    @pytest.mark.asyncio
    async def test_payload_format(self):
        """Verify payload matches standard Ollama chat protocol."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": "response"},
            "eval_count": 5,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            await svc._send_ollama_request(
                messages=[
                    {"role": "system", "content": "System prompt"},
                    {"role": "user", "content": "User message"},
                ],
                response_format="json",
                max_tokens=100,
                temperature=0.3,
            )

        call_args = mock_client.post.call_args
        payload = call_args[1].get("json", call_args[0][1] if len(call_args[0]) > 1 else {})

        assert payload["model"] == "test-model"
        assert "messages" in payload
        assert payload["stream"] is False
        assert "options" in payload
        assert "temperature" in payload["options"]
        assert payload["format"] == "json"
        
    @pytest.mark.asyncio
    async def test_response_parsing(self):
        """Verify the response is parsed from standard Ollama format."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "model": "test-model",
            "message": {"role": "assistant", "content": "The answer is 42"},
            "eval_count": 20,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            result = await svc._send_ollama_request(
                messages=[{"role": "user", "content": "What is 6*7?"}],
                response_format="text",
                max_tokens=100,
                temperature=0.3,
            )

        assert result == "The answer is 42"
