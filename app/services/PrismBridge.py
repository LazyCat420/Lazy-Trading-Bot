"""PrismBridge — forwards vLLM calls to Prism for centralized logging.

When LLM_PROVIDER is "vllm", calls bypass Prism entirely. This bridge
re-creates the Prism conversation payload from the vLLM request/response
and POSTs it to Prism's /chat endpoint as a fire-and-forget background
task. This gives you full visibility in Prism's admin dashboard without
modifying the Prism codebase.

Usage:
    # Called automatically from _send_vllm_request() — no manual use needed.
    from app.services.PrismBridge import PrismBridge
    await PrismBridge.forward_to_prism(messages, response, model, metadata)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.utils.logger import logger


class PrismBridge:
  """Non-blocking bridge that mirrors vLLM calls to Prism."""

  @staticmethod
  async def forward_to_prism(
    *,
    messages: list[dict],
    response_text: str,
    reasoning_text: str = "",
    model: str = "",
    tokens_used: int = 0,
    duration_seconds: float = 0.0,
    audit_ticker: str = "",
    audit_step: str = "",
    audit_cycle_id: str = "",
  ) -> None:
    """Forward a completed vLLM call to Prism as a conversation.

    This is fire-and-forget — failures are logged but never block
    the trading pipeline.
    """
    try:
      asyncio.create_task(
        PrismBridge._do_forward(
          messages=messages,
          response_text=response_text,
          reasoning_text=reasoning_text,
          model=model,
          tokens_used=tokens_used,
          duration_seconds=duration_seconds,
          audit_ticker=audit_ticker,
          audit_step=audit_step,
          audit_cycle_id=audit_cycle_id,
        )
      )
    except Exception as exc:
      logger.debug("[PrismBridge] Failed to create forward task: %s", exc)

  @staticmethod
  async def _do_forward(
    *,
    messages: list[dict],
    response_text: str,
    reasoning_text: str = "",
    model: str = "",
    tokens_used: int = 0,
    duration_seconds: float = 0.0,
    audit_ticker: str = "",
    audit_step: str = "",
    audit_cycle_id: str = "",
  ) -> None:
    """Internal: POST to Prism's /chat endpoint to create a conversation."""
    prism_url = settings.PRISM_URL.rstrip("/")

    # Build conversation title
    title_parts = []
    if audit_ticker:
      title_parts.append(audit_ticker)
    if audit_step:
      title_parts.append(audit_step)
    if audit_cycle_id:
      title_parts.append(f"cycle:{audit_cycle_id[:8]}")
    conv_title = " — ".join(title_parts) if title_parts else f"{model} (vLLM)"

    # Extract system + user from messages
    system_prompt = ""
    user_content = ""
    for m in messages:
      if m.get("role") == "system" and not system_prompt:
        system_prompt = m.get("content", "")
      elif m.get("role") == "user" and not user_content:
        user_content = m.get("content", "")

    conversation_id = str(uuid.uuid4())

    # Build the combined response for Prism display
    combined_response = response_text
    if reasoning_text:
      combined_response = (
        f"<thinking>\n{reasoning_text}\n</thinking>\n\n{response_text}"
      )

    payload = {
      "provider": "ollama",
      "model": model or settings.LLM_MODEL,
      "messages": messages,
      "options": {
        "temperature": settings.LLM_TEMPERATURE,
      },
      "conversationId": conversation_id,
      "conversationMeta": {
        "title": f"[vLLM] {conv_title}",
        "systemPrompt": system_prompt[:500],
        "settings": {
          "provider": "vllm",
          "model": model or settings.LLM_MODEL,
          "temperature": settings.LLM_TEMPERATURE,
        },
      },
      "userMessage": {
        "content": user_content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
      },
      # Pass a synthetic response so Prism logs the full exchange
      "_syntheticResponse": {
        "text": combined_response,
        "thinking": reasoning_text,
        "usage": {
          "outputTokens": tokens_used,
        },
        "duration_seconds": round(duration_seconds, 2),
      },
    }

    headers = {
      "Content-Type": "application/json",
      "x-api-secret": settings.PRISM_SECRET,
      "x-project": settings.PRISM_PROJECT,
      "x-username": model or settings.LLM_MODEL,
    }

    try:
      async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
          f"{prism_url}/chat?stream=false",
          json=payload,
          headers=headers,
        )
        if resp.status_code < 400:
          logger.info(
            "[PrismBridge] ✅ Forwarded vLLM call to Prism: %s (conv=%s)",
            conv_title,
            conversation_id[:8],
          )
        else:
          logger.debug(
            "[PrismBridge] Prism returned %d for %s",
            resp.status_code,
            conv_title,
          )
    except httpx.ConnectError:
      logger.debug(
        "[PrismBridge] Prism not reachable at %s — skipping forward",
        prism_url,
      )
    except Exception as exc:
      logger.debug(
        "[PrismBridge] Forward failed for %s: %s",
        conv_title,
        str(exc)[:120],
      )
