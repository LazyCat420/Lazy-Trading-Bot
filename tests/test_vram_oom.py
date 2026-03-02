"""Tests for VRAM estimation + Empirical Memory Audit system.

Tests:
1. estimate_model_vram() — KV cache math (unit test)
2. TestFastPath — cached proven_max_ctx → single load
3. TestAuditScaleUp — no cache → steps through ctx → saves limit
4. TestAuditOOM — all loads fail → oom_error
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_resp(status_code: int = 200, json_data: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


TAGS_DATA = {
    "models": [{"name": "gemma3:27b", "size": 17_800_000_000}],
}
SHOW_DATA = {
    "model_info": {
        "general.architecture": "gemma3",
        "gemma3.block_count": 28,
        "gemma3.attention.head_count": 16,
        "gemma3.attention.head_count_kv": 4,
        "gemma3.attention.key_length": 128,
        "gemma3.embedding_length": 2048,
        "gemma3.context_length": 128000,
    },
}


@pytest.fixture(autouse=True)
def _reset():
    from app.config import settings

    settings.LLM_VRAM_MEASUREMENTS = {}
    settings.LLM_CONTEXT_SIZE = 60000
    yield
    settings.LLM_VRAM_MEASUREMENTS = {}
    settings.LLM_CONTEXT_SIZE = 8192


# ---------------------------------------------------------------------------
# Unit: estimate_model_vram (now public)
# ---------------------------------------------------------------------------


class TestEstimateModelVram:
    def test_kv_cache_math(self):
        from app.services.llm_service import LLMService

        est = LLMService.estimate_model_vram(
            SHOW_DATA["model_info"],
            model_file_size=17_800_000_000,
            num_ctx=60000,
        )
        assert est["fields_found"] is True
        # KV per token = 2 * 28 * 4 * 128 * 2 = 57,344
        assert est["kv_bytes_per_token"] == 57_344
        assert est["kv_bytes"] == 57_344 * 60000
        assert est["weights_bytes"] == 17_800_000_000
        assert est["total_bytes"] == 17_800_000_000 + 57_344 * 60000 + int(
            0.5 * (1024**3)
        )

    def test_missing_fields_returns_weights_only(self):
        from app.services.llm_service import LLMService

        est = LLMService.estimate_model_vram(
            {"general.architecture": "unknown"},
            model_file_size=10_000_000_000,
            num_ctx=60000,
        )
        assert est["fields_found"] is False
        assert est["kv_bytes"] == 0
        assert est["total_bytes"] == 10_000_000_000 + int(0.5 * (1024**3))


# ---------------------------------------------------------------------------
# FAST PATH: Cached proven_max_ctx → single load
# ---------------------------------------------------------------------------


class TestFastPath:
    def test_uses_cached_proven_max_ctx(self):
        """With cached proven_max_ctx, loads at min(desired, proven)."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 60000
        settings.LLM_VRAM_MEASUREMENTS = {
            "gemma3:27b": {"proven_max_ctx": 32768},
        }

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                return _mock_resp(200, {})
            return _mock_resp(200, {})

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            return _mock_resp(200, {})

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            with (
                patch.object(
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_all_ollama_models",
                    return_value=0,
                ),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434",
                        "gemma3:27b",
                    )
                )

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        assert result["audit_performed"] is False
        # desired=60000 but proven limit is 32768
        assert result["recommended_ctx"] == 32768
        assert result["proven_max_ctx"] == 32768
        assert "clamped_from" in result


# ---------------------------------------------------------------------------
# AUDIT PATH: No cache → step through ctx sizes → find limit
# ---------------------------------------------------------------------------


class TestAuditScaleUp:
    def test_steps_up_and_finds_limit(self):
        """No cache → audit steps through ctx sizes → saves limit."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 60000
        settings.LLM_VRAM_MEASUREMENTS = {}

        # Simulate: ctx 2048-32768 succeed, 49152 fails
        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                body = kwargs.get("json", {})
                ctx = body.get("options", {}).get("num_ctx", 0)
                ka = body.get("keep_alive")
                # Unload calls (keep_alive="0") always succeed
                if ka == "0" or ka == 0:
                    return _mock_resp(200, {})
                # Loads at ctx <= 32768 succeed, above fail
                if ctx > 0 and ctx > 32768:
                    return _mock_resp(500, {})
                return _mock_resp(200, {})
            return _mock_resp(200, {})

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            return _mock_resp(200, {})

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            with (
                patch.object(
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_ollama_model",
                    return_value=True,
                ) as mock_unload,
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434",
                        "gemma3:27b",
                    )
                )

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        assert result["audit_performed"] is True
        assert result["proven_max_ctx"] == 32768
        assert result["recommended_ctx"] == 32768
        # Cache should be populated with proven_max_ctx
        assert settings.LLM_VRAM_MEASUREMENTS["gemma3:27b"]["proven_max_ctx"] == 32768
        # unload_ollama_model called between each step
        assert mock_unload.call_count >= 1


# ---------------------------------------------------------------------------
# AUDIT OOM: Even smallest context fails
# ---------------------------------------------------------------------------


class TestAuditOOM:
    def test_returns_oom_when_all_loads_fail(self):
        """No cache → all audit steps fail → oom_error."""
        from app.services.llm_service import LLMService

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                body = kwargs.get("json", {})
                ka = body.get("keep_alive")
                # Unload calls succeed
                if ka == "0" or ka == 0:
                    return _mock_resp(200, {})
                # ALL loads fail
                return _mock_resp(500, {})
            return _mock_resp(200, {})

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            return _mock_resp(200, {})

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            with (
                patch.object(
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_ollama_model",
                    return_value=True,
                ),
                patch("asyncio.sleep", new_callable=AsyncMock),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434",
                        "gemma3:27b",
                    )
                )

        assert result["status"] == "oom_error"
        assert result["model_found"] is True
