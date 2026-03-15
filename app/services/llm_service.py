"""LLM service — sends chat requests via Prism AI Gateway.

All LLM calls are routed through Prism (POST /chat?stream=false) which
proxies to the configured Ollama backend.  Prism logs all requests,
tracks usage/cost, and provides centralized data collection.

Conversation tracking uses `conversationMeta` in the chat payload —
Prism auto-creates conversations when a conversationId is provided.

Direct Ollama access is only used for model warm-up and VRAM estimation.

Uses a module-level shared httpx.AsyncClient for connection pooling.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time

import httpx

from app.config import settings
from app.services.pipeline_health import log_llm_call
from app.utils.logger import logger

# Shared async HTTP client — reused across all LLM calls for connection pooling.
# Created lazily on first use; lives for the entire app lifecycle.
_shared_client: httpx.AsyncClient | None = None

# ── LLM Request Queue ────────────────────────────────────────────────
# Global semaphore that ensures only ONE LLM request hits Ollama at a
# time.  Ollama on a single GPU processes requests sequentially — firing
# multiple in parallel just causes GPU context-switching overhead which
# tanks throughput.  All callers across the app (discovery, analysis,
# trading, peer fetching) automatically queue through this.
_llm_queue: asyncio.Semaphore | None = None
_llm_queue_waiters: int = 0  # Track how many requests are waiting

# ── Graceful Shutdown Flag ────────────────────────────────────────────
# When True, all new LLM requests are skipped and in-flight requests
# are cancelled as soon as possible.
_shutdown_requested: bool = False


def _get_llm_queue() -> asyncio.Semaphore:
    """Lazy-init the LLM request queue (must be called inside an event loop)."""
    global _llm_queue
    if _llm_queue is None:
        _llm_queue = asyncio.Semaphore(1)
    return _llm_queue


def request_shutdown() -> None:
    """Signal all LLM operations to stop. Closes the shared HTTP client
    to abort any in-flight request to Ollama/Prism."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("[LLM] Shutdown requested — cancelling all LLM operations")


def cancel_shutdown() -> None:
    """Reset the shutdown flag so LLM operations can resume."""
    global _shutdown_requested
    _shutdown_requested = False
    logger.info("[LLM] Shutdown cancelled — LLM operations resumed")


async def close_shared_client() -> None:
    """Close the shared httpx client, aborting any in-flight HTTP request."""
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
        logger.info("[LLM] Shared HTTP client closed")


async def _get_shared_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,  # Fail fast if server is unreachable
                read=600.0,  # 10 min — thinking models can be very slow
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

    def __init__(self, *, model_override: str = "") -> None:
        self._model_override = model_override

    @property
    def base_url(self) -> str:
        return settings.LLM_BASE_URL

    @property
    def model(self) -> str:
        return self._model_override or settings.LLM_MODEL

    @model.setter
    def model(self, value: str) -> None:
        self._model_override = value

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
        system: str = "",
        user: str = "",
        *,
        messages: list[dict] | None = None,
        response_format: str = "json",
        schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        # ── Audit metadata (optional, passed by callers for traceability) ──
        audit_ticker: str = "",
        audit_step: str = "",
        audit_cycle_id: str = "",
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Args:
            system: The system prompt (legacy mode).
            user: The user message (legacy mode).
            messages: Native messages array (multi-turn). When provided,
                      system/user params are ignored and messages is passed
                      directly to Ollama. This preserves tool-call context.
            response_format: "json" to hint at JSON output, "text" for free-form.
            schema: Optional JSON Schema dict. When provided, passed as the
                    Ollama `format` field to enforce structured output.
            max_tokens: Optional max token limit for the response.
            temperature: Optional per-request temperature override.
                         If None, uses the global setting from config.
            audit_ticker: Ticker being analyzed (for audit trail).
            audit_step: Pipeline step name (for audit trail).
            audit_cycle_id: Trading cycle ID (for audit trail).

        Returns:
            The raw string response from the LLM.
        """
        if messages is not None:
            # Native multi-turn mode — pass messages directly
            effective_msgs = messages
        else:
            # Legacy mode — build messages from system + user
            effective_msgs = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]

        # Resolve effective temperature (per-request override > global config)
        effective_temp = temperature if temperature is not None else self.temperature

        # ── Timed call with audit logging ─────────────────────────
        import time as _time

        t0 = _time.monotonic()
        try:
            content = await self._call_prism(
                effective_msgs,
                response_format,
                max_tokens,
                effective_temp,
                schema=schema,
                audit_ticker=audit_ticker,
                audit_step=audit_step,
                audit_cycle_id=audit_cycle_id,
            )
        except (TimeoutError, Exception) as exc:
            content = ""
            logger.warning(
                "[LLM] Primary model %s failed: %s — trying fallback",
                self.model, str(exc)[:100],
            )

        elapsed_ms = int((_time.monotonic() - t0) * 1000)

        # ── Log empty responses but do NOT fall back to a different model ──
        # Each bot must use only its assigned model. If the model returns
        # empty, the caller (TradingAgent, TradeActionParser) has its own
        # retry/skip logic. Silently swapping to a different model produces
        # mixed, unreliable results.
        if not content.strip():
            logger.warning(
                "[LLM] ⚠️ Model %s returned empty after %.1fs — "
                "NOT falling back to another model. "
                "The caller will handle this via retry/skip.",
                self.model, elapsed_ms / 1000,
            )

        # Non-blocking audit log (never crashes the pipeline)
        try:
            from app.services.llm_audit_logger import LLMAuditLogger

            # Extract system/user from effective messages
            sys_text = ""
            usr_text = ""
            for m in effective_msgs:
                if m.get("role") == "system":
                    sys_text = m.get("content", "")
                elif m.get("role") == "user":
                    usr_text = m.get("content", "")

            # Try to parse response as JSON for the parsed_json column
            parsed = None
            try:
                import json as _json

                cleaned = self.clean_json_response(content)
                parsed = _json.loads(cleaned)
            except Exception:
                pass

            LLMAuditLogger.log(
                cycle_id=audit_cycle_id,
                ticker=audit_ticker,
                agent_step=audit_step,
                system_prompt=sys_text,
                user_context=usr_text,
                raw_response=content,
                parsed_json=parsed,
                tokens_used=self.estimate_tokens(content),
                execution_time_ms=elapsed_ms,
                model=self.model,
            )
        except Exception:
            pass  # Audit logging must never block trading

        return content

    async def _call_prism(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        temperature: float,
        *,
        schema: dict | None = None,
        audit_ticker: str = "",
        audit_step: str = "",
        audit_cycle_id: str = "",
    ) -> str:
        """Call the Prism AI Gateway for text generation.

        Implements a dual-mode retry: if JSON-format response returns empty
        (some models can't handle grammar constraints), the call is retried
        with text format and explicit JSON instructions.
        """
        # ── Shutdown gate: skip immediately if shutdown requested ──
        if _shutdown_requested:
            logger.info(
                "[LLM] Shutdown in progress — skipping request (%s %s)",
                audit_ticker or self.model, audit_step or "",
            )
            return ""

        # ── Queue: serialize all LLM requests ──────────────────
        # Only one request hits Ollama at a time; others wait in
        # a FIFO queue to maximize single-GPU throughput.
        global _llm_queue_waiters
        queue = _get_llm_queue()

        if queue.locked():
            _llm_queue_waiters += 1
            logger.info(
                "[LLM Queue] Request queued (position #%d) — %s %s",
                _llm_queue_waiters,
                audit_ticker or self.model,
                audit_step or "",
            )

        async with queue:
            _llm_queue_waiters = max(0, _llm_queue_waiters - 1)

            content = await self._send_prism_request(
                messages,
                response_format,
                max_tokens,
                temperature,
                schema=schema,
                audit_ticker=audit_ticker,
                audit_step=audit_step,
                audit_cycle_id=audit_cycle_id,
            )

            # ── Dual-mode retry for empty JSON responses ──
            # Some models (e.g. GLM-4.7-flash) return 0 chars when
            # format=json is used because they can't handle the GBNF
            # grammar constraint.  Retry with format=text instead.
            if not content.strip() and response_format == "json":
                logger.warning(
                    "[LLM] Empty response with format=json — retrying "
                    "with format=text + JSON instructions",
                )
                # Append explicit JSON instruction to system prompt
                retry_msgs = list(messages)  # shallow copy
                if retry_msgs and retry_msgs[0].get("role") == "system":
                    retry_msgs[0] = {
                        **retry_msgs[0],
                        "content": (
                            retry_msgs[0]["content"] + "\n\nIMPORTANT: You MUST respond with "
                            "valid JSON only. No markdown, no "
                            "explanations — pure JSON."
                        ),
                    }
                content = await self._send_prism_request(
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
                        "[LLM] Text-mode retry also returned empty — model may be unresponsive",
                    )

            return content

    async def _send_prism_request(
        self,
        messages: list[dict],
        response_format: str,
        max_tokens: int | None,
        temperature: float,
        *,
        schema: dict | None = None,
        audit_ticker: str = "",
        audit_step: str = "",
        audit_cycle_id: str = "",
    ) -> str:
        """Send a single request to the Prism AI Gateway.

        Routes through Prism's POST /chat?stream=false endpoint which
        forwards to the configured Ollama backend.  Prism logs all calls
        centrally.  When audit metadata is provided, a conversationMeta
        object is included so the call auto-creates a conversation in
        Prism's live activity dashboard.
        """
        url = f"{self.base_url}/chat?stream=false"

        # Build Prism options
        options: dict = {"temperature": temperature}

        # ── Max tokens: cap generation length to prevent runaway thinking ──
        if max_tokens:
            options["maxTokens"] = max_tokens
        else:
            default_predict = 2048 if response_format == "json" or schema else 4096
            options["maxTokens"] = default_predict

        # Build the messages, injecting JSON instructions if needed
        effective_msgs = list(messages)  # shallow copy
        if schema is not None or response_format == "json":
            # Prism doesn't support Ollama's format field,
            # so we enforce JSON output via system prompt instruction
            json_instruction = (
                "\n\nIMPORTANT: You MUST respond with valid JSON only. "
                "No markdown, no code fences, no explanations — pure JSON."
            )
            if schema is not None:
                import json as _json
                json_instruction += (
                    f"\n\nYour response MUST conform to this JSON Schema:\n"
                    f"{_json.dumps(schema, indent=2)}"
                )
            # Append to system message
            if effective_msgs and effective_msgs[0].get("role") == "system":
                effective_msgs[0] = {
                    **effective_msgs[0],
                    "content": effective_msgs[0]["content"] + json_instruction,
                }

        payload: dict = {
            "provider": "ollama",
            "model": self.model,
            "messages": effective_msgs,
            "options": options,
        }

        # ── Pass `format` field for Ollama constrained decoding ──
        # When Ollama receives a JSON Schema in the `format` field,
        # it uses GBNF grammar to guarantee valid JSON at the token
        # generation level — no more malformed output.
        if schema is not None:
            payload["format"] = schema  # Full JSON Schema → GBNF enforcement
        elif response_format == "json":
            payload["format"] = "json"  # Generic JSON mode

        # ── Conversation tracking via conversationMeta ──
        # Prism auto-creates a conversation when conversationId +
        # conversationMeta are present in the payload.
        import uuid
        from datetime import datetime as _dt, timezone as _tz

        conversation_id = str(uuid.uuid4())
        title_parts = []
        if audit_ticker:
            title_parts.append(audit_ticker)
        if audit_step:
            title_parts.append(audit_step)
        if audit_cycle_id:
            title_parts.append(f"cycle:{audit_cycle_id[:8]}")
        conv_title = " — ".join(title_parts) if title_parts else f"{self.model} generation"

        payload["conversationId"] = conversation_id
        payload["conversationMeta"] = {
            "title": conv_title,
            "systemPrompt": (
                effective_msgs[0].get("content", "")[:500]
                if effective_msgs and effective_msgs[0].get("role") == "system"
                else ""
            ),
            "settings": {
                "provider": "ollama",
                "model": self.model,
                "temperature": temperature,
                "maxTokens": options.get("maxTokens"),
            },
        }

        # Extract user message content for Prism conversation auto-append
        user_content = ""
        for m in effective_msgs:
            if m.get("role") == "user":
                user_content = m.get("content", "")
                break

        payload["userMessage"] = {
            "content": user_content,
            "timestamp": _dt.now(_tz.utc).isoformat(),
        }

        # Prism auth headers
        headers = {
            "Content-Type": "application/json",
            "x-api-secret": settings.PRISM_SECRET,
            "x-project": settings.PRISM_PROJECT,
            "x-username": self.model,
        }

        # Derive a short context label from the system prompt
        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"

        # ── Dynamic timeout based on model size ──────────────────
        _base_timeout = settings.LLM_CALL_TIMEOUT_SECONDS
        _measurement = settings.LLM_VRAM_MEASUREMENTS.get(self.model, {})
        _file_size = _measurement.get("model_file_size", 0)
        _model_file_gb = _file_size / (1024**3) if _file_size else 0
        _timeout = max(_base_timeout, 300) if _model_file_gb > 15 else _base_timeout

        logger.info(
            "Prism request START -> %s model=%s format=%s maxTokens=%d timeout=%ds",
            url,
            self.model,
            response_format,
            options.get("maxTokens", 0),
            _timeout,
        )
        t0 = time.perf_counter()

        # ── Heartbeat: log every 30s so the user knows we're not stuck ──
        _hb_label = f"{self.model}"
        if audit_ticker:
            _hb_label += f" — {audit_ticker}"
        if audit_step:
            _hb_label += f" {audit_step}"

        async def _heartbeat() -> None:
            _elapsed = 0
            while True:
                await asyncio.sleep(30)
                _elapsed += 30
                logger.info(
                    "[LLM] ⏳ Still waiting on %s (%ds elapsed)…",
                    _hb_label, _elapsed,
                )

        _hb_task = asyncio.create_task(_heartbeat())

        try:
            try:
                client = await _get_shared_client()
                resp = await asyncio.wait_for(
                    client.post(url, json=payload, headers=headers),
                    timeout=_timeout,
                )
                resp.raise_for_status()
            except (TimeoutError, httpx.ReadTimeout):
                elapsed = time.perf_counter() - t0
                logger.error(
                    "Prism request TIMEOUT after %.1fs (limit=%ds)",
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
            except httpx.HTTPStatusError as exc:
                elapsed = time.perf_counter() - t0
                error_body = ""
                try:
                    error_body = exc.response.text[:500]
                except Exception:
                    pass
                logger.warning(
                    "Prism request FAILED -> %.1fs: HTTP %d — %s",
                    elapsed,
                    exc.response.status_code,
                    error_body or str(exc)[:120],
                )
                log_llm_call(
                    context=_ctx,
                    model=self.model,
                    duration_seconds=elapsed,
                    error=f"HTTP {exc.response.status_code}: {error_body[:120]}",
                )
                if exc.response.status_code >= 500:
                    return ""
                raise
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                # Connection-level failures — Prism may be loading the model
                elapsed = time.perf_counter() - t0
                exc_type = type(exc).__name__
                logger.warning(
                    "Prism request CONN_ERROR -> %.1fs: %s: %s",
                    elapsed,
                    exc_type,
                    str(exc)[:200] or "(no message)",
                )
                log_llm_call(
                    context=_ctx,
                    model=self.model,
                    duration_seconds=elapsed,
                    error=f"{exc_type}: {str(exc)[:120]}",
                )
                # Return empty so dual-mode retry can attempt again
                return ""
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                exc_type = type(exc).__name__
                logger.warning(
                    "Prism request FAILED -> %.1fs: %s: %s",
                    elapsed,
                    exc_type,
                    str(exc)[:200] or repr(exc)[:200],
                )
                log_llm_call(
                    context=_ctx,
                    model=self.model,
                    duration_seconds=elapsed,
                    error=f"{exc_type}: {str(exc)[:120]}",
                )
                raise
        finally:
            _hb_task.cancel()

        data = resp.json()
        content = data.get("text", "")
        thinking = data.get("thinking", "") or ""
        usage = data.get("usage", {})
        tokens = usage.get("outputTokens", 0)

        # ── Thinking-model fallback ──
        # Some thinking models put all reasoning in `thinking` and
        # leave `text` empty. Try to extract JSON from thinking.
        if not content.strip() and thinking.strip():
            import json as _json

            # Strip <think>...</think> blocks from thinking text
            clean_thinking = re.sub(
                r"<think>.*?</think>",
                "",
                thinking,
                flags=re.DOTALL,
            ).strip()
            text_to_parse = clean_thinking or thinking

            candidate = LLMService.clean_json_response(text_to_parse)
            if candidate.strip().startswith("{"):
                try:
                    parsed = _json.loads(candidate)
                    content = candidate
                    _keys = list(parsed.keys())[:5]
                    logger.info(
                        "[LLM] Extracted JSON from thinking field: keys=%s (%d chars)",
                        _keys,
                        len(content),
                    )
                except _json.JSONDecodeError:
                    pass

            if not content.strip():
                content = clean_thinking or thinking
                logger.warning(
                    "[LLM] Using raw thinking text as response (%d chars) — no JSON found",
                    len(content),
                )

        elapsed = time.perf_counter() - t0

        if thinking:
            logger.info(
                "Prism request DONE  -> %.2fs, %d chars (thinking: %d chars) [%s]",
                elapsed,
                len(content),
                len(thinking),
                _hb_label,
            )
        else:
            logger.info(
                "Prism request DONE  -> %.2fs, %d chars [%s]",
                elapsed,
                len(content),
                _hb_label,
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
                    "✂️ Trimmed message[%d] from %d → %d chars",
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
        to extract only the first complete {...} object, then apply light
        repairs for common LLM output quirks.
        """
        # Strip <think>...</think> blocks from reasoning models
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)

        # Strip markdown code blocks
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
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
            extracted = cleaned[start : end + 1]
        else:
            # Incomplete object (truncated by max_tokens) — return what we have
            extracted = cleaned[start:]

        # ── JSON repair: fix common LLM output quirks ──
        extracted = LLMService._repair_json(extracted)

        return extracted

    @staticmethod
    def _repair_json(text: str) -> str:
        """Light JSON repair for common LLM mistakes.

        Fixes: trailing commas, NaN/Infinity literals, single-quoted strings,
        unescaped control characters.
        """
        # Replace NaN, Infinity, -Infinity with null (invalid in JSON)
        text = re.sub(r"\bNaN\b", "null", text)
        text = re.sub(r"\bInfinity\b", "99999999", text)
        text = re.sub(r"-Infinity\b", "-99999999", text)

        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)

        # Replace single-quoted strings with double-quoted (simple cases)
        # Only do this outside of already-double-quoted strings
        # This is a best-effort repair; complex nested quotes may fail
        text = re.sub(r"(?<![\"\\])'([^']*)'(?![\"\\])", r'"\1"', text)

        # Remove control characters that break JSON (except \n \r \t)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

        return text

    @staticmethod
    async def fetch_models(base_url: str) -> list[str]:
        """Probe an Ollama URL directly and return available model names.

        Used by model warmup and VRAM estimation (direct Ollama access).
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
    async def fetch_models_from_prism() -> list[str]:
        """Fetch available Ollama models via the Prism /config endpoint.

        Returns model names pulled from Prism's centralized config.
        Falls back to direct Ollama if Prism is unreachable.
        """
        prism_url = settings.PRISM_URL.rstrip("/")
        try:
            headers = {
                "x-api-secret": settings.PRISM_SECRET,
                "x-project": settings.PRISM_PROJECT,
                "x-username": "trading-bot",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{prism_url}/config",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                # Extract Ollama models from Prism config
                ttt = data.get("textToText", {})
                models_map = ttt.get("models", {})
                ollama_models = models_map.get("ollama", [])
                return [m.get("name", "") for m in ollama_models if m.get("name")]
        except Exception as exc:
            logger.warning(
                "[LLM] Prism model fetch failed, falling back to direct Ollama: %s",
                exc,
            )
            return await LLMService.fetch_models(settings.OLLAMA_URL)

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

                # Clean up ephemeral templated model if applicable
                try:
                    from app.services.TemplateRegistry import (
                        delete_ephemeral_model,
                        is_ephemeral_model,
                    )
                    if is_ephemeral_model(model):
                        await delete_ephemeral_model(base_url, model)
                except Exception:
                    pass  # Non-fatal

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

        # Also clean up any stale ephemeral templated models
        try:
            from app.services.TemplateRegistry import (
                cleanup_all_ephemeral_models,
            )
            cleaned = await cleanup_all_ephemeral_models(base_url)
            if cleaned:
                logger.info(
                    "[LLM] Cleaned up %d stale ephemeral model(s)",
                    cleaned,
                )
        except Exception:
            pass  # Non-fatal

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
         Model weight VRAM ≈ GGUF file size.

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
    def calculate_compute_optimal_ctx(
        model_file_size: int,
        kv_bytes_per_token: int,
        safe_ceiling_bytes: int,
        *,
        compute_reserve_pct: float = 0.30,
    ) -> int:
        """Calculate optimal context length that leaves VRAM headroom for compute.

        Instead of maximizing ctx until OOM, this reserves a percentage of
        usable VRAM for inference throughput (attention buffers, activation
        tensors, CUDA workspace).

        Formula:
            compute_reserve = safe_ceiling * compute_reserve_pct
            kv_budget = safe_ceiling - weights - graph - compute_reserve
            optimal_ctx = kv_budget / kv_bytes_per_token

        Args:
            model_file_size: Model weights in bytes.
            kv_bytes_per_token: KV cache bytes per context token.
            safe_ceiling_bytes: Total usable VRAM (after OS reserve).
            compute_reserve_pct: Fraction of safe ceiling to reserve
                for compute (default 0.30 = 30%).

        Returns:
            Optimal context length (clamped to [2048, 131072]).
        """
        if kv_bytes_per_token <= 0 or safe_ceiling_bytes <= 0:
            return 16384  # Safe default when VRAM info unavailable

        _GRAPH_OVERHEAD = int(0.5 * (1024**3))  # 0.5 GiB
        compute_reserve = int(safe_ceiling_bytes * compute_reserve_pct)
        kv_budget = safe_ceiling_bytes - model_file_size - _GRAPH_OVERHEAD - compute_reserve

        if kv_budget <= 0:
            logger.warning(
                "[LLM] Model weights (%.1f GiB) + compute reserve "
                "(%.1f GiB) exceed safe ceiling (%.1f GiB). "
                "Using minimum ctx=8192.",
                model_file_size / (1024**3),
                compute_reserve / (1024**3),
                safe_ceiling_bytes / (1024**3),
            )
            return 8192

        optimal = int(kv_budget / kv_bytes_per_token)
        # Round down to nearest 1024 for clean alignment
        optimal = (optimal // 1024) * 1024
        optimal = max(8192, min(optimal, 131072))

        logger.info(
            "[LLM] Compute-optimal ctx=%d "
            "(ceiling=%.1fG, weights=%.1fG, "
            "reserve=%.1fG [%d%%], kv_budget=%.1fG)",
            optimal,
            safe_ceiling_bytes / (1024**3),
            model_file_size / (1024**3),
            compute_reserve / (1024**3),
            int(compute_reserve_pct * 100),
            kv_budget / (1024**3),
        )
        return optimal

    @staticmethod
    async def verify_and_warm_ollama_model(
        base_url: str,
        model: str,
        *,
        keep_alive: str = "10m",
    ) -> dict:
        """Verify an Ollama model exists and pre-warm it.

        Flow:
          1. GET /api/tags  → verify model exists, get file size
          2. POST /api/show → get architecture (layers, kv_heads, head_dim)
          3. Load the model at the user’s desired context size

        Returns dict with model info. No calibration or VRAM audit.
        """
        base_url = base_url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                # —— Step 1: Verify model exists ——————————————————
                tags_resp = await client.get(f"{base_url}/api/tags")
                tags_resp.raise_for_status()
                tags_data = tags_resp.json().get("models", [])
                available = [m["name"] for m in tags_data]

                model_found = model in available
                model_file_size = 0

                if not model_found:
                    # Normalize: "ibm/granite-3.2-8b" → "granite328b"
                    #            "granite3.2:8b"       → "granite328b"
                    import re as _re

                    def _norm(n: str) -> str:
                        if "/" in n:
                            n = n.split("/", 1)[1]
                        return _re.sub(r"[.\-:_]", "", n).lower()

                    req_norm = _norm(model)
                    for avail_model in tags_data:
                        avail_name = avail_model["name"]
                        if (
                            avail_name == f"{model}:latest"
                            or _norm(avail_name) == req_norm
                            or req_norm in _norm(avail_name)
                            or _norm(avail_name) in req_norm
                        ):
                            model = avail_name
                            model_found = True
                            break

                if not model_found:
                    suggestions = [
                        m for m in available
                        if _norm(model) in _norm(m)
                        or _norm(m) in _norm(model)
                    ] if "_norm" in dir() else available[:5]
                    return {
                        "status": "model_not_found",
                        "model": model,
                        "available_models": available,
                        "suggestions": suggestions,
                        "model_found": False,
                    }

                # Get file size
                for m in tags_data:
                    if m["name"] == model:
                        model_file_size = m.get("size", 0)
                        break

                model_max_ctx = 0
                model_info: dict = {}

                # —— Step 2: Query architecture ———————————————————
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

                # -- Step 2.5: Compute desired context FIRST ------
                # (Needed before template injection so num_ctx can
                #  be baked into the ephemeral modelfile.)
                from app.config import settings as _cfg

                desired_ctx = _cfg.LLM_CONTEXT_SIZE
                if model_max_ctx > 0:
                    desired_ctx = min(desired_ctx, model_max_ctx)
                desired_ctx = max(desired_ctx, 2048)

                # -- Step 2.6: Template injection (if needed) ----------
                # Check if the model's template is missing or broken
                # and create an ephemeral wrapper with the correct one.
                from app.config import settings as _ti_cfg

                effective_model = model  # May change if we inject
                if _ti_cfg.TEMPLATE_INJECTION_ENABLED:
                    try:
                        from app.services.TemplateRegistry import (
                            ensure_template,
                        )

                        mode = _ti_cfg.TEMPLATE_INJECTION_MODE
                        injected_model = await ensure_template(
                            base_url, model, mode=mode, num_ctx=desired_ctx,
                        )
                        if injected_model != model:
                            effective_model = injected_model
                            logger.info(
                                "[LLM] \u2705 Template injected: using '%s' "
                                "instead of '%s'",
                                effective_model, model,
                            )
                    except Exception as ti_exc:
                        logger.warning(
                            "[LLM] Template injection failed (non-fatal): %s",
                            ti_exc,
                        )

                # —— Step 3: VRAM estimation (informational) ———————
                # (desired_ctx already computed above in Step 2.5)
                estimate = LLMService.estimate_model_vram(
                    model_info,
                    model_file_size,
                    desired_ctx,
                )
                kv_per_tok = estimate["kv_bytes_per_token"]

                est_gb = estimate["total_bytes"] / (1024**3)
                total_gpu = LLMService.get_total_vram_bytes()
                total_gb = total_gpu / (1024**3) if total_gpu else 0

                logger.info(
                    "[LLM] VRAM estimate for %s @ ctx=%d: "
                    "%.1f GiB needed (weights=%.1f + KV=%.1f + "
                    "graph=0.5), total=%.1f GiB",
                    model,
                    desired_ctx,
                    est_gb,
                    estimate["weights_bytes"] / (1024**3),
                    estimate["kv_bytes"] / (1024**3),
                    total_gb,
                )

                # —— Step 5: Flush OTHER models then load —————————
                import asyncio

                # Check if our model is already loaded in VRAM
                already_loaded = False
                try:
                    ps_resp = await client.get(f"{base_url}/api/ps")
                    ps_data = ps_resp.json()
                    loaded_models = ps_data.get("models", [])
                    loaded_names = [m.get("name", "") for m in loaded_models]

                    if effective_model in loaded_names or model in loaded_names:
                        already_loaded = True
                        logger.info(
                            "[LLM] ✅ Model %s already loaded in VRAM — "
                            "skipping unload/reload cycle",
                            effective_model,
                        )
                        # Only unload OTHER models to free memory
                        for lm in loaded_models:
                            lm_name = lm.get("name", "")
                            if lm_name and lm_name != effective_model and lm_name != model:
                                try:
                                    await client.post(
                                        f"{base_url}/api/generate",
                                        json={
                                            "model": lm_name,
                                            "prompt": "",
                                            "keep_alive": "0",
                                            "stream": False,
                                        },
                                        timeout=10.0,
                                    )
                                    logger.info(
                                        "[LLM] Evicted unneeded model %s", lm_name,
                                    )
                                except Exception:
                                    pass
                except Exception:
                    pass  # Can't check — proceed with full flush

                if not already_loaded:
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

                    # Load the model at user's desired context
                    # Use effective_model (may be ephemeral templated model)
                    try:
                        warm_resp = await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": effective_model,
                                "prompt": "",
                                "keep_alive": keep_alive,
                                "stream": False,
                                "options": {
                                    "num_ctx": desired_ctx,
                                    "num_gpu": 999,
                                },
                            },
                        )
                        warm_resp.raise_for_status()
                        logger.info(
                            "[LLM] ✅ Model %s loaded at ctx=%d",
                            effective_model,
                            desired_ctx,
                        )
                    except httpx.HTTPStatusError:
                        logger.warning(
                            "[LLM] ⚠️ Failed to load %s at ctx=%d",
                            effective_model,
                            desired_ctx,
                        )
                        return {
                            "status": "oom_error",
                            "model": effective_model,
                            "base_model": model,
                            "template_injected": effective_model != model,
                            "available_models": [m["name"] for m in tags_data],
                            "model_found": True,
                            "pre_warmed": False,
                            "requested_ctx": desired_ctx,
                            "message": (
                                f"Model {effective_model} failed to load at "
                                f"ctx={desired_ctx}. Try a lower context size."
                            ),
                        }

                # Return success (covers both already-loaded and freshly loaded)
                return {
                    "status": "model_verified",
                    "model": effective_model,
                    "base_model": model,
                    "template_injected": effective_model != model,
                    "available_models": [m["name"] for m in tags_data],
                    "model_found": True,
                    "pre_warmed": True,
                    "model_max_ctx": model_max_ctx,
                    "recommended_ctx": desired_ctx,
                    "kv_rate_bytes_per_token": kv_per_tok,
                }
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
        ollama_url = settings.OLLAMA_URL.rstrip("/")
        try:
            url = f"{ollama_url}/api/tags"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                return {
                    "status": "ok",
                    "provider": "ollama",
                    "prism_url": self.base_url,
                    "ollama_url": ollama_url,
                    "models": models,
                    "configured_model": self.model,
                    "model_available": self.model in models,
                }
        except Exception as e:
            return {
                "status": "error",
                "provider": "ollama",
                "prism_url": self.base_url,
                "ollama_url": ollama_url,
                "error": str(e),
            }
