"""Prompt Evolver — self-improvement loop for per-model system prompts.

After each trading run, evaluates the bot's extraction/trading performance
and asks the model to improve its own prompts. Each model evolves
independently — prompts are NEVER shared between models.
"""

from __future__ import annotations

import json
from typing import Any

from app.database import get_db
from app.services.AgenticExtractor import AgenticExtractor
from app.services.llm_service import LLMService
from app.utils.logger import logger

_EVOLUTION_PROMPT = """You are a prompt engineer reviewing your own system prompts.

CURRENT PROMPT (for step "{step_name}"):
---
{current_prompt}
---

PERFORMANCE DATA from last trading run:
- Transcripts processed: {transcripts_processed}
- Tickers extracted: {tickers_extracted}
- Empty extractions (no tickers found): {empty_extractions}
- Valid tickers (confirmed real): {valid_tickers}
- Trades placed: {trades_placed}
- Profitable trades: {profitable_trades}
- Parse failures (LLM output couldn't be parsed): {parse_failures}
- Repair successes (broken JSON fixed): {repair_successes}
- Repair failures (broken JSON couldn't be fixed): {repair_failures}
- Forced HOLDs (gave up parsing): {forced_holds}
- Decisions WITHOUT research tools: {no_tools_decisions}
- Decisions WITH research tools: {tool_decisions}

TASK: Improve the prompt above based on the performance data.
- If many extractions returned empty, make the prompt better at finding tickers
- If many tickers were invalid, add stricter validation instructions
- If trades lost money, consider adding more risk-awareness instructions
- If parse failures are high, simplify the output format instructions
- If no-tools decisions are high, make the prompt encourage tool usage
- Keep the prompt CONCISE — under 200 words
- Return ONLY the improved prompt text, nothing else."""


class PromptEvolver:
  """Evaluates and evolves per-model system prompts."""

  def __init__(self, bot_id: str) -> None:
    self.bot_id = bot_id
    self.llm = LLMService()
    self.extractor = AgenticExtractor(bot_id=bot_id)

  async def evolve(self) -> dict[str, Any]:
    """Run the self-improvement cycle for this bot.

    1. Gather performance stats from the last run
    2. For each prompt step, ask the model to improve it
    3. Store the new version

    Returns a summary of what changed.
    """
    stats = self._gather_stats()
    results: dict[str, Any] = {
      "bot_id": self.bot_id,
      "stats": stats,
      "evolved_steps": [],
    }

    # Only evolve extraction prompts (most impactful)
    for step_name in ("extraction_summarize", "extraction_extract"):
      try:
        evolved = await self._evolve_step(step_name, stats)
        if evolved:
          results["evolved_steps"].append(evolved)
      except Exception as exc:
        logger.warning(
          "[PromptEvolver] Failed to evolve %s for %s: %s",
          step_name, self.bot_id, exc,
        )

    return results

  def _gather_stats(self) -> dict[str, Any]:
    """Gather performance stats from this bot's last run."""
    conn = get_db()

    # Count recent extractions
    total_extractions = 0
    empty_extractions = 0
    tickers_extracted = 0
    try:
      # Count YouTube transcripts processed recently
      yt_count = conn.execute(
        "SELECT COUNT(*) FROM youtube_trading_data "
        "WHERE created_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
      ).fetchone()
      total_extractions = yt_count[0] if yt_count else 0

      # Count transcripts that produced tickers
      ticker_count = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM youtube_trading_data "
        "WHERE created_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR "
        "AND ticker IS NOT NULL AND ticker != ''",
      ).fetchone()
      tickers_extracted = ticker_count[0] if ticker_count else 0
      empty_extractions = max(0, total_extractions - tickers_extracted)
    except Exception:
      pass

    # Count recent trades for this bot
    trades_placed = 0
    profitable_trades = 0
    try:
      trades = conn.execute(
        "SELECT COUNT(*) FROM orders "
        "WHERE bot_id = ? AND created_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
        [self.bot_id],
      ).fetchone()
      trades_placed = trades[0] if trades else 0

      # Profitable sells
      profits = conn.execute(
        "SELECT COUNT(*) FROM orders "
        "WHERE bot_id = ? AND side = 'sell' "
        "AND created_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
        [self.bot_id],
      ).fetchone()
      profitable_trades = profits[0] if profits else 0
    except Exception:
      pass

    # Count validated vs invalid tickers
    valid_tickers = 0
    try:
      valid = conn.execute(
        "SELECT COUNT(*) FROM ticker_scores "
        "WHERE updated_at > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
      ).fetchone()
      valid_tickers = valid[0] if valid else 0
    except Exception:
      pass

    return {
      "transcripts_processed": total_extractions,
      "tickers_extracted": tickers_extracted,
      "empty_extractions": empty_extractions,
      "valid_tickers": valid_tickers,
      "trades_placed": trades_placed,
      "profitable_trades": profitable_trades,
      "parse_failures": self._count_events("trade_parse:parse_failed"),
      "repair_successes": self._count_events("trade_parse:repair_succeeded"),
      "repair_failures": self._count_events("trade_parse:repair_failed"),
      "forced_holds": self._count_events("trade_parse:forced_hold"),
      "no_tools_decisions": self._count_events("trading_agent:no_tools_used"),
      "tool_decisions": self._count_events("trading_agent:tool_usage"),
    }

  def _count_events(self, event_type: str) -> int:
    """Count pipeline events of a given type in the last 24 hours."""
    conn = get_db()
    try:
      row = conn.execute(
        "SELECT COUNT(*) FROM pipeline_events "
        "WHERE bot_id = ? AND event_type = ? "
        "AND timestamp > CURRENT_TIMESTAMP - INTERVAL 24 HOUR",
        [self.bot_id, event_type],
      ).fetchone()
      return row[0] if row else 0
    except Exception:
      return 0

  async def _evolve_step(
    self, step_name: str, stats: dict[str, Any],
  ) -> dict[str, Any] | None:
    """Ask the model to improve one prompt step."""
    current_prompt = self.extractor.get_prompt(step_name)
    current_version = self.extractor.get_prompt_version(step_name)

    if not current_prompt:
      return None

    # Skip evolution if there's not enough data to learn from
    if stats["transcripts_processed"] < 3:
      logger.info(
        "[PromptEvolver] Not enough data to evolve %s (only %d transcripts)",
        step_name, stats["transcripts_processed"],
      )
      return None

    # Ask the model to improve its own prompt
    evolution_prompt = _EVOLUTION_PROMPT.format(
      step_name=step_name,
      current_prompt=current_prompt,
      **stats,
    )

    new_prompt = await self.llm.chat(
      system="You are a prompt engineer. Return ONLY the improved prompt text.",
      user=evolution_prompt,
      response_format="text",
      audit_step=f"prompt_evolution_{step_name}",
    )

    new_prompt = new_prompt.strip()

    # Sanity checks
    if not new_prompt or len(new_prompt) < 20:
      logger.warning(
        "[PromptEvolver] Evolution returned too short prompt for %s",
        step_name,
      )
      return None

    if len(new_prompt) > 3000:
      logger.warning(
        "[PromptEvolver] Evolution returned too long prompt for %s (%d chars)",
        step_name, len(new_prompt),
      )
      new_prompt = new_prompt[:3000]

    # Calculate a simple performance score
    score = 0.0
    total = stats["transcripts_processed"]
    if total > 0:
      extraction_rate = stats["tickers_extracted"] / total
      score = extraction_rate * 100

    # Store the new version
    new_version = current_version + 1
    reason = (
      f"auto_evolution: extraction_rate={stats['tickers_extracted']}/{total}, "
      f"trades={stats['trades_placed']}"
    )

    self.extractor._store_prompt(
      step_name,
      new_prompt,
      version=new_version,
      parent_version=current_version,
      reason=reason,
      score=score,
    )

    logger.info(
      "[PromptEvolver] Evolved %s v%d → v%d for bot %s (score=%.1f)",
      step_name, current_version, new_version, self.bot_id, score,
    )

    return {
      "step": step_name,
      "old_version": current_version,
      "new_version": new_version,
      "score": score,
      "reason": reason,
      "prompt_length": len(new_prompt),
    }
