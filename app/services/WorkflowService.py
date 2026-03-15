"""WorkflowService — posts pipeline workflows to Prism for Retna display.

Tracks LLM steps during a trading cycle and posts the completed workflow
to Prism's POST /workflows endpoint using the raw steps format.  Prism's
WorkflowAssembler converts steps into a visual graph for the Retna admin
dashboard.

Usage:
    from app.services.WorkflowService import WorkflowTracker

    tracker = WorkflowTracker(title="Discovery — AAPL")
    tracker.add_step(
        model="olmo-3:latest",
        label="Summarize article",
        system_prompt="...",
        user_input="...",
        output="...",
        duration=12.5,
        conversation_id="abc-123",
    )
    await tracker.post_workflow()
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.utils.logger import logger


class WorkflowTracker:
    """Collects LLM pipeline steps and posts them to Prism as a workflow."""

    def __init__(
        self,
        *,
        title: str = "Trading Pipeline",
        source: str = "lazy-trading-bot",
    ) -> None:
        self.title = title
        self.source = source
        self.steps: list[dict] = []
        self.conversation_ids: list[str] = []
        self._workflow_id: str | None = None
        self._start_time = time.monotonic()

    def add_step(
        self,
        *,
        model: str,
        label: str,
        system_prompt: str = "",
        user_input: str = "",
        output: str = "",
        duration: float = 0.0,
        conversation_id: str = "",
    ) -> None:
        """Record a pipeline step (one LLM call)."""
        step = {
            "model": model,
            "type": "ollama",
            "label": label,
            "systemPrompt": system_prompt[:500] if system_prompt else "",
            "input": user_input[:2000] if user_input else "",
            "output": output[:2000] if output else "",
            "duration": round(duration, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "index": len(self.steps),
        }
        self.steps.append(step)

        if conversation_id:
            self.conversation_ids.append(conversation_id)

    async def post_workflow(self) -> str | None:
        """Post the collected steps to Prism's POST /workflows endpoint.

        Returns the workflow ID on success, or None on failure.
        """
        if not self.steps:
            logger.debug("[Workflow] No steps to post — skipping")
            return None

        prism_url = settings.PRISM_URL.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "x-api-secret": settings.PRISM_SECRET,
            "x-project": settings.PRISM_PROJECT,
            "x-username": "trading-bot",
        }

        total_duration = time.monotonic() - self._start_time
        payload = {
            "title": self.title,
            "source": self.source,
            "steps": self.steps,
            "totalDuration": round(total_duration, 2),
            "stepCount": len(self.steps),
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{prism_url}/workflows",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                self._workflow_id = data.get("id")

                logger.info(
                    "[Workflow] Posted %d steps to Prism — workflow_id=%s",
                    len(self.steps),
                    self._workflow_id,
                )

                # Link conversation IDs to the workflow
                if self._workflow_id and self.conversation_ids:
                    await self._link_conversations(
                        client, prism_url, headers,
                    )

                return self._workflow_id

        except Exception as exc:
            logger.warning(
                "[Workflow] Failed to post workflow to Prism: %s",
                str(exc)[:200],
            )
            return None

    async def _link_conversations(
        self,
        client: httpx.AsyncClient,
        prism_url: str,
        headers: dict,
    ) -> None:
        """Link conversation IDs to the workflow via PATCH."""
        try:
            await client.patch(
                f"{prism_url}/workflows/{self._workflow_id}/conversations",
                json={"conversationIds": self.conversation_ids},
                headers=headers,
            )
            logger.info(
                "[Workflow] Linked %d conversations to workflow %s",
                len(self.conversation_ids),
                self._workflow_id,
            )
        except Exception as exc:
            logger.warning(
                "[Workflow] Failed to link conversations: %s",
                str(exc)[:120],
            )
