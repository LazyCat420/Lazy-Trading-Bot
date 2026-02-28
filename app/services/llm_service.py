"""Provider-agnostic LLM service — supports Ollama and LM Studio.

Provider URLs are centralized in app.config.settings:
    OLLAMA_URL   — Ollama endpoint (default http://localhost:11434)
    LMSTUDIO_URL — LM Studio endpoint (default http://localhost:1234)

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
    """Sends chat completion requests to Ollama or LM Studio (OpenAI-compatible).

    All config values (provider, model, context_size, temperature) are read
    LIVE from settings on every call, so hot-patching via the Settings UI
    takes effect immediately — no restart needed.
    """

    @property
    def provider(self) -> str:
        return settings.LLM_PROVIDER

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

    @property
    def api_key(self) -> str:
        return settings.OPENAI_API_KEY

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

        if self.provider == "ollama":
            return await self._call_ollama(
                messages, response_format, max_tokens, effective_temp
            )
        else:
            # Both "lmstudio" and "openai" use the OpenAI-compatible API
            return await self._call_openai(
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

    async def _call_openai(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        temperature: float,
        *,
        _retries: int = 0,
    ) -> str:
        """Call an OpenAI-compatible /v1/chat/completions endpoint.

        Pre-validates prompt size against context_size (from Settings UI)
        and trims proactively. On 400 errors, retries up to 2 times.
        """
        # ── Pre-validate prompt against configured context_size ──
        # This makes the Settings UI context_size control effective
        # for ALL providers including LM Studio.
        total_chars = sum(len(m.get("content", "")) for m in messages)
        est_tokens = total_chars // 4  # ~4 chars per token
        ctx = self.context_size

        if est_tokens > ctx and _retries == 0:
            logger.warning(
                "⚠️  Prompt (~%d tokens) exceeds context_size (%d), "
                "pre-trimming before sending to %s",
                est_tokens,
                ctx,
                self.provider,
            )
            messages = self._trim_messages(messages)
            total_chars = sum(len(m.get("content", "")) for m in messages)
            est_tokens = total_chars // 4
        url = f"{self.base_url}/v1/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # LM Studio does NOT support response_format — omit it entirely.
        if response_format == "json" and self.provider != "lmstudio":
            payload["response_format"] = {"type": "json_object"}

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.info(
            "⏱️  OpenAI request START → %s model=%s provider=%s "
            "(~%d chars, ~%d est tokens, ctx=%d, retry=%d)",
            url,
            self.model,
            self.provider,
            total_chars,
            est_tokens,
            ctx,
            _retries,
        )
        t0 = time.perf_counter()

        # Derive a short context label from the system prompt
        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"

        try:
            client = await _get_shared_client()
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.ReadTimeout:
            elapsed = time.perf_counter() - t0
            logger.error(
                "⏱️  OpenAI request TIMEOUT after %.1fs", elapsed,
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

        # ── 400 Bad Request → retry with trimmed prompt ────────
        if resp.status_code == 400 and _retries < 2:
            body = resp.text
            logger.warning(
                "⚠️  400 from LLM (attempt %d), trimming prompt and retrying. Body: %s",
                _retries + 1,
                body[:300],
            )
            # Trim the longest message by ~40%
            trimmed = self._trim_messages(messages)
            return await self._call_openai(
                trimmed,
                response_format,
                max_tokens,
                temperature,
                _retries=_retries + 1,
            )

        # Log diagnostic info on errors before raising
        if resp.status_code >= 400:
            body = resp.text
            logger.error(
                "❌ OpenAI endpoint returned %d: %s",
                resp.status_code,
                body[:500],
            )
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        elapsed = time.perf_counter() - t0
        logger.info(
            "⏱️  OpenAI request DONE  → %.2fs, %d chars",
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
    async def fetch_models(provider: str, base_url: str) -> list[str]:
        """Probe a provider URL and return available model names.

        Works independently of the current config — used by the frontend
        to test arbitrary URLs before saving them.
        """
        base_url = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if provider == "ollama":
                    resp = await client.get(f"{base_url}/api/tags")
                    resp.raise_for_status()
                    return [m["name"] for m in resp.json().get("models", [])]
                else:
                    headers: dict[str, str] = {}
                    api_key = settings.OPENAI_API_KEY
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    resp = await client.get(f"{base_url}/v1/models", headers=headers)
                    resp.raise_for_status()
                    return [m.get("id", "") for m in resp.json().get("data", [])]
        except Exception:
            return []

    @staticmethod
    async def load_model_with_config(
        base_url: str,
        model: str,
        config: dict,
    ) -> dict:
        """Load a model via LM Studio v1 API with specific parameters.

        POST /api/v1/models/load with echo_load_config=true.
        Returns the actual load config applied by LM Studio.
        Warns loudly if the applied context_length differs from requested.
        """
        url = f"{base_url.rstrip('/')}/api/v1/models/load"
        payload: dict = {
            "model": model,
            "echo_load_config": True,
        }
        requested_ctx = 0
        # Only include optional params if set
        if config.get("context_length"):
            requested_ctx = int(config["context_length"])
            payload["context_length"] = requested_ctx
        if config.get("eval_batch_size"):
            payload["eval_batch_size"] = int(config["eval_batch_size"])
        if "flash_attention" in config:
            payload["flash_attention"] = bool(config["flash_attention"])
        if config.get("num_experts"):
            payload["num_experts"] = int(config["num_experts"])
        if "offload_kv_cache_to_gpu" in config:
            payload["offload_kv_cache_to_gpu"] = bool(config["offload_kv_cache_to_gpu"])

        logger.info("[LLM] Loading model %s with config: %s", model, payload)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = settings.OPENAI_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

            load_time = result.get("load_time_seconds", 0)
            load_cfg = result.get("load_config", {})
            actual_ctx = load_cfg.get("context_length", 0)

            logger.info(
                "[LLM] Model loaded: %s (%.1fs) — actual load_config: %s",
                model,
                load_time,
                load_cfg,
            )

            # ── Detect silent context cap ──
            if requested_ctx and actual_ctx and actual_ctx < requested_ctx:
                logger.warning(
                    "⚠️  CONTEXT MISMATCH: requested %d but LM Studio "
                    "applied %d for model %s — prompts may overflow!",
                    requested_ctx,
                    actual_ctx,
                    model,
                )

            return result

    @staticmethod
    async def get_loaded_model_info(base_url: str) -> list[dict]:
        """GET /v1/models — returns currently loaded model details."""
        url = f"{base_url.rstrip('/')}/v1/models"
        headers: dict[str, str] = {}
        api_key = settings.OPENAI_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json().get("data", [])
        except Exception:
            return []

    @staticmethod
    async def unload_all_lmstudio_models(base_url: str) -> int:
        """Unload ALL loaded model instances from LM Studio to free VRAM.

        GET /api/v1/models to list loaded instances, then
        POST /api/v1/models/unload for each instance.

        Returns the number of instances successfully unloaded.
        """
        base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = settings.OPENAI_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        unloaded = 0
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                list_resp = await client.get(
                    f"{base_url}/api/v1/models", headers=headers,
                )
                if list_resp.status_code != 200:
                    logger.warning(
                        "[LLM] Could not list LM Studio models for unload "
                        "(status %d)",
                        list_resp.status_code,
                    )
                    return 0

                all_models = list_resp.json().get("models", [])
                # Also handle the flat /v1/models format (list of {id: ...})
                if not all_models:
                    all_models = list_resp.json().get("data", [])

                for m in all_models:
                    # LM Studio v1 API returns loaded_instances per model
                    instances = m.get("loaded_instances", [])
                    if instances:
                        for inst in instances:
                            inst_id = inst.get("id", m.get("key"))
                            if not inst_id:
                                continue
                            try:
                                await client.post(
                                    f"{base_url}/api/v1/models/unload",
                                    json={"instance_id": inst_id},
                                    headers=headers,
                                )
                                unloaded += 1
                                logger.info(
                                    "[LLM] Unloaded LM Studio instance: %s",
                                    inst_id,
                                )
                            except Exception:
                                logger.warning(
                                    "[LLM] Failed to unload instance %s",
                                    inst_id,
                                )
                    else:
                        # Fallback: try unloading by model id/key directly
                        model_id = m.get("id") or m.get("key")
                        if model_id:
                            try:
                                await client.post(
                                    f"{base_url}/api/v1/models/unload",
                                    json={"model": model_id},
                                    headers=headers,
                                )
                                unloaded += 1
                                logger.info(
                                    "[LLM] Unloaded LM Studio model: %s",
                                    model_id,
                                )
                            except Exception:
                                logger.warning(
                                    "[LLM] Failed to unload model %s",
                                    model_id,
                                )
        except Exception as exc:
            logger.warning("[LLM] LM Studio unload sweep failed: %s", exc)

        logger.info("[LLM] LM Studio unload complete: %d instances freed", unloaded)
        return unloaded

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
    async def verify_and_warm_ollama_model(
        base_url: str,
        model: str,
        *,
        keep_alive: str = "10m",
    ) -> dict:
        """Verify an Ollama model exists, query VRAM, and pre-warm it.

        GET /api/tags to check availability, POST /api/show to get the
        model's architecture max context, GET /api/ps for VRAM usage,
        then POST /api/generate with keep_alive to load into VRAM.

        Returns a dict with model info AND recommended_ctx based on VRAM.
        """
        base_url = base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                # Step 1: Verify model exists (flexible name matching)
                tags_resp = await client.get(f"{base_url}/api/tags")
                tags_resp.raise_for_status()
                available = [m["name"] for m in tags_resp.json().get("models", [])]

                # Ollama tags always include :latest, so match flexibly
                # e.g. "glm-ocr" should match "glm-ocr:latest" and vice-versa
                model_found = model in available
                if not model_found:
                    # Try adding/removing :latest suffix
                    alt = f"{model}:latest" if ":" not in model else model.split(":")[0]
                    for avail in available:
                        if avail == alt or avail.split(":")[0] == model.split(":")[0]:
                            model_found = True
                            logger.info(
                                "[LLM] Fuzzy match: '%s' → '%s'", model, avail,
                            )
                            break

                model_max_ctx = 0
                vram_total_bytes = 0
                recommended_ctx = 8192  # safe fallback

                if model_found:
                    # Step 2: Query model architecture for max context
                    try:
                        show_resp = await client.post(
                            f"{base_url}/api/show",
                            json={"name": model},
                        )
                        show_resp.raise_for_status()
                        show_data = show_resp.json()

                        # model_info contains architecture details
                        model_info = show_data.get("model_info", {})
                        for key, val in model_info.items():
                            if "context_length" in key and isinstance(val, int):
                                model_max_ctx = val
                                break

                        logger.info(
                            "[LLM] Model %s architecture max context: %d",
                            model, model_max_ctx,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[LLM] Could not query model info: %s", exc,
                        )

                    # Step 3: Query VRAM usage from running models
                    try:
                        ps_resp = await client.get(f"{base_url}/api/ps")
                        ps_resp.raise_for_status()
                        ps_models = ps_resp.json().get("models", [])
                        for m in ps_models:
                            vram = m.get("size_vram", 0)
                            if vram > vram_total_bytes:
                                vram_total_bytes = vram
                    except Exception:
                        pass  # ps may be empty if nothing loaded yet

                    # Step 4+5: Probe-and-warm — find the maximum num_ctx
                    # that Ollama can actually allocate, starting from the
                    # user's desired context_size and halving on OOM.
                    from app.config import settings as _cfg

                    desired_ctx = _cfg.LLM_CONTEXT_SIZE
                    if model_max_ctx > 0:
                        desired_ctx = min(desired_ctx, model_max_ctx)
                    # Floor at 2048
                    desired_ctx = max(desired_ctx, 2048)

                    _MIN_CTX = 2048
                    attempt_ctx = desired_ctx
                    warmed = False

                    while attempt_ctx >= _MIN_CTX:
                        logger.info(
                            "[LLM] Warming %s with num_ctx=%d …",
                            model, attempt_ctx,
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
                                        "num_ctx": attempt_ctx,
                                    },
                                },
                            )
                            warm_resp.raise_for_status()
                            recommended_ctx = attempt_ctx
                            warmed = True
                            logger.info(
                                "[LLM] ✅ Model %s warmed at num_ctx=%d",
                                model, attempt_ctx,
                            )
                            break
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code == 500:
                                logger.warning(
                                    "[LLM] num_ctx=%d too large (OOM), "
                                    "halving → %d",
                                    attempt_ctx, attempt_ctx // 2,
                                )
                                attempt_ctx //= 2
                            else:
                                raise

                    if not warmed:
                        # Last resort: try minimum
                        recommended_ctx = _MIN_CTX
                        warm_resp = await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model,
                                "prompt": "",
                                "keep_alive": keep_alive,
                                "stream": False,
                                "options": {"num_ctx": _MIN_CTX},
                            },
                        )
                        warm_resp.raise_for_status()
                        logger.warning(
                            "[LLM] Fell back to minimum num_ctx=%d",
                            _MIN_CTX,
                        )

                return {
                    "status": (
                        "model_verified" if model_found
                        else "model_not_found"
                    ),
                    "model": model,
                    "available_models": available,
                    "model_found": model_found,
                    "pre_warmed": model_found,
                    "model_max_ctx": model_max_ctx,
                    "recommended_ctx": recommended_ctx,
                    "vram_bytes": vram_total_bytes,
                }
        except Exception as exc:
            logger.warning("[LLM] Ollama model verification failed: %s", exc)
            return {
                "status": "verification_failed",
                "error": str(exc),
            }

    async def health_check(self) -> dict:
        """Check connectivity to the LLM backend."""
        try:
            if self.provider == "ollama":
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
                        "lmstudio_url": settings.LMSTUDIO_URL,
                        "models": models,
                        "configured_model": self.model,
                        "model_available": self.model in models,
                    }
            else:
                # LM Studio and OpenAI both use /v1/models
                url = f"{self.base_url}/v1/models"
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    models = [m.get("id", "") for m in data.get("data", [])]
                    return {
                        "status": "ok",
                        "provider": self.provider,
                        "active_url": self.base_url,
                        "ollama_url": settings.OLLAMA_URL,
                        "lmstudio_url": settings.LMSTUDIO_URL,
                        "models": models,
                        "configured_model": self.model,
                    }
        except Exception as e:
            return {
                "status": "error",
                "provider": self.provider,
                "active_url": self.base_url,
                "ollama_url": settings.OLLAMA_URL,
                "lmstudio_url": settings.LMSTUDIO_URL,
                "error": str(e),
            }
