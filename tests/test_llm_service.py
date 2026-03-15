"""Tests for LLM service — validates Prism API endpoint and payload format."""

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


class TestSendPrismRequest:
    """Test that _send_prism_request calls the correct Prism endpoint."""

    @pytest.mark.asyncio
    async def test_uses_chat_endpoint(self):
        """Verify URL is /chat?stream=false (not the old /text-to-text)."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        # Mock the shared client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "text": "hello",
            "thinking": "",
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            result = await svc._send_prism_request(
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
        assert "/chat?stream=false" in url, (
            f"Expected /chat?stream=false in URL, got {url}"
        )
        assert "/text-to-text" not in url, (
            f"Old /text-to-text endpoint should not be used, got {url}"
        )

    @pytest.mark.asyncio
    async def test_payload_has_conversation_meta(self):
        """Verify payload includes conversationMeta (not a separate /conversations/start call)."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "text": "response",
            "thinking": "",
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            await svc._send_prism_request(
                messages=[
                    {"role": "system", "content": "System prompt"},
                    {"role": "user", "content": "User message"},
                ],
                response_format="text",
                max_tokens=100,
                temperature=0.3,
                audit_ticker="AAPL",
                audit_step="discovery",
            )

        # Only ONE call should be made (to /chat), not two (no /conversations/start)
        assert mock_client.post.call_count == 1, (
            f"Expected exactly 1 HTTP call, got {mock_client.post.call_count}. "
            "The old /conversations/start call should be removed."
        )

        call_args = mock_client.post.call_args
        payload = call_args[1].get("json", call_args[0][1] if len(call_args[0]) > 1 else {})

        # Verify conversationMeta is present
        assert "conversationMeta" in payload, (
            "Payload must include conversationMeta for Prism conversation auto-creation"
        )
        meta = payload["conversationMeta"]
        assert "title" in meta
        assert "AAPL" in meta["title"]
        assert "settings" in meta
        assert meta["settings"]["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_payload_has_conversation_id(self):
        """Verify payload includes a conversationId UUID."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "text": "ok",
            "thinking": "",
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            await svc._send_prism_request(
                messages=[{"role": "user", "content": "Hi"}],
                response_format="text",
                max_tokens=100,
                temperature=0.3,
            )

        call_args = mock_client.post.call_args
        payload = call_args[1].get("json", {})

        assert "conversationId" in payload
        assert isinstance(payload["conversationId"], str)
        assert len(payload["conversationId"]) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_response_parsing_unchanged(self):
        """Verify the response is parsed the same way (text, thinking, usage)."""
        from app.services.llm_service import LLMService

        svc = LLMService(model_override="test-model")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "text": "The answer is 42",
            "thinking": "Let me think...",
            "usage": {"inputTokens": 50, "outputTokens": 20},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch("app.services.llm_service._get_shared_client", return_value=mock_client):
            result = await svc._send_prism_request(
                messages=[{"role": "user", "content": "What is 6*7?"}],
                response_format="text",
                max_tokens=100,
                temperature=0.3,
            )

        assert result == "The answer is 42"
