"""Tests for VRAM estimation + flush + OOM prevention.

Tests:
1. estimate_model_vram() — KV cache math
2. Predicted OOM (85% ceiling) — returns error without loading
3. Estimate OK → flush → successful warm
4. Unexpected OOM — evicts, suggests 75%
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
        assert est["total_bytes"] == 17_800_000_000 + 57_344 * 60000

    def test_missing_fields_returns_weights_only(self):
        from app.services.llm_service import LLMService

        est = LLMService.estimate_model_vram(
            {"general.architecture": "unknown"},
            model_file_size=10_000_000_000,
            num_ctx=60000,
        )
        assert est["fields_found"] is False
        assert est["kv_bytes"] == 0
        assert est["total_bytes"] == 10_000_000_000


# ---------------------------------------------------------------------------
# Predicted OOM — estimate exceeds 85% of total GPU
# ---------------------------------------------------------------------------

class TestPredictedOOM:
    def test_returns_oom_without_loading(self):
        """When estimate > 85% of total GPU, return oom_error without any
        /api/generate call (zero model loads)."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 128000

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            raise AssertionError(f"Should not call: {url}")

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            raise AssertionError(f"Unexpected GET: {url}")

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            # Total GPU = 20 GiB. 85% ceiling = 17 GiB.
            # Estimate for 128k ctx ≈ 17.8 + 7.0 = ~24.8 GiB → exceeds
            with patch.object(
                LLMService, "get_total_gpu_memory_bytes",
                return_value=20 * 1024**3,
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434", "gemma3:27b",
                    )
                )

        assert result["status"] == "oom_error"
        assert result["pre_warmed"] is False
        assert result["suggested_ctx"] > 0
        assert result["suggested_ctx"] < 128000
        assert "total_vram_gb" in result
        # /api/generate never called
        gen_calls = [c for c in client.post.call_args_list
                     if "/api/generate" in str(c)]
        assert len(gen_calls) == 0


# ---------------------------------------------------------------------------
# Estimate OK → flush → warm
# ---------------------------------------------------------------------------

class TestEstimateOKWarm:
    def test_loads_after_flush(self):
        """When estimate fits in 85% ceiling, flush VRAM then load."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 60000

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                return _mock_resp(200, {})
            return _mock_resp(200, {})

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            if "/api/ps" in url:
                return _mock_resp(200, {
                    "models": [{"name": "gemma3:27b",
                                "size_vram": 21_000_000_000}],
                })
            return _mock_resp(200, {})

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            # Total GPU = 64 GiB → 85% = 54.4 GiB.
            # Estimate ≈ 17.8 + 3.3 = ~21 GiB → fits
            with (
                patch.object(
                    LLMService, "get_total_gpu_memory_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService, "unload_all_ollama_models",
                    return_value=0,
                ) as mock_flush,
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434", "gemma3:27b",
                    )
                )

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        assert result["recommended_ctx"] == 60000
        # Flush was called before load
        mock_flush.assert_called_once()
        assert settings.LLM_VRAM_MEASUREMENTS["gemma3:27b"]["ctx"] == 60000


# ---------------------------------------------------------------------------
# Unexpected OOM — estimate was wrong
# ---------------------------------------------------------------------------

class TestUnexpectedOOM:
    def test_evicts_and_suggests_75pct(self):
        """If estimate says OK but load OOMs, evict and suggest 75%."""
        from app.services.llm_service import LLMService

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                json_body = kwargs.get("json", {})
                if json_body.get("keep_alive") == "0":
                    return _mock_resp(200, {})
                return _mock_resp(500, {})  # OOM
            return _mock_resp(200, {})

        async def mock_get(url, **kwargs):
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            if "/api/ps" in url:
                return _mock_resp(200, {"models": []})
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
                    LLMService, "get_total_gpu_memory_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService, "unload_all_ollama_models",
                    return_value=0,
                ),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434", "gemma3:27b",
                    )
                )

        assert result["status"] == "oom_error"
        assert result["suggested_ctx"] == (int(60000 * 0.75) // 4096) * 4096
        assert "total_vram_gb" in result
