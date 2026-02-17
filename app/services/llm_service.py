"""Provider-agnostic LLM service — supports Ollama and LM Studio.

Provider URLs are centralized in app.config.settings:
    OLLAMA_URL   — Ollama endpoint (default http://localhost:11434)
    LMSTUDIO_URL — LM Studio endpoint (default http://localhost:1234)
"""

from __future__ import annotations

import re

import httpx

from app.config import settings
from app.utils.logger import logger


class LLMService:
    """Sends chat completion requests to Ollama or LM Studio (OpenAI-compatible)."""

    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER
        self.base_url = settings.LLM_BASE_URL  # Computed property, already stripped
        self.model = settings.LLM_MODEL
        self.temperature = settings.LLM_TEMPERATURE
        self.context_size = settings.LLM_CONTEXT_SIZE
        self.api_key = settings.OPENAI_API_KEY

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
        """Call the Ollama /api/chat endpoint."""
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

        logger.debug("Ollama request -> %s model=%s format=%s", url, self.model, response_format)

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            logger.debug("Ollama response length: %d chars", len(content))
            return content

    async def _call_openai(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
    ) -> str:
        """Call an OpenAI-compatible /v1/chat/completions endpoint."""
        url = f"{self.base_url}/v1/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("OpenAI request -> %s model=%s", url, self.model)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.debug("OpenAI response length: %d chars", len(content))
            return content

    @staticmethod
    def clean_json_response(raw: str) -> str:
        """Strip markdown code fences and extract the JSON object.

        LLMs often wrap their JSON in ```json ... ``` markers.
        """
        # Strip markdown code blocks
        cleaned = re.sub(r"```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # Try to find JSON object boundaries
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
        elif start != -1:
            # Found start but no end - try to salvage what we can or just return as is
            # expecting parser to fail later if it's incomplete
            cleaned = cleaned[start:]
        
        return cleaned

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
                    resp = await client.get(
                        f"{base_url}/v1/models", headers=headers
                    )
                    resp.raise_for_status()
                    return [
                        m.get("id", "")
                        for m in resp.json().get("data", [])
                    ]
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
