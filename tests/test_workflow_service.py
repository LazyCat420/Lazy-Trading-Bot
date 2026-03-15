"""Tests for WorkflowService — validates workflow posting to Prism."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.WorkflowService import WorkflowTracker


class TestWorkflowTracker:
    """Test WorkflowTracker step collection and Prism posting."""

    def test_add_step(self):
        """Steps are collected correctly."""
        tracker = WorkflowTracker(title="Test Workflow")
        tracker.add_step(
            model="olmo-3:latest",
            label="Step 1",
            system_prompt="Be helpful",
            user_input="Hello",
            output="Hi there",
            duration=2.5,
        )
        tracker.add_step(
            model="olmo-3:latest",
            label="Step 2",
            user_input="How are you?",
            output="Good",
            duration=1.0,
            conversation_id="conv-123",
        )

        assert len(tracker.steps) == 2
        assert tracker.steps[0]["label"] == "Step 1"
        assert tracker.steps[0]["index"] == 0
        assert tracker.steps[1]["label"] == "Step 2"
        assert tracker.steps[1]["index"] == 1
        assert "conv-123" in tracker.conversation_ids

    def test_step_truncation(self):
        """Long system prompts and inputs are truncated."""
        tracker = WorkflowTracker(title="Test")
        tracker.add_step(
            model="test",
            label="Long step",
            system_prompt="x" * 1000,
            user_input="y" * 5000,
            output="z" * 5000,
        )

        assert len(tracker.steps[0]["systemPrompt"]) == 500
        assert len(tracker.steps[0]["input"]) == 2000
        assert len(tracker.steps[0]["output"]) == 2000

    def test_empty_workflow_returns_none(self):
        """An empty tracker should not post anything."""
        import asyncio

        tracker = WorkflowTracker(title="Empty")
        result = asyncio.get_event_loop().run_until_complete(tracker.post_workflow())
        assert result is None

    @pytest.mark.asyncio
    async def test_post_workflow_sends_correct_payload(self):
        """Verify POST /workflows is called with the correct shape."""
        tracker = WorkflowTracker(title="Test Pipeline", source="test")
        tracker.add_step(
            model="test-model",
            label="Discovery",
            system_prompt="Find tickers",
            user_input="Scan Reddit",
            output="Found: AAPL, TSLA",
            duration=10.0,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "id": "wf-abc-123"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.patch = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tracker.post_workflow()

        assert result == "wf-abc-123"

        # Check the POST call
        post_call = mock_client.post.call_args
        payload = post_call[1].get("json", {})

        assert payload["title"] == "Test Pipeline"
        assert payload["source"] == "test"
        assert len(payload["steps"]) == 1
        assert payload["steps"][0]["model"] == "test-model"
        assert payload["steps"][0]["label"] == "Discovery"
        assert payload["stepCount"] == 1
        assert "totalDuration" in payload

        # Check headers
        headers = post_call[1].get("headers", {})
        assert "x-api-secret" in headers
        assert "x-project" in headers

    @pytest.mark.asyncio
    async def test_post_workflow_links_conversations(self):
        """Verify conversation IDs are linked via PATCH."""
        tracker = WorkflowTracker(title="Test")
        tracker.add_step(
            model="test",
            label="Step 1",
            conversation_id="conv-aaa",
        )
        tracker.add_step(
            model="test",
            label="Step 2",
            conversation_id="conv-bbb",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "id": "wf-123"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.patch = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await tracker.post_workflow()

        # Verify PATCH was called for conversation linking
        mock_client.patch.assert_called_once()
        patch_call = mock_client.patch.call_args
        patch_payload = patch_call[1].get("json", {})
        assert patch_payload["conversationIds"] == ["conv-aaa", "conv-bbb"]

    @pytest.mark.asyncio
    async def test_post_workflow_handles_failure_gracefully(self):
        """Workflow posting failure should not raise."""
        tracker = WorkflowTracker(title="Test")
        tracker.add_step(model="test", label="Step 1")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tracker.post_workflow()

        assert result is None  # Should not raise, just return None
