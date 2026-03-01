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
        assert est["total_bytes"] == 17_800_000_000 + 57_344 * 60000 + int(
            1.5 * (1024**3)
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
        assert est["total_bytes"] == 10_000_000_000 + int(1.5 * (1024**3))


# ---------------------------------------------------------------------------
# Predicted OOM — estimate exceeds 85% of total GPU
# ---------------------------------------------------------------------------


class TestPredictedOOM:
    def test_clamps_ctx_when_exceeds_ceiling(self):
        """When estimate > safe ceiling, clamp ctx and load at lower value."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 128000

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
                return _mock_resp(
                    200,
                    {
                        "models": [{"name": "gemma3:27b", "size_vram": 19_000_000_000}],
                    },
                )
            return _mock_resp(200, {})

        with patch("httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=mock_post)
            client.get = AsyncMock(side_effect=mock_get)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            # Total GPU = 20 GiB. Safe ceiling = 15 GiB.
            # Estimate for 128k ctx ≈ 17.8 + 7.0 = ~24.8 GiB → exceeds
            # Code should clamp ctx down and still load
            with (
                patch.object(
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=20 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_all_ollama_models",
                    return_value=0,
                ),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434",
                        "gemma3:27b",
                    )
                )

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        # Context should be clamped below 128000
        assert result["recommended_ctx"] < 128000


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

        ps_call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal ps_call_count
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            if "/api/ps" in url:
                ps_call_count += 1
                if ps_call_count == 1:
                    # Pre-load check: model not loaded yet
                    return _mock_resp(200, {"models": []})
                # Post-load measurement
                return _mock_resp(
                    200,
                    {
                        "models": [{"name": "gemma3:27b", "size_vram": 21_000_000_000}],
                    },
                )
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
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_all_ollama_models",
                    return_value=0,
                ) as mock_flush,
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    LLMService.verify_and_warm_ollama_model(
                        "http://localhost:11434",
                        "gemma3:27b",
                    )
                )

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        assert result["recommended_ctx"] == 60000
        # Flush was called before load
        mock_flush.assert_called_once()
        assert settings.LLM_VRAM_MEASUREMENTS["gemma3:27b"]["ctx"] == 60000


# ---------------------------------------------------------------------------
# Anchor-and-Scale-Up: successful anchor + scale-up
# ---------------------------------------------------------------------------


class TestAnchorScaleUp:
    def test_anchor_then_scale_up(self):
        """OOM at full ctx → anchor at 50% → measure → scale up."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 60000

        first_load = True  # track first /api/generate call

        async def mock_post(url, **kwargs):
            nonlocal first_load
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                json_body = kwargs.get("json", {})
                # Unload requests always succeed
                if json_body.get("keep_alive") == "0":
                    return _mock_resp(200, {})
                # First load attempt OOMs
                if first_load:
                    first_load = False
                    return _mock_resp(500, {})
                # Anchor and scale-up loads succeed
                return _mock_resp(200, {})
            return _mock_resp(200, {})

        ps_call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal ps_call_count
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            if "/api/ps" in url:
                ps_call_count += 1
                if ps_call_count == 1:
                    # Pre-load check: model not loaded yet
                    return _mock_resp(200, {"models": []})
                # Post-anchor measurement: realistic VRAM
                # weights=17.8GB + graph=1.5GB + KV for 30k ctx
                # KV = 57344 * 30000 ≈ 1.6 GB → total ≈ 20.9 GB
                return _mock_resp(
                    200,
                    {
                        "models": [
                            {
                                "name": "gemma3:27b",
                                "size_vram": 20_900_000_000,
                            }
                        ],
                    },
                )
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

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        # Anchor = 30000 (60000 * 0.5 rounded to 1024).
        # Should scale UP beyond anchor.
        assert result["recommended_ctx"] > 30000


# ---------------------------------------------------------------------------
# Anchor-and-Scale-Up: no headroom to scale up
# ---------------------------------------------------------------------------


class TestAnchorNoScaleUp:
    def test_stays_at_anchor_when_no_telemetry(self):
        """OOM → anchor at 50% → no VRAM telemetry → stay at anchor."""
        from app.config import settings
        from app.services.llm_service import LLMService

        settings.LLM_CONTEXT_SIZE = 60000

        first_load = True

        async def mock_post(url, **kwargs):
            nonlocal first_load
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                json_body = kwargs.get("json", {})
                if json_body.get("keep_alive") == "0":
                    return _mock_resp(200, {})
                if first_load:
                    first_load = False
                    return _mock_resp(500, {})
                return _mock_resp(200, {})
            return _mock_resp(200, {})

        ps_call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal ps_call_count
            if "/api/tags" in url:
                return _mock_resp(200, TAGS_DATA)
            if "/api/ps" in url:
                ps_call_count += 1
                if ps_call_count == 1:
                    # Pre-load check: model not loaded yet
                    return _mock_resp(200, {"models": []})
                # After anchor load: model present but no size_vram
                return _mock_resp(
                    200,
                    {
                        "models": [
                            {
                                "name": "gemma3:27b",
                                "size_vram": 0,
                            }
                        ],
                    },
                )
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

        assert result["status"] == "model_verified"
        assert result["pre_warmed"] is True
        # No telemetry → stays at anchor
        anchor = max(int(60000 * 0.5) // 1024 * 1024, 2048)
        assert result["recommended_ctx"] == anchor


# ---------------------------------------------------------------------------
# Anchor-and-Scale-Up: anchor itself OOMs (model truly too big)
# ---------------------------------------------------------------------------


class TestAnchorFallback:
    def test_returns_oom_when_anchor_fails(self):
        """OOM at full ctx → anchor at 50% also OOMs → return oom_error."""
        from app.services.llm_service import LLMService

        async def mock_post(url, **kwargs):
            if "/api/show" in url:
                return _mock_resp(200, SHOW_DATA)
            if "/api/generate" in url:
                json_body = kwargs.get("json", {})
                if json_body.get("keep_alive") == "0":
                    return _mock_resp(200, {})
                # ALL loads fail
                return _mock_resp(500, {})
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
                    LLMService,
                    "get_total_vram_bytes",
                    return_value=64 * 1024**3,
                ),
                patch.object(
                    LLMService,
                    "unload_all_ollama_models",
                    return_value=0,
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
        assert result["suggested_ctx"] == 2048
        assert "total_vram_gb" in result
