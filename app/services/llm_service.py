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

from app.services.unified_logger import track_class_telemetry, track_telemetry
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


@track_class_telemetry
class LLMService:
    """Sends chat completion requests to Ollama.

    All config values (model, context_size, temperature) are read LIVE
    from settings on every call, so hot-patching via the Settings UI
    takes effect immediately — no restart needed.
    """

    def __init__(self, *, model_override: str = "") -> None:
        self._model_override = model_override
        self._last_reasoning: str = ""  # Carries thinking from vLLM to audit
        self._last_prism_usage: dict | None = None  # Carries usage/TTFB from Prism done event

    @property
    def base_url(self) -> str:
        if settings.LLM_PROVIDER == "vllm":
            return settings.VLLM_URL
        return settings.OLLAMA_URL

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
            content = await self._call_provider(
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

        # Non-blocking audit log + conversation tracking
        try:
            from app.config import settings as _settings
            from app.services.ConversationTracker import ConversationTracker
            from app.services.llm_audit_logger import LLMAuditLogger

            # Determine provider
            provider = _settings.LLM_PROVIDER  # "vllm" or "prism"

            # Extract system/user from effective messages
            sys_text = ""
            usr_text = ""
            for m in effective_msgs:
                if m.get("role") == "system":
                    sys_text = m.get("content", "")
                elif m.get("role") == "user":
                    usr_text = m.get("content", "")

            # Capture reasoning from the last vLLM call (if any)
            reasoning_text = self._last_reasoning
            self._last_reasoning = ""  # Reset for next call

            # Capture Prism usage metrics (actual tokens, TTFB, tok/s)
            prism_usage = self._last_prism_usage
            self._last_prism_usage = None  # Reset for next call

            # Start a conversation record
            conv_title = f"${audit_ticker} — {audit_step}" if audit_ticker else audit_step or "LLM Call"
            conv_id = ConversationTracker.start_conversation(
                title=conv_title,
                model=self.model,
                provider=provider,
                system_prompt=sys_text,
                cycle_id=audit_cycle_id,
                ticker=audit_ticker,
                agent_step=audit_step,
            )

            # Use actual token count from Prism/vLLM if available,
            # otherwise fall back to rough estimate
            est_tokens = (
                prism_usage.get("outputTokens", self.estimate_tokens(content))
                if prism_usage
                else self.estimate_tokens(content)
            )
            ttfb_ms = (
                int(prism_usage["timeToGeneration"] * 1000)
                if prism_usage and prism_usage.get("timeToGeneration") is not None
                else None
            )
            ConversationTracker.add_message(
                conv_id,
                role="assistant",
                content=content,
                tokens=est_tokens,
                duration_ms=elapsed_ms,
            )

            # End the conversation
            ConversationTracker.end_conversation(conv_id)

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
                reasoning_content=reasoning_text,
                parsed_json=parsed,
                tokens_used=est_tokens,
                execution_time_ms=elapsed_ms,
                model=self.model,
                provider=provider,
                conversation_id=conv_id,
                ttfb_ms=ttfb_ms,
            )
        except Exception:
            pass  # Audit logging must never block trading

        return content

    async def _call_provider(
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
        """Call the configured LLM provider for text generation.

        Dispatches to Prism (Ollama) or vLLM based on settings.LLM_PROVIDER.
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
        # Only one request hits the GPU at a time; others wait in
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

            # ── Provider dispatch ──────────────────────────────
            provider = settings.LLM_PROVIDER
            if provider == "vllm":
                content = await self._send_vllm_request(
                    messages,
                    response_format,
                    max_tokens,
                    temperature,
                    schema=schema,
                    audit_ticker=audit_ticker,
                    audit_step=audit_step,
                    audit_cycle_id=audit_cycle_id,
                )
            else:
                content = await self._send_ollama_request(
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
                # Retry with the SAME provider — just format=text
                _retry_method = (
                    self._send_vllm_request if provider == "vllm"
                    else self._send_ollama_request
                )
                content = await _retry_method(
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

    async def _send_ollama_request(
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
        """Send a single request natively to Ollama."""
        from app.config import settings
        url = f"{settings.OLLAMA_URL.rstrip('/')}/api/chat"
        options: dict = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        else:
            options["num_predict"] = 4096 if response_format == "json" or schema else 4096

        effective_msgs = list(messages)
        if schema is not None or response_format == "json":
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
            if effective_msgs and effective_msgs[0].get("role") == "system":
                effective_msgs[0] = {
                    **effective_msgs[0],
                    "content": effective_msgs[0]["content"] + json_instruction,
                }

        payload: dict = {
            "model": self.model,
            "messages": effective_msgs,
            "stream": False,
            "options": options,
        }

        if schema is not None:
            payload["format"] = schema
        elif response_format == "json":
            payload["format"] = "json"

        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"
        _base_timeout = settings.LLM_CALL_TIMEOUT_SECONDS
        _measurement = settings.LLM_VRAM_MEASUREMENTS.get(self.model, {})
        _timeout = max(_base_timeout, 600) if _measurement.get("model_file_size", 0)/(1024**3) > 15 else max(_base_timeout, 600)

        import time, asyncio, httpx
        from app.services.llm_service import _get_shared_client, log_llm_call, logger
        
        logger.info(
            "Ollama request START -> %s model=%s format=%s timeout=%ds",
            url, self.model, response_format, _timeout,
        )
        t0 = time.perf_counter()
        _hb_label = f"{self.model}{' — ' + audit_ticker if audit_ticker else ''}{' ' + audit_step if audit_step else ''}"

        async def _heartbeat() -> None:
            _elapsed = 0
            while True:
                await asyncio.sleep(30)
                _elapsed += 30
                logger.info("[LLM] ⏳ Still waiting on %s (%ds elapsed)…", _hb_label, _elapsed)

        _hb_task = asyncio.create_task(_heartbeat())

        try:
            try:
                client = await _get_shared_client()
                resp = await asyncio.wait_for(client.post(url, json=payload), timeout=_timeout)
                resp.raise_for_status()
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                err_msg = str(exc)[:200]
                logger.warning("Ollama FAILED -> %.1fs: %s", elapsed, err_msg)
                log_llm_call(context=_ctx, model=self.model, duration_seconds=elapsed, error=f"{type(exc).__name__}: {err_msg[:120]}")
                if status_code and status_code >= 500: return ""
                if isinstance(exc, (httpx.ConnectError, httpx.ReadError)): return ""
                raise
        finally:
            _hb_task.cancel()

        data = resp.json()
        if "message" in data and "content" in data["message"]:
            content = data["message"]["content"]
        else:
            content = data.get("response", "")
            
        thinking = ""
        tokens = data.get("eval_count", 0)

        if "<think>" in content and "</think>" in content:
            import re
            thinking_match = re.search(r"<think>(.*?)</think>", content, flags=re.DOTALL)
            if thinking_match: thinking = thinking_match.group(1).strip()
            clean_content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            
            if not clean_content.strip() and thinking.strip():
                import json as _json
                candidate = LLMService.clean_json_response(clean_content or content)
                if candidate.strip().startswith("{"):
                    try:
                        _json.loads(candidate)
                        content = candidate
                    except _json.JSONDecodeError:
                        pass
                if not content.strip(): content = clean_content or content
            else:
                self._last_reasoning = thinking

        elapsed = time.perf_counter() - t0
        logger.info("Ollama request DONE  -> %.2fs, %d chars [%s]", elapsed, len(content), _hb_label)
        log_llm_call(context=_ctx, model=self.model, duration_seconds=elapsed, tokens_used=tokens)
        return content

    async def _send_vllm_request(
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
        """Send a streaming request to a vLLM server (OpenAI-compatible API).

        Uses SSE streaming with activity-based idle timeout:
        - Tokens flowing → never timeout (idle timer resets on each chunk)
        - No tokens for LLM_IDLE_TIMEOUT_SECONDS → abort
        - No hard wall-clock timeout on the generation itself

        Handles thinking/reasoning tokens from models like Qwen3.
        """
        import json as _json

        url = f"{settings.VLLM_URL.rstrip('/')}/v1/chat/completions"
        _via_prism = False
        _prism_headers: dict = {}

        # ── Route through Prism/Retina gateway if configured ──
        # Prism expects { provider, model, messages, options } format
        # and emits SSE events as { type: "chunk", content: "..." }
        if getattr(settings, "PRISM_URL", None):
            url = f"{settings.PRISM_URL.rstrip('/')}/chat"
            _via_prism = True
            # Prism auth headers
            _prism_secret = getattr(settings, "PRISM_SECRET", "") or ""
            _prism_project = getattr(settings, "PRISM_PROJECT", "") or "lazy-trading-bot"
            if not _prism_secret:
                # Try to read from llm_config.json
                try:
                    cfg_path = getattr(settings, "LLM_CONFIG_PATH", None)
                    if cfg_path and cfg_path.exists():
                        _cfg_data = _json.loads(cfg_path.read_text())
                        _prism_secret = _cfg_data.get("prism_secret", "")
                        _prism_project = _cfg_data.get("prism_project", "lazy-trading-bot")
                except Exception:
                    pass
            _prism_headers = {
                "x-api-secret": _prism_secret,
                "x-project": _prism_project,
                "x-username": "trading-bot",
                "Content-Type": "application/json",
            }
            logger.debug(
                "[LLM] Routing vLLM request through Prism: %s", url,
            )

        # Build the messages, injecting JSON instructions if needed
        effective_msgs = list(messages)
        if schema is not None or response_format == "json":
            json_instruction = (
                "\n\nIMPORTANT: You MUST respond with valid JSON only. "
                "No markdown, no code fences, no explanations — pure JSON."
            )
            if schema is not None:
                json_instruction += (
                    f"\n\nYour response MUST conform to this JSON Schema:\n"
                    f"{_json.dumps(schema, indent=2)}"
                )
            if effective_msgs and effective_msgs[0].get("role") == "system":
                effective_msgs[0] = {
                    **effective_msgs[0],
                    "content": effective_msgs[0]["content"] + json_instruction,
                }

        # Build payload — format depends on whether we go through Prism
        if _via_prism:
            # Prism expects: { provider, model, messages, options }
            payload: dict = {
                "provider": "vllm",
                "model": self.model,
                "messages": effective_msgs,
                "options": {
                    "temperature": temperature,
                    "maxTokens": max_tokens or 4096,
                },
            }
        else:
            # Direct vLLM: OpenAI-compatible payload
            payload: dict = {
                "model": self.model,
                "messages": effective_msgs,
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
                "stream": True,
            }

        # JSON mode
        if schema is not None or response_format == "json":
            if _via_prism:
                payload["options"]["responseFormat"] = {"type": "json_object"}
            else:
                payload["response_format"] = {"type": "json_object"}

        # Derive context label
        _ctx = messages[0].get("content", "")[:60] if messages else "unknown"

        # ── Timeouts ──
        _connect_timeout = settings.LLM_CALL_TIMEOUT_SECONDS  # For initial connection
        _idle_timeout = settings.LLM_IDLE_TIMEOUT_SECONDS      # Between chunks

        # ── Label for logging ──
        _hb_label = f"{self.model}"
        if audit_ticker:
            _hb_label += f" — {audit_ticker}"
        if audit_step:
            _hb_label += f" {audit_step}"

        logger.info(
            "vLLM STREAM START -> %s model=%s format=%s maxTokens=%d "
            "connect_timeout=%ds idle_timeout=%ds",
            url, self.model, response_format,
            payload.get("max_tokens", 0), _connect_timeout, _idle_timeout,
        )
        t0 = time.perf_counter()

        # ── Accumulators ──
        content_chunks: list[str] = []
        thinking_chunks: list[str] = []
        chunk_count = 0
        last_progress_log = 0  # Track when we last logged progress
        prism_usage: dict | None = None  # Captured from Prism done event

        try:
            client = await _get_shared_client()

            # Phase 1: Connect and start streaming
            # Use a dedicated client with read=idle_timeout so httpx automatically
            # raises ReadTimeout if no data arrives between chunks for idle_timeout seconds.
            _stream_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=float(_connect_timeout),
                    read=float(_idle_timeout),   # ← idle timeout between chunks
                    write=30.0,
                    pool=30.0,
                ),
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
            try:
                _req_headers = _prism_headers if _via_prism else {}
                resp = await _stream_client.send(
                    _stream_client.build_request("POST", url, json=payload, headers=_req_headers),
                    stream=True,
                )
                resp.raise_for_status()
            except (TimeoutError, httpx.ReadTimeout):
                elapsed = time.perf_counter() - t0
                logger.error(
                    "vLLM STREAM connect TIMEOUT after %.1fs (limit=%ds)",
                    elapsed, _connect_timeout,
                )
                log_llm_call(
                    context=_ctx, model=self.model,
                    duration_seconds=elapsed, timed_out=True,
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
                    "vLLM STREAM FAILED -> %.1fs: HTTP %d — %s",
                    elapsed, exc.response.status_code,
                    error_body or str(exc)[:120],
                )
                log_llm_call(
                    context=_ctx, model=self.model,
                    duration_seconds=elapsed,
                    error=f"HTTP {exc.response.status_code}: {error_body[:120]}",
                )
                if exc.response.status_code >= 500:
                    return ""
                raise
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                elapsed = time.perf_counter() - t0
                exc_type = type(exc).__name__
                logger.warning(
                    "vLLM STREAM CONN_ERROR -> %.1fs: %s: %s",
                    elapsed, exc_type, str(exc)[:200] or "(no message)",
                )
                log_llm_call(
                    context=_ctx, model=self.model,
                    duration_seconds=elapsed,
                    error=f"{exc_type}: {str(exc)[:120]}",
                )
                return ""

            # Phase 2: Stream chunks with idle timeout
            # Each chunk resets the idle timer — active generation never times out
            try:
                async for raw_line in resp.aiter_lines():
                    # Reset idle timer: we got data, model is alive
                    # (asyncio timeout is reset by the outer wait_for per-line)

                    if not raw_line.startswith("data: "):
                        continue

                    data_str = raw_line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk_data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue

                    # ── Dual SSE format parsing ──
                    # Prism format: { type: "chunk", content: "..." }
                    # OpenAI format: { choices: [{ delta: { content: "..." } }] }
                    chunk_type = chunk_data.get("type")

                    if chunk_type == "chunk" and "content" in chunk_data:
                        # Prism SSE format
                        if chunk_data["content"]:
                            content_chunks.append(chunk_data["content"])
                            chunk_count += 1
                    elif chunk_type == "thinking" and "content" in chunk_data:
                        # Prism thinking format
                        if chunk_data["content"]:
                            thinking_chunks.append(chunk_data["content"])
                            chunk_count += 1
                    elif chunk_type == "done":
                        # Prism done event — extract usage metrics
                        _done_usage = chunk_data.get("usage")
                        if _done_usage:
                            prism_usage = {
                                "outputTokens": _done_usage.get("outputTokens", 0),
                                "inputTokens": _done_usage.get("inputTokens", 0),
                            }
                        _done_tps = chunk_data.get("tokensPerSec")
                        if _done_tps:
                            prism_usage = prism_usage or {}
                            prism_usage["tokensPerSec"] = _done_tps
                        _done_ttg = chunk_data.get("timeToGeneration")
                        if _done_ttg is not None:
                            prism_usage = prism_usage or {}
                            prism_usage["timeToGeneration"] = _done_ttg
                        break
                    elif chunk_type == "error":
                        # Prism error event
                        err_msg = chunk_data.get("message", "Unknown Prism error")
                        logger.error("[vLLM/Prism] Error event: %s", err_msg)
                        break
                    else:
                        # OpenAI SSE format fallback
                        choices = chunk_data.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        # Content tokens
                        if delta.get("content"):
                            content_chunks.append(delta["content"])
                            chunk_count += 1

                        # Reasoning/thinking tokens (Qwen3)
                        if delta.get("reasoning_content"):
                            thinking_chunks.append(delta["reasoning_content"])
                            chunk_count += 1

                    # Log progress every 30s
                    elapsed_now = time.perf_counter() - t0
                    if elapsed_now - last_progress_log >= 30:
                        content_len = sum(len(c) for c in content_chunks)
                        thinking_len = sum(len(c) for c in thinking_chunks)
                        logger.info(
                            "[vLLM] ⏳ Streaming: %d chunks, %d content chars, "
                            "%d reasoning chars (%.1fs elapsed) [%s]",
                            chunk_count, content_len, thinking_len,
                            elapsed_now, _hb_label,
                        )
                        last_progress_log = elapsed_now

            except (httpx.ReadTimeout, TimeoutError):
                # Idle timeout fired — model went silent
                elapsed = time.perf_counter() - t0
                content_len = sum(len(c) for c in content_chunks)
                thinking_len = sum(len(c) for c in thinking_chunks)
                if chunk_count > 0:
                    logger.warning(
                        "[vLLM] ⚠️ Stream IDLE TIMEOUT after %.1fs — "
                        "model stopped sending tokens. Got %d chunks, "
                        "%d content chars, %d reasoning chars so far. "
                        "Using partial response. [%s]",
                        elapsed, chunk_count, content_len, thinking_len,
                        _hb_label,
                    )
                else:
                    logger.error(
                        "[vLLM] Stream IDLE TIMEOUT after %.1fs — "
                        "no tokens received at all [%s]",
                        elapsed, _hb_label,
                    )
                    log_llm_call(
                        context=_ctx, model=self.model,
                        duration_seconds=elapsed, timed_out=True,
                    )
                    return ""
            except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
                elapsed = time.perf_counter() - t0
                exc_type = type(exc).__name__
                content_len = sum(len(c) for c in content_chunks)
                if chunk_count > 0:
                    logger.warning(
                        "[vLLM] Stream broken after %d chunks (%.1fs): %s — "
                        "using partial response (%d chars) [%s]",
                        chunk_count, elapsed, exc_type, content_len,
                        _hb_label,
                    )
                else:
                    logger.error(
                        "[vLLM] Stream failed immediately: %s: %s [%s]",
                        exc_type, str(exc)[:200], _hb_label,
                    )
                    log_llm_call(
                        context=_ctx, model=self.model,
                        duration_seconds=elapsed,
                        error=f"{exc_type}: {str(exc)[:120]}",
                    )
                    return ""
            finally:
                await resp.aclose()
                await _stream_client.aclose()

        except Exception as exc:
            if not isinstance(exc, (TimeoutError, httpx.ReadTimeout,
                                    httpx.HTTPStatusError, httpx.ConnectError,
                                    httpx.ReadError, httpx.RemoteProtocolError)):
                elapsed = time.perf_counter() - t0
                exc_type = type(exc).__name__
                logger.warning(
                    "vLLM STREAM FAILED -> %.1fs: %s: %s",
                    elapsed, exc_type, str(exc)[:200] or repr(exc)[:200],
                )
                log_llm_call(
                    context=_ctx, model=self.model,
                    duration_seconds=elapsed,
                    error=f"{exc_type}: {str(exc)[:120]}",
                )
                raise

        # ── Assemble final response ──
        content = "".join(content_chunks)
        thinking = "".join(thinking_chunks)
        # Use actual token count from Prism/vLLM usage if available,
        # otherwise fall back to chunk_count
        tokens = (
            prism_usage.get("outputTokens", chunk_count)
            if prism_usage
            else chunk_count
        )

        # ── Thinking-model fallback (same as Prism path) ──
        if not content.strip() and thinking.strip():
            clean_thinking = re.sub(
                r"<think>.*?</think>", "", thinking, flags=re.DOTALL,
            ).strip()
            text_to_parse = clean_thinking or thinking

            candidate = LLMService.clean_json_response(text_to_parse)
            if candidate.strip().startswith("{"):
                try:
                    parsed = _json.loads(candidate)
                    content = candidate
                    _keys = list(parsed.keys())[:5]
                    logger.info(
                        "[vLLM] Extracted JSON from reasoning stream: keys=%s (%d chars)",
                        _keys, len(content),
                    )
                except _json.JSONDecodeError:
                    pass

            if not content.strip():
                content = clean_thinking or thinking
                logger.warning(
                    "[vLLM] Using raw reasoning text as response (%d chars) — no JSON found",
                    len(content),
                )

        elapsed = time.perf_counter() - t0

        # ── Store reasoning for audit logger in chat() ──
        self._last_reasoning = thinking
        # ── Store Prism usage metrics for chat() ──
        self._last_prism_usage = prism_usage

        if thinking:
            logger.info(
                "vLLM STREAM DONE -> %.2fs, %d content chars, %d reasoning chars, "
                "%d chunks [%s]",
                elapsed, len(content), len(thinking), chunk_count, _hb_label,
            )
        else:
            logger.info(
                "vLLM STREAM DONE -> %.2fs, %d chars, %d chunks [%s]",
                elapsed, len(content), chunk_count, _hb_label,
            )

        log_llm_call(
            context=_ctx, model=self.model,
            duration_seconds=elapsed, tokens_used=tokens,
        )

        # (Conversation tracking handled locally in chat() via ConversationTracker)

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
    async def fetch_models_from_vllm(vllm_url: str | None = None) -> list[str]:
        """Fetch available models from a vLLM server via /v1/models.

        vLLM serves an OpenAI-compatible API, so we query GET /v1/models.
        Returns a list of model ID strings.
        """
        base = (vllm_url or settings.VLLM_URL).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                data = resp.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            logger.warning("[LLM] vLLM model fetch failed (%s): %s", base, exc)
            return []

    @staticmethod
    async def verify_vllm_model(
        vllm_url: str | None = None,
        model: str | None = None,
    ) -> dict:
        """Verify a vLLM model is loaded and reachable.

        Pings GET /v1/models on the vLLM server (Docker container) and
        checks that the requested model name is in the served model list.

        Returns a result dict compatible with the Ollama pre-warm flow:
            {"pre_warmed": True, "base_model": ..., "model": ..., ...}
        """
        from app.config import settings as _cfg

        base = (vllm_url or _cfg.VLLM_URL).rstrip("/")
        target_model = model or _cfg.LLM_MODEL

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                data = resp.json()
                served = [m["id"] for m in data.get("data", [])]

            if not served:
                logger.warning(
                    "[LLM] vLLM at %s returned 0 models", base,
                )
                return {
                    "status": "no_models",
                    "model": target_model,
                    "base_model": target_model,
                    "pre_warmed": False,
                    "available_models": [],
                }

            # vLLM model IDs may differ in casing or prefix — do a fuzzy match
            model_found = target_model in served
            if not model_found:
                # Try substring match (e.g. "Qwen3.5-35B" in a longer path)
                for s in served:
                    if target_model in s or s in target_model:
                        target_model = s  # Use the served name
                        model_found = True
                        break

            if not model_found:
                logger.warning(
                    "[LLM] vLLM model '%s' not found — served: %s",
                    target_model, served,
                )
                return {
                    "status": "model_not_found",
                    "model": target_model,
                    "base_model": target_model,
                    "pre_warmed": False,
                    "available_models": served,
                }

            logger.info(
                "[LLM] ✅ vLLM model verified: %s @ %s",
                target_model, base,
            )
            return {
                "status": "model_verified",
                "model": target_model,
                "base_model": target_model,
                "pre_warmed": True,
                "model_found": True,
                "available_models": served,
                "recommended_ctx": _cfg.LLM_CONTEXT_SIZE,
                "model_max_ctx": _cfg.LLM_CONTEXT_SIZE,
                "vram_bytes": 0,  # vLLM manages its own VRAM
            }
        except Exception as exc:
            logger.warning(
                "[LLM] vLLM verification failed (%s): %s", base, exc,
            )
            return {
                "status": "verification_failed",
                "error": str(exc),
                "model": target_model,
                "base_model": target_model,
                "pre_warmed": False,
            }

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
                    
                    "ollama_url": ollama_url,
                    "models": models,
                    "configured_model": self.model,
                    "model_available": self.model in models,
                }
        except Exception as e:
            return {
                "status": "error",
                "provider": "ollama",
                
                "ollama_url": ollama_url,
                "error": str(e),
            }
