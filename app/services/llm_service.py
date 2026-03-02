"""Ollama LLM service ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â sends chat requests to Ollama.

The Ollama URL is centralized in app.config.settings:
    OLLAMA_URL ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â Ollama endpoint (default http://localhost:11434)

Uses a module-level shared httpx.AsyncClient for connection pooling.
This is critical for parallel LLM calls ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â when OLLAMA_NUM_PARALLEL > 1,
multiple agents can share the same TCP connection pool instead of each
creating and destroying their own connection.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from app.config import settings
from app.services.pipeline_health import log_llm_call
from app.utils.logger import logger

# Shared async HTTP client ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â reused across all LLM calls for connection pooling.
# Created lazily on first use; lives for the entire app lifecycle.
_shared_client: httpx.AsyncClient | None = None


async def _get_shared_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient."""
    global _shared_client  # noqa: PLW0603
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,  # Fail fast if server is unreachable
                read=600.0,  # 10 min ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â thinking models can be very slow
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
    takes effect immediately ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â no restart needed.
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
        """Call the Ollama /api/chat endpoint using shared connection pool.

        Implements a dual-mode retry: if ``format=json`` returns an empty
        response (some models can't handle GBNF grammar constraints), the
        call is retried with ``format=text`` and an explicit JSON
        instruction appended to the system prompt.
        """
        content = await self._send_ollama_request(
            messages,
            response_format,
            max_tokens,
            temperature,
        )

        # ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Dual-mode retry for empty JSON responses ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬
        # Some models (e.g. GLM-4.7-flash) return 0 chars when
        # format=json is used because they can't handle the GBNF
        # grammar constraint.  Retry with format=text instead.
        if not content.strip() and response_format == "json":
            logger.warning(
                "[LLM] Empty response with format=json ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â retrying "
                "with format=text + JSON instructions",
            )
            # Append explicit JSON instruction to system prompt
            retry_msgs = list(messages)  # shallow copy
            if retry_msgs and retry_msgs[0].get("role") == "system":
                retry_msgs[0] = {
                    **retry_msgs[0],
                    "content": (
                        retry_msgs[0]["content"]
                        + "\n\nIMPORTANT: You MUST respond with "
                        "valid JSON only. No markdown, no "
                        "explanations ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â pure JSON."
                    ),
                }
            content = await self._send_ollama_request(
                retry_msgs,
                "text",
                max_tokens,
                temperature,
            )
            if content.strip():
                logger.info(
                    "[LLM] Text-mode retry succeeded (%d chars)",
                    len(content),
                )
            else:
                logger.error(
                    "[LLM] Text-mode retry also returned empty ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â "
                    "model may be unresponsive",
                )

        return content

    async def _send_ollama_request(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        temperature: float,
    ) -> str:
        """Send a single request to the Ollama /api/chat endpoint."""
        url = f"{self.base_url}/api/chat"

        # Context size: Use the PROVEN loaded context for this model
        # from vram_measurements. The config context_size is a desired
        # maximum, but the actual ctx must match what the model was
        # loaded with ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â sending a larger num_ctx causes 500 errors.
        desired_ctx = self.context_size if self.context_size > 0 else 8192
        measurement = settings.LLM_VRAM_MEASUREMENTS.get(self.model, {})
        proven_ctx = measurement.get("ctx", 0)
        if proven_ctx > 0:
            effective_ctx = min(desired_ctx, proven_ctx)
        else:
            # No measurement yet ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â use a safe default
            effective_ctx = min(desired_ctx, 8192)

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
            "Ollama request START -> %s model=%s format=%s ctx=%d",
            url,
            self.model,
            response_format,
            effective_ctx,
        )
        t0 = time.perf_counter()

        # Derive a short context label from the system prompt
        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"

        # Hard timeout ceiling (configurable, default 180s)
        _timeout = settings.LLM_CALL_TIMEOUT_SECONDS

        try:
            client = await _get_shared_client()
            resp = await asyncio.wait_for(
                client.post(url, json=payload),
                timeout=_timeout,
            )
            resp.raise_for_status()
        except (httpx.ReadTimeout, asyncio.TimeoutError):
            elapsed = time.perf_counter() - t0
            logger.error(
                "Ollama request TIMEOUT after %.1fs (limit=%ds)",
                elapsed,
                _timeout,
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
            logger.warning(
                "Ollama request FAILED -> %.1fs: %s",
                elapsed,
                str(exc)[:120],
            )
            log_llm_call(
                context=_ctx,
                model=self.model,
                duration_seconds=elapsed,
                error=str(exc)[:120],
            )
            # Return empty string for 500 errors so the retry can kick in
            # instead of crashing the whole pipeline
            if "500" in str(exc):
                return ""
            raise

        data = resp.json()
        msg = data.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        tokens = data.get("eval_count", 0)

        # ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Thinking-model fallback ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬
        # Some thinking models (e.g. olmo-3:32b, qwen3) put all
        # their reasoning in `thinking` and leave `content` empty.
        # Try to extract JSON from the thinking text.
        if not content.strip() and thinking.strip():
            # Try to find a JSON object or array in the thinking
            import json as _json

            # Look for the last JSON block in thinking text
            # (the answer usually comes after the reasoning)
            json_match = re.search(
                r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})",
                thinking,
                re.DOTALL,
            )
            if json_match:
                candidate = json_match.group(1)
                try:
                    _json.loads(candidate)  # Validate it's real JSON
                    content = candidate
                    logger.info(
                        "[LLM] Extracted JSON from thinking field (%d chars)",
                        len(content),
                    )
                except _json.JSONDecodeError:
                    pass  # Not valid JSON, keep content empty

            # If still empty, return the raw thinking text
            # so the caller can try to parse it
            if not content.strip() and thinking.strip():
                content = thinking
                logger.warning(
                    "[LLM] Using raw thinking text as response "
                    "(%d chars) ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â no JSON found",
                    len(content),
                )

        elapsed = time.perf_counter() - t0

        # Log thinking model output separately
        if thinking:
            think_tokens = data.get("thinking_eval_count", 0)
            logger.info(
                "Ollama request DONE  -> %.2fs, %d chars "
                "(thinking: %d chars, %d tokens)",
                elapsed,
                len(content),
                len(thinking),
                think_tokens,
            )
        else:
            logger.info(
                "Ollama request DONE  -> %.2fs, %d chars",
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
                    "ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â  Trimmed message[%d] from %d ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ %d chars",
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

        # Incomplete object (truncated by max_tokens) ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â return what we have
        return cleaned[start:]

    @staticmethod
    async def fetch_models(base_url: str) -> list[str]:
        """Probe an Ollama URL and return available model names.

        Works independently of the current config ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â used by the frontend
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
                    "[LLM] Ollama model %s unloaded (keep_alive=0)",
                    model,
                )
                return True
        except Exception as exc:
            logger.warning(
                "[LLM] Failed to unload Ollama model %s: %s",
                model,
                exc,
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
                            "[LLM] Unloaded Ollama model: %s",
                            model_name,
                        )
                    except Exception:
                        logger.warning(
                            "[LLM] Failed to unload Ollama model %s",
                            model_name,
                        )
        except Exception as exc:
            logger.warning("[LLM] Ollama unload sweep failed: %s", exc)

        logger.info(
            "[LLM] Ollama unload complete: %d models freed",
            unloaded,
        )
        return unloaded

    @staticmethod
    def get_total_vram_bytes() -> int:
        """Get total VRAM/unified-memory for the Ollama server.

        Uses SYSTEM_TOTAL_VRAM_GB from config (set via Settings UI
        or llm_config.json).  For Jetson Orin AGX 64GB, set to 64.
        Falls back to /proc/meminfo (Jetson unified memory) then
        nvidia-smi (discrete GPUs).
        """
        from app.config import settings as _cfg

        if _cfg.SYSTEM_TOTAL_VRAM_GB > 0:
            return int(_cfg.SYSTEM_TOTAL_VRAM_GB * (1024**3))

        # JETSON FIX: Read true unified memory total from Linux OS.
        # nvidia-smi does NOT work on Jetson (unified memory).
        import os

        try:
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return kb * 1024
        except Exception as exc:
            logger.debug("[LLM] /proc/meminfo failed: %s", exc)

        # Fallback: nvidia-smi for discrete GPUs (not Jetson)
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
                total_mib = int(
                    result.stdout.strip().split("\n")[0].strip(),
                )
                return total_mib * 1024 * 1024
        except Exception as exc:
            logger.debug("[LLM] nvidia-smi failed: %s", exc)
        return 0

    @staticmethod
    def get_safe_ceiling_bytes() -> int:
        """Get the safe VRAM ceiling (total - 5 GiB for OS overhead).

        On Jetson unified memory, the OS and background processes
        need ~4-6 GiB.  We reserve 5 GiB as a safety buffer.
        """
        total = LLMService.get_total_vram_bytes()
        _OS_RESERVE = 5 * (1024**3)  # 5 GiB
        return max(total - _OS_RESERVE, 0) if total else 0

    @staticmethod
    def estimate_model_vram(
        model_info: dict,
        model_file_size: int,
        num_ctx: int,
    ) -> dict:
        """Estimate total VRAM for a model at a given context length.

        Uses the model architecture from /api/show's model_info to
        calculate KV cache size.  Model weight VRAM ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â°Ãƒâ€¹Ã¢â‚¬Â  GGUF file size.

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
            # KV cache = 2 (K+V) ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â layers ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â kv_heads ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â head_dim ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â 2 (FP16)
            kv_bytes_per_token = 2 * block_count * head_count_kv * head_dim * 2
            kv_bytes = kv_bytes_per_token * num_ctx

        # Graph overhead: ~500 MiB for compute graph buffers.
        # Research shows 0.5-1 GiB is typical; 1.5 GiB was overly
        # conservative and wasted usable context on Jetson.
        _GRAPH_OVERHEAD = int(0.5 * (1024**3))

        return {
            "total_bytes": model_file_size + kv_bytes + _GRAPH_OVERHEAD,
            "weights_bytes": model_file_size,
            "kv_bytes": kv_bytes,
            "graph_overhead": _GRAPH_OVERHEAD,
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
          1. GET /api/tags  ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ verify model exists, get file size
          2. POST /api/show ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ get architecture (layers, kv_heads, head_dim)
          3. nvidia-smi      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ get free GPU memory
          4. MATH            ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ estimate total VRAM needed
          5. If estimated > free ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ return oom_error + suggested_ctx (NO load)
          6. If estimated ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â°Ãƒâ€šÃ‚Â¤ free ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ load the model (single attempt)

        Returns dict with model info.  On predicted/actual OOM, returns
        status="oom_error" with suggested_ctx.
        """
        base_url = base_url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                # â€”â€” Step 1: Verify model exists â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
                tags_resp = await client.get(f"{base_url}/api/tags")
                tags_resp.raise_for_status()
                tags_data = tags_resp.json().get("models", [])
                available = [m["name"] for m in tags_data]

                model_found = model in available
                model_file_size = 0

                if not model_found:
                    alt = f"{model}:latest" if ":" not in model else model.split(":")[0]
                    for avail_model in tags_data:
                        if (
                            avail_model["name"] == alt
                            or avail_model["name"].split(":")[0] == model.split(":")[0]
                        ):
                            model = avail_model["name"]
                            model_found = True
                            break

                if not model_found:
                    return {
                        "status": "model_not_found",
                        "model": model,
                        "available_models": available,
                        "model_found": False,
                    }

                # Get file size
                for m in tags_data:
                    if m["name"] == model:
                        model_file_size = m.get("size", 0)
                        break

                model_max_ctx = 0
                model_info: dict = {}

                # â€”â€” Step 2: Query architecture â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
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
                            val,
                            int,
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
                        "[LLM] Could not query model info: %s",
                        exc,
                    )

                # â€”â€” Step 3: Determine desired context â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
                from app.config import settings as _cfg

                desired_ctx = _cfg.LLM_CONTEXT_SIZE
                if model_max_ctx > 0:
                    desired_ctx = min(desired_ctx, model_max_ctx)
                desired_ctx = max(desired_ctx, 2048)

                # â€”â€” Step 4: Estimate VRAM (for frontend display) â€”
                estimate = LLMService.estimate_model_vram(
                    model_info,
                    model_file_size,
                    desired_ctx,
                )
                total_gpu = LLMService.get_total_vram_bytes()
                safe_ceiling = LLMService.get_safe_ceiling_bytes()
                kv_per_tok = estimate["kv_bytes_per_token"]

                est_gb = estimate["total_bytes"] / (1024**3)
                total_gb = total_gpu / (1024**3) if total_gpu else 0
                safe_gb = safe_ceiling / (1024**3) if safe_ceiling else 0

                logger.info(
                    "[LLM] VRAM estimate for %s @ ctx=%d: "
                    "%.1f GiB needed (weights=%.1f + KV=%.1f + "
                    "graph=0.5), total=%.1f GiB, safe=%.1f GiB",
                    model,
                    desired_ctx,
                    est_gb,
                    estimate["weights_bytes"] / (1024**3),
                    estimate["kv_bytes"] / (1024**3),
                    total_gb,
                    safe_gb,
                )

                # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
                # EMPIRICAL MEMORY AUDIT SYSTEM
                #
                # On Jetson unified memory, there is a ~10 GB
                # invisible overhead (OS page cache, CUDA context,
                # fragmentation) that no formula can predict.
                # Instead of math, we test the real hardware.
                #
                # Two paths:
                #   FAST PATH:  proven_max_ctx in cache â†’ instant
                #   AUDIT PATH: step through ctx sizes â†’ find limit
                # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
                import asyncio

                cached = _cfg.LLM_VRAM_MEASUREMENTS.get(model, {})
                proven_max_ctx = cached.get("proven_max_ctx", 0)

                if proven_max_ctx > 0:
                    # â•â•â• FAST PATH: Audited model â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
                    load_ctx = min(desired_ctx, proven_max_ctx)
                    load_ctx = max(load_ctx, 2048)
                    clamped = load_ctx < desired_ctx

                    logger.info(
                        "[LLM] âš¡ FAST PATH: %s audited limit=%d. "
                        "Loading at ctx=%d (desired=%d)",
                        model,
                        proven_max_ctx,
                        load_ctx,
                        desired_ctx,
                    )

                    # Flush other models first
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
                    except Exception:
                        pass

                    # Single, confident load
                    try:
                        warm_resp = await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model,
                                "prompt": "",
                                "keep_alive": keep_alive,
                                "stream": False,
                                "options": {
                                    "num_ctx": load_ctx,
                                    "num_gpu": 999,
                                },
                            },
                        )
                        warm_resp.raise_for_status()
                        logger.info(
                            "[LLM] âœ… Model %s loaded at ctx=%d",
                            model,
                            load_ctx,
                        )
                    except httpx.HTTPStatusError:
                        # Cached limit failed â€” invalidate and
                        # fall through to re-audit next time.
                        logger.warning(
                            "[LLM] âš ï¸ Cached limit %d failed for "
                            "%s! Clearing cache for re-audit.",
                            proven_max_ctx,
                            model,
                        )
                        _cfg.LLM_VRAM_MEASUREMENTS.pop(model, None)
                        try:
                            _cfg.update_llm_config(
                                {"vram_measurements": _cfg.LLM_VRAM_MEASUREMENTS},
                            )
                        except Exception:
                            pass
                        return {
                            "status": "model_verified",
                            "model": model,
                            "available_models": [m["name"] for m in tags_data],
                            "model_found": True,
                            "pre_warmed": False,
                            "audit_performed": False,
                            "recommended_ctx": 2048,
                            "message": (
                                f"Cached limit {proven_max_ctx:,} failed. "
                                "Re-audit needed on next load."
                            ),
                        }

                    result: dict = {
                        "status": "model_verified",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": True,
                        "pre_warmed": True,
                        "model_max_ctx": model_max_ctx,
                        "recommended_ctx": load_ctx,
                        "proven_max_ctx": proven_max_ctx,
                        "audit_performed": False,
                        "kv_rate_bytes_per_token": kv_per_tok,
                    }
                    if clamped:
                        result["clamped_from"] = desired_ctx
                        result["message"] = (
                            f"Loaded at {load_ctx:,} tokens "
                            f"(hardware limit: {proven_max_ctx:,})."
                        )
                    return result

                else:
                    # â•â•â• AUDIT PATH: First-time model test â•â•â•â•â•â•
                    logger.info(
                        "[LLM] ðŸ” MEMORY AUDIT: First time loading "
                        "%s. Testing hardware limits...",
                        model,
                    )

                    # Define stepped context sizes to test
                    audit_steps = [
                        2048,
                        4096,
                        8192,
                        16384,
                        24576,
                        32768,
                        49152,
                        65536,
                        98304,
                        131072,
                    ]
                    # Only test up to what the user wants
                    audit_steps = [s for s in audit_steps if s <= desired_ctx]
                    if desired_ctx not in audit_steps:
                        audit_steps.append(desired_ctx)

                    last_successful_ctx = 0

                    for ctx_test in audit_steps:
                        logger.info(
                            "[LLM] ðŸ” Audit step: testing ctx=%d...",
                            ctx_test,
                        )

                        # 1. Unload completely before every step
                        await LLMService.unload_ollama_model(
                            base_url,
                            model,
                        )
                        await asyncio.sleep(2)

                        # 2. Attempt load
                        try:
                            warm_resp = await client.post(
                                f"{base_url}/api/generate",
                                json={
                                    "model": model,
                                    "prompt": "",
                                    "keep_alive": keep_alive,
                                    "stream": False,
                                    "options": {
                                        "num_ctx": ctx_test,
                                        "num_gpu": 999,
                                    },
                                },
                            )
                            warm_resp.raise_for_status()
                            last_successful_ctx = ctx_test
                            logger.info(
                                "[LLM] âœ… Audit step ctx=%d SUCCESS",
                                ctx_test,
                            )
                        except httpx.HTTPStatusError:
                            logger.warning(
                                "[LLM] ðŸ›‘ Audit step ctx=%d FAILED. "
                                "Hardware limit found.",
                                ctx_test,
                            )
                            break

                    # Handle total failure (even 2048 failed)
                    if last_successful_ctx == 0:
                        logger.error(
                            "[LLM] âŒ Model %s cannot load at any "
                            "context size. Model weights exceed "
                            "available memory.",
                            model,
                        )
                        return {
                            "status": "oom_error",
                            "model": model,
                            "available_models": [m["name"] for m in tags_data],
                            "model_found": True,
                            "pre_warmed": False,
                            "message": (
                                f"Model {model} weights exceed available "
                                "memory. Try a smaller model."
                            ),
                        }

                    proven_max_ctx = last_successful_ctx
                    logger.info(
                        "[LLM] ðŸ AUDIT COMPLETE! %s proven limit: ctx=%d",
                        model,
                        proven_max_ctx,
                    )

                    # Save to persistent cache
                    _cfg.LLM_VRAM_MEASUREMENTS[model] = {
                        "proven_max_ctx": proven_max_ctx,
                    }
                    try:
                        _cfg.update_llm_config(
                            {"vram_measurements": _cfg.LLM_VRAM_MEASUREMENTS},
                        )
                        logger.info(
                            "[LLM] Audit results saved to disk.",
                        )
                    except Exception as save_exc:
                        logger.warning(
                            "[LLM] Could not persist audit cache: %s",
                            save_exc,
                        )

                    # Reload at proven limit (audit may have ended
                    # on a failure, leaving model unloaded)
                    load_ctx = min(desired_ctx, proven_max_ctx)
                    load_ctx = max(load_ctx, 2048)

                    await LLMService.unload_ollama_model(
                        base_url,
                        model,
                    )
                    await asyncio.sleep(2)
                    try:
                        warm_resp = await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model,
                                "prompt": "",
                                "keep_alive": keep_alive,
                                "stream": False,
                                "options": {
                                    "num_ctx": load_ctx,
                                    "num_gpu": 999,
                                },
                            },
                        )
                        warm_resp.raise_for_status()
                        logger.info(
                            "[LLM] âœ… Final load at ctx=%d after audit",
                            load_ctx,
                        )
                    except httpx.HTTPStatusError:
                        logger.warning(
                            "[LLM] Final reload at ctx=%d failed "
                            "after audit. Model may be unloaded.",
                            load_ctx,
                        )

                    clamped = load_ctx < desired_ctx
                    result: dict = {
                        "status": "model_verified",
                        "model": model,
                        "available_models": [m["name"] for m in tags_data],
                        "model_found": True,
                        "pre_warmed": True,
                        "model_max_ctx": model_max_ctx,
                        "recommended_ctx": load_ctx,
                        "proven_max_ctx": proven_max_ctx,
                        "audit_performed": True,
                        "kv_rate_bytes_per_token": kv_per_tok,
                        "message": (
                            f"Memory audit complete. Hardware limit: "
                            f"{proven_max_ctx:,} tokens. Loaded at "
                            f"{load_ctx:,}."
                        ),
                    }
                    if clamped:
                        result["clamped_from"] = desired_ctx
                    return result
        except Exception as exc:
            logger.warning(
                "[LLM] Ollama model verification failed: %s",
                exc,
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
