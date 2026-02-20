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
                connect=10.0,   # Fail fast if server is unreachable
                read=300.0,     # LLM inference can be slow
                write=30.0,     # Sending large prompts
                pool=30.0,      # Waiting for a connection slot
            ),
            limits=httpx.Limits(
                max_connections=20,  # Up to 20 parallel TCP connections
                max_keepalive_connections=10,
            ),
        )
    return _shared_client


class LLMService:
    """Sends chat completion requests to Ollama or LM Studio (OpenAI-compatible)."""

    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER
        self.base_url = settings.LLM_BASE_URL  # Computed property, already stripped
        self.model = settings.LLM_MODEL
        self.temperature = settings.LLM_TEMPERATURE
        self.context_size = settings.LLM_CONTEXT_SIZE
        self.api_key = settings.OPENAI_API_KEY

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
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Args:
            system: The system prompt.
            user: The user message (data context).
            response_format: "json" to hint at JSON output, "text" for free-form.
            max_tokens: Optional max token limit for the response.

        Returns:
            The raw string response from the LLM.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        if self.provider == "ollama":
            return await self._call_ollama(messages, response_format, max_tokens)
        else:
            # Both "lmstudio" and "openai" use the OpenAI-compatible API
            return await self._call_openai(messages, response_format, max_tokens)

    async def _call_ollama(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
    ) -> str:
        """Call the Ollama /api/chat endpoint using shared connection pool."""
        url = f"{self.base_url}/api/chat"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_size,
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

        client = await _get_shared_client()
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")

        elapsed = time.perf_counter() - t0
        logger.info(
            "⏱️  Ollama request DONE  → %.2fs, %d chars",
            elapsed,
            len(content),
        )
        return content

    async def _call_openai(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        *,
        _retries: int = 0,
    ) -> str:
        """Call an OpenAI-compatible /v1/chat/completions endpoint.

        On 400 errors (often prompt overflow), trims the longest message
        and retries up to 2 times.
        """
        url = f"{self.base_url}/v1/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # LM Studio does NOT support response_format — omit it entirely.
        if response_format == "json" and self.provider != "lmstudio":
            payload["response_format"] = {"type": "json_object"}

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        total_chars = sum(len(m.get("content", "")) for m in messages)
        est_tokens = self.estimate_tokens(str(total_chars))
        logger.info(
            "⏱️  OpenAI request START → %s model=%s provider=%s "
            "(~%d chars, ~%d est tokens, retry=%d)",
            url, self.model, self.provider,
            total_chars, est_tokens, _retries,
        )
        t0 = time.perf_counter()

        client = await _get_shared_client()
        resp = await client.post(url, json=payload, headers=headers)

        # ── 400 Bad Request → retry with trimmed prompt ────────
        if resp.status_code == 400 and _retries < 2:
            body = resp.text
            logger.warning(
                "⚠️  400 from LLM (attempt %d), trimming prompt and retrying. "
                "Body: %s",
                _retries + 1, body[:300],
            )
            # Trim the longest message by ~40%
            trimmed = self._trim_messages(messages)
            return await self._call_openai(
                trimmed, response_format, max_tokens,
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

        elapsed = time.perf_counter() - t0
        logger.info(
            "⏱️  OpenAI request DONE  → %.2fs, %d chars",
            elapsed,
            len(content),
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
                    i, len(content), len(trimmed_content),
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
