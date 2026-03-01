"""Ollama LLM service — sends chat requests to Ollama.

The Ollama URL is centralized in app.config.settings:
    OLLAMA_URL — Ollama endpoint (default http://localhost:11434)

Uses a module-level shared httpx.AsyncClient for connection pooling.
This is critical for parallel LLM calls — when OLLAMA_NUM_PARALLEL > 1,
multiple agents can share the same TCP connection pool instead of each
creating and destroying their own connection.
"""

from __future__ import annotations

import re
import time

import httpx

from app.config import settings
from app.services.pipeline_health import log_llm_call
from app.utils.logger import logger

# Shared async HTTP client — reused across all LLM calls for connection pooling.
# Created lazily on first use; lives for the entire app lifecycle.
_shared_client: httpx.AsyncClient | None = None


async def _get_shared_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient."""
    global _shared_client  # noqa: PLW0603
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,  # Fail fast if server is unreachable
                read=300.0,  # LLM inference can be slow
                write=30.0,  # Sending large prompts
                pool=30.0,  # Waiting for a connection slot
            ),
            limits=httpx.Limits(
                max_connections=20,  # Up to 20 parallel TCP connections
                max_keepalive_connections=10,
            ),
        )
    return _shared_client


class LLMService:
    """Sends chat completion requests to Ollama.

    All config values (model, context_size, temperature) are read LIVE
    from settings on every call, so hot-patching via the Settings UI
    takes effect immediately — no restart needed.
    """

    @property
    def base_url(self) -> str:
        return settings.LLM_BASE_URL

    @property
    def model(self) -> str:
        return settings.LLM_MODEL

    @property
    def temperature(self) -> float:
        return settings.LLM_TEMPERATURE

    @property
    def context_size(self) -> int:
        return settings.LLM_CONTEXT_SIZE

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token for English text."""
        return len(text) // 4

    async def chat(
        self,
        system: str,
        user: str,
        *,
        response_format: str = "json",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Args:
            system: The system prompt.
            user: The user message (data context).
            response_format: "json" to hint at JSON output, "text" for free-form.
            max_tokens: Optional max token limit for the response.
            temperature: Optional per-request temperature override.
                         If None, uses the global setting from config.

        Returns:
            The raw string response from the LLM.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        # Resolve effective temperature (per-request override > global config)
        effective_temp = temperature if temperature is not None else self.temperature

        return await self._call_ollama(
            messages, response_format, max_tokens, effective_temp
        )

    async def _call_ollama(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        temperature: float,
    ) -> str:
        """Call the Ollama /api/chat endpoint using shared connection pool."""
        url = f"{self.base_url}/api/chat"

        # Context size: always send num_ctx so Ollama doesn't try to
        # allocate the model's full default context (often 128K → OOM).
        # Use the value from settings, which is set to a proven-safe max
        # by verify_and_warm_ollama_model at startup.
        effective_ctx = self.context_size if self.context_size > 0 else 8192

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "2h",
            "options": {
                "temperature": temperature,
                "num_ctx": effective_ctx,
            },
        }
        if response_format == "json":
            payload["format"] = "json"

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        logger.info(
            "⏱️  Ollama request START → %s model=%s format=%s",
            url,
            self.model,
            response_format,
        )
        t0 = time.perf_counter()

        # Derive a short context label from the system prompt
        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"

        try:
            client = await _get_shared_client()
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.ReadTimeout:
            elapsed = time.perf_counter() - t0
            logger.error(
                "⏱️  Ollama request TIMEOUT after %.1fs", elapsed,
            )
            log_llm_call(
                context=_ctx,
                model=self.model,
                duration_seconds=elapsed,
                timed_out=True,
            )
            raise
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            log_llm_call(
                context=_ctx,
                model=self.model,
                duration_seconds=elapsed,
                error=str(exc)[:120],
            )
            raise

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        tokens = data.get("eval_count", 0)

        elapsed = time.perf_counter() - t0
        logger.info(
            "⏱️  Ollama request DONE  → %.2fs, %d chars",
            elapsed,
            len(content),
        )
        log_llm_call(
            context=_ctx,
            model=self.model,
            duration_seconds=elapsed,
            tokens_used=tokens,
        )
        return content

    @staticmethod
    def _trim_messages(messages: list[dict]) -> list[dict]:
        """Trim the longest message by ~40% to fit within context window."""
        trimmed = []
        # Find the longest message
        longest_idx = max(
            range(len(messages)),
            key=lambda i: len(messages[i].get("content", "")),
        )
        for i, msg in enumerate(messages):
            if i == longest_idx:
                content = msg["content"]
                # Keep ~60% of the content, cutting from the middle
                keep = int(len(content) * 0.6)
                half = keep // 2
                trimmed_content = (
                    content[:half]
                    + "\n\n[... content trimmed for context window ...]"
                    + content[-half:]
                )
                trimmed.append({**msg, "content": trimmed_content})
                logger.info(
                    "✂️  Trimmed message[%d] from %d → %d chars",
                    i,
                    len(content),
                    len(trimmed_content),
                )
            else:
                trimmed.append(msg)
        return trimmed

    @staticmethod
    def clean_json_response(raw: str) -> str:
        """Strip markdown code fences and extract the FIRST complete JSON object.

        LLMs often wrap their JSON in ```json ... ``` markers, or output
        multiple JSON objects in one response.  We use brace-depth counting
        to extract only the first complete {...} object.
        """
        # Strip markdown code blocks
        cleaned = re.sub(r"```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # Find the first '{' and use brace-depth counting to find its match
        start = cleaned.find("{")
        if start == -1:
            return cleaned  # No JSON object at all

        depth = 0
        in_string = False
        escape_next = False
        end = -1

        for i in range(start, len(cleaned)):
            ch = cleaned[i]

            if escape_next:
                escape_next = False
                continue

            if ch == "\\":
                escape_next = True
                continue

            if ch == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end != -1:
            return cleaned[start : end + 1]

        # Incomplete object (truncated by max_tokens) — return what we have
        return cleaned[start:]

    @staticmethod
    async def fetch_models(base_url: str) -> list[str]:
        """Probe an Ollama URL and return available model names.

        Works independently of the current config — used by the frontend
        to test arbitrary URLs before saving them.
        """
        base_url = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                return [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            return []

    @staticmethod
    async def unload_ollama_model(base_url: str, model: str) -> bool:
        """Immediately evict an Ollama model from VRAM.

        Sends POST /api/generate with keep_alive="0" which tells Ollama
        to unload the model from memory immediately.

        Returns True if the unload request succeeded.
        """
        base_url = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": "",
                        "keep_alive": "0",
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                logger.info(
                    "[LLM] Ollama model %s unloaded (keep_alive=0)", model,
                )
                return True
        except Exception as exc:
            logger.warning(
                "[LLM] Failed to unload Ollama model %s: %s", model, exc,
            )
            return False

    @staticmethod
    async def unload_all_ollama_models(base_url: str) -> int:
        """Unload ALL models currently loaded in Ollama VRAM.

        GET /api/ps to list running models, then unload each one.
        Returns the number of models successfully unloaded.
        """
        base_url = base_url.rstrip("/")
        unloaded = 0
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                ps_resp = await client.get(f"{base_url}/api/ps")
                ps_resp.raise_for_status()
                ps_models = ps_resp.json().get("models", [])

                for m in ps_models:
                    model_name = m.get("name", "")
                    if not model_name:
                        continue
                    try:
                        await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model_name,
                                "prompt": "",
                                "keep_alive": "0",
                                "stream": False,
                            },
                        )
                        unloaded += 1
                        logger.info(
                            "[LLM] Unloaded Ollama model: %s", model_name,
                        )
                    except Exception:
                        logger.warning(
                            "[LLM] Failed to unload Ollama model %s",
                            model_name,
                        )
        except Exception as exc:
            logger.warning("[LLM] Ollama unload sweep failed: %s", exc)

        logger.info(
            "[LLM] Ollama unload complete: %d models freed", unloaded,
        )
        return unloaded

    @staticmethod
    def get_free_gpu_memory_bytes() -> int:
        """Query nvidia-smi for free GPU memory.  Returns bytes, or 0."""
        import subprocess

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                free_mib = int(result.stdout.strip().split("\n")[0].strip())
                return free_mib * 1024 * 1024
        except Exception as exc:
            logger.debug("[LLM] nvidia-smi failed: %s", exc)
        return 0

    @staticmethod
    def get_total_gpu_memory_bytes() -> int:
        """Query nvidia-smi for total GPU memory.  Returns bytes, or 0."""
        import subprocess

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                total_mib = int(result.stdout.strip().split("\n")[0].strip())
                return total_mib * 1024 * 1024
        except Exception as exc:
            logger.debug("[LLM] nvidia-smi total failed: %s", exc)
        return 0

    @staticmethod
    def estimate_model_vram(
        model_info: dict,
        model_file_size: int,
        num_ctx: int,
    ) -> dict:
        """Estimate total VRAM for a model at a given context length.

        Uses the model architecture from /api/show's model_info to
        calculate KV cache size.  Model weight VRAM ≈ GGUF file size.

        Returns {"total_bytes", "weights_bytes", "kv_bytes",
                 "kv_bytes_per_token", "fields_found"}.
        """
        # Extract architecture fields
        block_count = 0
        head_count_kv = 0
        head_dim = 0
        embed_len = 0
        head_count = 0

        for key, val in model_info.items():
            if not isinstance(val, int):
                continue
            if "block_count" in key:
                block_count = val
            elif key.endswith(".attention.head_count_kv"):
                head_count_kv = val
            elif "key_length" in key:
                head_dim = val
            elif "embedding_length" in key:
                embed_len = val
            elif key.endswith(".attention.head_count"):
                head_count = val

        # Derive head_dim if not explicitly available
        if head_dim == 0 and embed_len > 0 and head_count > 0:
            head_dim = embed_len // head_count

        fields_found = bool(block_count and head_count_kv and head_dim)

        kv_bytes_per_token = 0
        kv_bytes = 0
        if fields_found:
            # KV cache = 2 (K+V) × layers × kv_heads × head_dim × 2 (FP16)
            kv_bytes_per_token = 2 * block_count * head_count_kv * head_dim * 2
            kv_bytes = kv_bytes_per_token * num_ctx

        return {
            "total_bytes": model_file_size + kv_bytes,
            "weights_bytes": model_file_size,
            "kv_bytes": kv_bytes,
            "kv_bytes_per_token": kv_bytes_per_token,
            "fields_found": fields_found,
        }

    @staticmethod
    async def verify_and_warm_ollama_model(
        base_url: str,
        model: str,
        *,
        keep_alive: str = "10m",
    ) -> dict:
        """Verify an Ollama model exists, estimate VRAM, and pre-warm it.

        Flow:
          1. GET /api/tags  → verify model exists, get file size
          2. POST /api/show → get architecture (layers, kv_heads, head_dim)
          3. nvidia-smi      → get free GPU memory
          4. MATH            → estimate total VRAM needed
          5. If estimated > free → return oom_error + suggested_ctx (NO load)
          6. If estimated ≤ free → load the model (single attempt)

        Returns dict with model info.  On predicted/actual OOM, returns
        status="oom_error" with suggested_ctx.
        """
        base_url = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                # ── Step 1: Verify model exists ──────────────────
                tags_resp = await client.get(f"{base_url}/api/tags")
                tags_resp.raise_for_status()
                tags_data = tags_resp.json().get("models", [])
                available = [m["name"] for m in tags_data]

                model_found = model in available
                model_file_size = 0

                if not model_found:
                    alt = (
                        f"{model}:latest"
                        if ":" not in model
                        else model.split(":")[0]
                    )
                    for avail_model in tags_data:
                        avail_name = avail_model.get("name", "")
                        if (
                            avail_name == alt
                            or avail_name.split(":")[0]
                            == model.split(":")[0]
                        ):
                            model_found = True
                            model_file_size = avail_model.get("size", 0)
                            logger.info(
                                "[LLM] Fuzzy match: '%s' → '%s'",
                                model, avail_name,
                            )
                            break
                else:
                    for m_tag in tags_data:
                        if m_tag["name"] == model:
                            model_file_size = m_tag.get("size", 0)
                            break

                model_max_ctx = 0
                recommended_ctx = 8192
                model_info: dict = {}

                if not model_found:
                    return {
                        "status": "model_not_found",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": False,
                        "pre_warmed": False,
                        "model_max_ctx": 0,
                        "recommended_ctx": 8192,
                        "vram_bytes": 0,
                    }

                # ── Step 2: Query architecture ───────────────────
                try:
                    show_resp = await client.post(
                        f"{base_url}/api/show",
                        json={"name": model},
                    )
                    show_resp.raise_for_status()
                    show_data = show_resp.json()
                    model_info = show_data.get("model_info", {})

                    for key, val in model_info.items():
                        if "context_length" in key and isinstance(
                            val, int,
                        ):
                            model_max_ctx = val
                            break

                    logger.info(
                        "[LLM] Model %s: max_ctx=%d file_size=%.1f GiB",
                        model,
                        model_max_ctx,
                        model_file_size / (1024**3) if model_file_size else 0,
                    )
                except Exception as exc:
                    logger.warning(
                        "[LLM] Could not query model info: %s", exc,
                    )

                # ── Step 3: Determine desired context ────────────
                from app.config import settings as _cfg

                desired_ctx = _cfg.LLM_CONTEXT_SIZE
                if model_max_ctx > 0:
                    desired_ctx = min(desired_ctx, model_max_ctx)
                desired_ctx = max(desired_ctx, 2048)

                # ── Step 4: Estimate VRAM ────────────────────────
                estimate = LLMService.estimate_model_vram(
                    model_info, model_file_size, desired_ctx,
                )
                total_gpu = LLMService.get_total_gpu_memory_bytes()
                kv_per_tok = estimate["kv_bytes_per_token"]

                est_gb = estimate["total_bytes"] / (1024**3)
                total_gb = total_gpu / (1024**3) if total_gpu else 0

                logger.info(
                    "[LLM] VRAM estimate for %s @ ctx=%d: "
                    "%.1f GiB needed (weights=%.1f + KV=%.1f), "
                    "%.1f GiB total GPU",
                    model,
                    desired_ctx,
                    est_gb,
                    estimate["weights_bytes"] / (1024**3),
                    estimate["kv_bytes"] / (1024**3),
                    total_gb,
                )

                # ── Step 5: Check if it fits ─────────────────────
                # Use 85% of total GPU as the safe ceiling
                # (leaves room for OS/Ubuntu overhead on Jetson)
                safe_ceiling = int(total_gpu * 0.85) if total_gpu else 0
                if (
                    safe_ceiling > 0
                    and estimate["fields_found"]
                    and estimate["total_bytes"] > safe_ceiling
                ):
                    # Will NOT fit — calculate max ctx that DOES fit
                    available_for_kv = max(
                        safe_ceiling - estimate["weights_bytes"], 0,
                    )

                    if kv_per_tok > 0:
                        suggested_ctx = (
                            available_for_kv // kv_per_tok // 4096
                        ) * 4096
                    else:
                        suggested_ctx = 8192
                    suggested_ctx = max(suggested_ctx, 2048)

                    sug_est = LLMService.estimate_model_vram(
                        model_info, model_file_size, suggested_ctx,
                    )

                    message = (
                        f"Estimated VRAM for ctx={desired_ctx:,}: "
                        f"{est_gb:.1f} GiB, but safe ceiling is "
                        f"{safe_ceiling / (1024**3):.1f} GiB "
                        f"(85% of {total_gb:.0f} GiB). "
                        f"Max safe context: {suggested_ctx:,} "
                        f"tokens (~{sug_est['total_bytes'] / (1024**3):.1f}"
                        f" GiB)."
                    )
                    logger.warning("[LLM] %s", message)

                    return {
                        "status": "oom_error",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": True,
                        "pre_warmed": False,
                        "model_max_ctx": model_max_ctx,
                        "requested_ctx": desired_ctx,
                        "suggested_ctx": suggested_ctx,
                        "kv_rate_bytes_per_token": kv_per_tok,
                        "estimated_vram_gb": round(est_gb, 1),
                        "total_vram_gb": round(total_gb, 1),
                        "message": message,
                    }

                # ── Step 5b: Flush VRAM before loading ──────────
                # Evict ALL models so this model gets 100% VRAM
                import asyncio
                try:
                    freed = await LLMService.unload_all_ollama_models(
                        base_url,
                    )
                    if freed > 0:
                        logger.info(
                            "[LLM] Flushed %d model(s) before load",
                            freed,
                        )
                        await asyncio.sleep(2)
                        # Verify VRAM is clear
                        ps_check = await client.get(
                            f"{base_url}/api/ps",
                        )
                        still_loaded = len(
                            ps_check.json().get("models", []),
                        )
                        if still_loaded:
                            logger.warning(
                                "[LLM] %d model(s) still in VRAM "
                                "after flush",
                                still_loaded,
                            )
                except Exception as flush_exc:
                    logger.warning(
                        "[LLM] VRAM flush failed: %s", flush_exc,
                    )

                # ── Step 6: Load the model ───────────────────────
                logger.info(
                    "[LLM] Estimate OK — warming %s at num_ctx=%d …",
                    model, desired_ctx,
                )
                try:
                    warm_resp = await client.post(
                        f"{base_url}/api/generate",
                        json={
                            "model": model,
                            "prompt": "",
                            "keep_alive": keep_alive,
                            "stream": False,
                            "options": {
                                "num_ctx": desired_ctx,
                            },
                        },
                    )
                    warm_resp.raise_for_status()
                    recommended_ctx = desired_ctx
                    logger.info(
                        "[LLM] ✅ Model %s warmed at num_ctx=%d",
                        model, desired_ctx,
                    )

                    # Measure actual VRAM via /api/ps
                    size_vram = 0
                    try:
                        ps_resp = await client.get(
                            f"{base_url}/api/ps",
                        )
                        ps_resp.raise_for_status()
                        for m_info in ps_resp.json().get("models", []):
                            m_name = m_info.get("name", "")
                            if (
                                m_name == model
                                or m_name.split(":")[0]
                                == model.split(":")[0]
                            ):
                                size_vram = m_info.get("size_vram", 0)
                                break
                    except Exception:
                        pass

                    # Cache the measurement
                    if size_vram:
                        _cfg.LLM_VRAM_MEASUREMENTS[model] = {
                            "ctx": desired_ctx,
                            "size_vram": size_vram,
                            "kv_rate": kv_per_tok,
                        }

                    return {
                        "status": "model_verified",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": True,
                        "pre_warmed": True,
                        "model_max_ctx": model_max_ctx,
                        "recommended_ctx": recommended_ctx,
                        "vram_bytes": size_vram,
                        "kv_rate_bytes_per_token": kv_per_tok,
                    }

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 500:
                        raise
                    # Unexpected OOM (estimate said it would fit)
                    # Evict and return error — NO retry
                    logger.warning(
                        "[LLM] Unexpected OOM at ctx=%d for %s "
                        "(estimate said it would fit)",
                        desired_ctx, model,
                    )
                    try:
                        await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model,
                                "prompt": "",
                                "keep_alive": "0",
                                "stream": False,
                            },
                        )
                    except Exception:
                        pass

                    # Suggest 75% of what we tried, rounded
                    suggested_ctx = (
                        int(desired_ctx * 0.75) // 4096
                    ) * 4096
                    suggested_ctx = max(suggested_ctx, 2048)

                    cached = _cfg.LLM_VRAM_MEASUREMENTS.get(model)
                    if cached:
                        suggested_ctx = cached["ctx"]

                    return {
                        "status": "oom_error",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": True,
                        "pre_warmed": False,
                        "model_max_ctx": model_max_ctx,
                        "requested_ctx": desired_ctx,
                        "suggested_ctx": suggested_ctx,
                        "kv_rate_bytes_per_token": kv_per_tok,
                        "estimated_vram_gb": round(est_gb, 1),
                        "total_vram_gb": round(total_gb, 1),
                        "message": (
                            f"OOM at {desired_ctx:,} tokens "
                            f"(estimate: {est_gb:.1f} GiB, "
                            f"total: {total_gb:.1f} GiB). "
                            f"Suggested: {suggested_ctx:,}."
                        ),
                    }

        except Exception as exc:
            logger.warning(
                "[LLM] Ollama model verification failed: %s", exc,
            )
            return {
                "status": "verification_failed",
                "error": str(exc),
            }

    async def health_check(self) -> dict:
        """Check connectivity to the Ollama backend."""
        try:
            url = f"{self.base_url}/api/tags"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                return {
                    "status": "ok",
                    "provider": "ollama",
                    "active_url": self.base_url,
                    "ollama_url": settings.OLLAMA_URL,
                    "models": models,
                    "configured_model": self.model,
                    "model_available": self.model in models,
                }
        except Exception as e:
            return {
                "status": "error",
                "provider": "ollama",
                "active_url": self.base_url,
                "ollama_url": settings.OLLAMA_URL,
                "error": str(e),
            }
