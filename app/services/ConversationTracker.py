"""ConversationTracker — local conversation tracking (mirrors Prism's conversation model).

Every LLM call is wrapped in a conversation record with provider awareness,
token counting, and duration tracking. This replaces Prism-dependent
conversation logging so diagnostics are fully self-contained.

Usage (called automatically from LLMService.chat()):
    from app.services.ConversationTracker import ConversationTracker

    conv_id = ConversationTracker.start_conversation(
        title="AAPL — final_decision",
        model="qwen3-30b-a3b",
        provider="vllm",
        system_prompt="You are a trading analyst...",
        cycle_id="abc-123",
        ticker="AAPL",
        agent_step="final_decision",
    )
    ConversationTracker.add_message(conv_id, role="user", content="...", tokens=500)
    ConversationTracker.add_message(conv_id, role="assistant", content="...", tokens=800, duration_ms=12500)
    ConversationTracker.end_conversation(conv_id)
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime

from app.utils.logger import logger


def _get_conn():
  """Lazy import to avoid circular dependency at module load."""
  from app.database import get_db

  return get_db()


class ConversationTracker:
  """Local conversation tracking — mirrors Prism's conversation model."""

  @staticmethod
  def start_conversation(
    *,
    title: str = "",
    model: str = "",
    provider: str = "",
    system_prompt: str = "",
    cycle_id: str = "",
    ticker: str = "",
    agent_step: str = "",
  ) -> str:
    """Create a new conversation. Returns conversation_id."""
    conv_id = str(uuid.uuid4())
    try:
      conn = _get_conn()
      conn.execute(
        """
        INSERT INTO llm_conversations (
            id, cycle_id, title, model, provider,
            system_prompt, status, ticker, agent_step,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
          conv_id,
          cycle_id,
          title,
          model,
          provider,
          system_prompt[:10_000],
          "active",
          ticker,
          agent_step,
          datetime.now(),
        ],
      )
      logger.debug(
        "[Conversation] Started %s: %s (%s/%s)",
        conv_id[:8], title, provider, model,
      )
    except Exception as exc:
      logger.warning("[Conversation] Failed to start: %s", exc)
    return conv_id

  @staticmethod
  def add_message(
    conversation_id: str,
    *,
    role: str = "user",
    content: str = "",
    tokens: int = 0,
    duration_ms: int = 0,
  ) -> None:
    """Add a message to an existing conversation and update aggregate stats."""
    try:
      conn = _get_conn()
      conn.execute(
        """
        UPDATE llm_conversations
        SET total_tokens = total_tokens + ?,
            total_duration_ms = total_duration_ms + ?,
            message_count = message_count + 1,
            tokens_per_second = CASE
                WHEN (total_duration_ms + ?) > 0
                THEN CAST((total_tokens + ?) AS DOUBLE) / ((total_duration_ms + ?) / 1000.0)
                ELSE 0
            END
        WHERE id = ?
        """,
        [tokens, duration_ms, duration_ms, tokens, duration_ms, conversation_id],
      )
    except Exception as exc:
      logger.warning("[Conversation] Failed to add message: %s", exc)

  @staticmethod
  def end_conversation(
    conversation_id: str,
    *,
    status: str = "completed",
  ) -> None:
    """Mark a conversation as finished."""
    try:
      conn = _get_conn()
      conn.execute(
        """
        UPDATE llm_conversations
        SET status = ?,
            completed_at = ?
        WHERE id = ?
        """,
        [status, datetime.now(), conversation_id],
      )
      logger.debug("[Conversation] Ended %s: %s", conversation_id[:8], status)
    except Exception as exc:
      logger.warning("[Conversation] Failed to end: %s", exc)

  @staticmethod
  def get_active() -> list[dict]:
    """Get all currently active conversations."""
    try:
      conn = _get_conn()
      rows = conn.execute(
        """
        SELECT id, cycle_id, title, model, provider,
               status, total_tokens, total_duration_ms,
               tokens_per_second, message_count, ticker,
               agent_step, created_at
        FROM llm_conversations
        WHERE status = 'active'
        ORDER BY created_at DESC
        """
      ).fetchall()
      columns = [
        "id", "cycle_id", "title", "model", "provider",
        "status", "total_tokens", "total_duration_ms",
        "tokens_per_second", "message_count", "ticker",
        "agent_step", "created_at",
      ]
      return [
        {**dict(zip(columns, row, strict=False)), "created_at": str(row[-1])}
        for row in rows
      ]
    except Exception as exc:
      logger.warning("[Conversation] Active query failed: %s", exc)
      return []

  @staticmethod
  def get_recent(limit: int = 50, provider: str = "", model: str = "") -> list[dict]:
    """Get recent conversations with optional filters."""
    try:
      conn = _get_conn()
      where_clauses = []
      params = []

      if provider:
        where_clauses.append("provider = ?")
        params.append(provider)
      if model:
        where_clauses.append("model = ?")
        params.append(model)

      where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
      params.append(limit)

      rows = conn.execute(
        f"""
        SELECT id, cycle_id, title, model, provider,
               status, total_tokens, total_duration_ms,
               tokens_per_second, message_count, ticker,
               agent_step, created_at, completed_at
        FROM llm_conversations
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
      ).fetchall()
      columns = [
        "id", "cycle_id", "title", "model", "provider",
        "status", "total_tokens", "total_duration_ms",
        "tokens_per_second", "message_count", "ticker",
        "agent_step", "created_at", "completed_at",
      ]
      results = []
      for row in rows:
        d = dict(zip(columns, row, strict=False))
        d["created_at"] = str(d["created_at"])
        d["completed_at"] = str(d["completed_at"]) if d["completed_at"] else None
        results.append(d)
      return results
    except Exception as exc:
      logger.warning("[Conversation] Recent query failed: %s", exc)
      return []

  @staticmethod
  def get_by_id(conversation_id: str) -> dict | None:
    """Get full conversation detail."""
    try:
      conn = _get_conn()
      row = conn.execute(
        """
        SELECT id, cycle_id, title, model, provider,
               system_prompt, status, total_tokens, total_duration_ms,
               tokens_per_second, message_count, ticker,
               agent_step, created_at, completed_at
        FROM llm_conversations
        WHERE id = ?
        """,
        [conversation_id],
      ).fetchone()

      if not row:
        return None

      columns = [
        "id", "cycle_id", "title", "model", "provider",
        "system_prompt", "status", "total_tokens", "total_duration_ms",
        "tokens_per_second", "message_count", "ticker",
        "agent_step", "created_at", "completed_at",
      ]
      d = dict(zip(columns, row, strict=False))
      d["created_at"] = str(d["created_at"])
      d["completed_at"] = str(d["completed_at"]) if d["completed_at"] else None

      # Also fetch associated audit logs
      with contextlib.suppress(Exception):
        from app.services.llm_audit_logger import LLMAuditLogger
        logs = conn.execute(
          """
          SELECT id, ticker, agent_step, raw_response,
                 reasoning_content, tokens_used, execution_time_ms,
                 model, created_at
          FROM llm_audit_logs
          WHERE conversation_id = ?
          ORDER BY created_at ASC
          """,
          [conversation_id],
        ).fetchall()
        log_columns = [
          "id", "ticker", "agent_step", "raw_response",
          "reasoning_content", "tokens_used", "execution_time_ms",
          "model", "created_at",
        ]
        d["messages"] = [
          {**dict(zip(log_columns, r, strict=False)), "created_at": str(r[-1])}
          for r in logs
        ]

      return d
    except Exception as exc:
      logger.warning("[Conversation] Detail query failed: %s", exc)
      return None

  @staticmethod
  def get_summary() -> dict:
    """Aggregate diagnostics summary — total convos, tokens, tok/s by provider."""
    try:
      conn = _get_conn()

      # Overall stats
      overall = conn.execute(
        """
        SELECT
            COUNT(*) as total_conversations,
            SUM(total_tokens) as total_tokens,
            SUM(total_duration_ms) as total_duration_ms,
            COUNT(CASE WHEN status = 'active' THEN 1 END) as active_now,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed
        FROM llm_conversations
        """
      ).fetchone()

      # Per-provider breakdown
      by_provider = conn.execute(
        """
        SELECT
            provider,
            COUNT(*) as count,
            SUM(total_tokens) as tokens,
            AVG(tokens_per_second) as avg_tok_per_sec,
            AVG(total_duration_ms) as avg_duration_ms
        FROM llm_conversations
        WHERE provider != ''
        GROUP BY provider
        ORDER BY count DESC
        """
      ).fetchall()

      return {
        "total_conversations": overall[0] if overall else 0,
        "total_tokens": overall[1] or 0 if overall else 0,
        "total_duration_ms": overall[2] or 0 if overall else 0,
        "active_now": overall[3] if overall else 0,
        "completed": overall[4] if overall else 0,
        "by_provider": [
          {
            "provider": row[0],
            "count": row[1],
            "tokens": row[2] or 0,
            "avg_tok_per_sec": round(row[3] or 0, 1),
            "avg_duration_ms": round(row[4] or 0, 0),
          }
          for row in by_provider
        ],
      }
    except Exception as exc:
      logger.warning("[Conversation] Summary query failed: %s", exc)
      return {
        "total_conversations": 0, "total_tokens": 0,
        "total_duration_ms": 0, "active_now": 0, "completed": 0,
        "by_provider": [],
      }
