"""Agentic Extractor — multi-step extraction with per-model self-improving prompts.

Each model (bot) has its own set of evolving system prompts stored in the
model_logic_loops table. The extraction runs in 3 steps:

  1. Summarize — short summary of the transcript
  2. Extract  — pull tickers and trading data from the summary
  3. Self-Question — LLM generates and answers its own follow-up questions

If no custom prompts exist for a bot, seed defaults are used and stored.
"""

from __future__ import annotations

import json
from typing import Any

from app.database import get_db
from app.services.llm_service import LLMService
from app.utils.logger import logger

# ── Seed Defaults ─────────────────────────────────────────────────────
# These are intentionally SHORT. The model will evolve them over time.

SEED_PROMPTS: dict[str, str] = {
  "extraction_summarize": (
    "You are a financial data extraction system. Extract the following from "
    "the video transcript below. Output ONLY the extraction, nothing else.\n\n"
    "Extract:\n"
    "- All stocks, assets, ETFs, or markets discussed (use ticker symbols)\n"
    "- Key price levels, support/resistance, moving averages mentioned\n"
    "- Technical setups or chart patterns described\n"
    "- Catalysts: earnings dates, FDA approvals, product launches, etc.\n"
    "- The creator's sentiment for each stock (bullish/bearish/neutral)\n"
    "- Any analyst ratings, price targets, or earnings data cited\n"
    "- Risk factors or bearish arguments mentioned\n\n"
    "RULES:\n"
    "- Do NOT ask questions or offer to refine anything\n"
    "- Do NOT add commentary, opinions, or conversational text\n"
    "- Write ONLY the extracted data points, no filler\n"
    "- If a data point is not in the transcript, do not make it up"
  ),
  "extraction_extract": (
    "From this summary, extract US stock tickers (NYSE/NASDAQ only).\n"
    "Rules:\n"
    "- Only REAL US stock tickers, no ETFs/indices/crypto/forex\n"
    "- Resolve company names to tickers (e.g. Intel → INTC)\n"
    "- If no US stocks are discussed, return empty tickers array\n\n"
    "Return JSON:\n"
    '{"tickers": ["NVDA", "INTC"], "trading_data": {\n'
    '  "sentiment": "bullish|bearish|neutral|mixed",\n'
    '  "summary": "one-line summary of key points",\n'
    '  "price_levels": ["$X support", "$Y resistance"],\n'
    '  "catalysts": ["event 1", "event 2"],\n'
    '  "risks": ["risk 1"],\n'
    '  "technicals": "brief technical setup"\n'
    "}}\n"
    "If no trading data, set trading_data to null."
  ),
  "extraction_self_question": (
    "You are a trading analyst. Given the tickers extracted and the video summary,\n"
    "generate exactly 3 follow-up questions AND answer them based ONLY on what\n"
    "was discussed in the transcript. Each question must be actionable for a\n"
    "buy/sell/hold decision.\n\n"
    "Focus your questions on:\n"
    "- Earnings, revenue growth, and profitability metrics mentioned\n"
    "- Technical levels (support, resistance, moving averages, volume trends)\n"
    "- Upcoming catalysts (earnings dates, FDA approvals, product launches)\n"
    "- Risk factors (debt levels, competition, regulatory threats)\n"
    "- Valuation (P/E, forward P/E, price-to-sales) vs sector peers\n\n"
    "DO NOT ask vague or open-ended questions. Every question must lead to\n"
    "a concrete data point that helps decide: should we BUY, SELL, or HOLD?\n\n"
    "Return JSON:\n"
    '{"questions": ["Q1", "Q2", "Q3"],\n'
    ' "answers": ["A1 based on transcript data", "A2 based on transcript data", "A3 based on transcript data"]}'
  ),
  "trading_agent": (
    "You are a trading analyst. Given market data and analysis,\n"
    "decide: BUY, SELL, or HOLD with a confidence score 0-1.\n"
    "Be decisive — avoid defaulting to HOLD."
  ),
  "peer_discovery": (
    "Given a stock ticker and its sector/industry, identify 3 direct\n"
    "competitors traded on NYSE/NASDAQ. Return a JSON array of 3 tickers."
  ),
}


class AgenticExtractor:
  """Multi-step agentic extraction with per-model prompt evolution."""

  def __init__(self, bot_id: str = "default") -> None:
    self.bot_id = bot_id
    self.llm = LLMService()

  # ── Prompt Loading ────────────────────────────────────────────────

  def get_prompt(self, step_name: str) -> str:
    """Load the active prompt for this bot + step. Seeds if missing."""
    conn = get_db()
    rows = conn.execute(
      "SELECT system_prompt FROM model_logic_loops "
      "WHERE bot_id = ? AND step_name = ? AND is_active = TRUE "
      "ORDER BY version DESC LIMIT 1",
      [self.bot_id, step_name],
    ).fetchall()

    if rows:
      return rows[0][0]

    # No prompt exists — seed the default
    seed = SEED_PROMPTS.get(step_name, "")
    if seed:
      self._store_prompt(step_name, seed, version=1, reason="initial_seed")
    return seed

  def _store_prompt(
    self,
    step_name: str,
    prompt: str,
    *,
    version: int = 1,
    parent_version: int | None = None,
    reason: str = "",
    score: float = 0.0,
  ) -> None:
    """Store a prompt version in the DB."""
    conn = get_db()
    # Deactivate old versions
    conn.execute(
      "UPDATE model_logic_loops SET is_active = FALSE "
      "WHERE bot_id = ? AND step_name = ? AND is_active = TRUE",
      [self.bot_id, step_name],
    )
    # Insert new version
    conn.execute(
      "INSERT INTO model_logic_loops "
      "(bot_id, step_name, system_prompt, version, performance_score, "
      " is_active, parent_version, mutation_reason) "
      "VALUES (?, ?, ?, ?, ?, TRUE, ?, ?)",
      [self.bot_id, step_name, prompt, version, score, parent_version, reason],
    )
    logger.info(
      "[AgenticExtractor] Stored prompt v%d for %s/%s (%s)",
      version, self.bot_id, step_name, reason,
    )

  def get_prompt_version(self, step_name: str) -> int:
    """Get the current active version number for a step."""
    conn = get_db()
    row = conn.execute(
      "SELECT version FROM model_logic_loops "
      "WHERE bot_id = ? AND step_name = ? AND is_active = TRUE "
      "ORDER BY version DESC LIMIT 1",
      [self.bot_id, step_name],
    ).fetchone()
    return row[0] if row else 0

  # ── Seed All Prompts ──────────────────────────────────────────────

  def seed_all_prompts(self) -> None:
    """Seed all default prompts for this bot. Called on bot registration.

    Also detects stale prompts: if the stored prompt doesn't match the
    current SEED_PROMPTS, it creates a new version with the updated text.
    This ensures prompt improvements propagate without manual DB work.
    """
    conn = get_db()
    for step_name, seed_prompt in SEED_PROMPTS.items():
      # Check current active prompt
      row = conn.execute(
        "SELECT version, system_prompt FROM model_logic_loops "
        "WHERE bot_id = ? AND step_name = ? AND is_active = TRUE "
        "ORDER BY version DESC LIMIT 1",
        [self.bot_id, step_name],
      ).fetchone()

      if not row:
        # No prompt exists — seed it
        self._store_prompt(step_name, seed_prompt, version=1, reason="initial_seed")
      elif row[1] != seed_prompt:
        # Prompt exists but is stale — upgrade it
        old_version = row[0]
        self._store_prompt(
          step_name, seed_prompt,
          version=old_version + 1,
          parent_version=old_version,
          reason="seed_prompt_upgrade",
        )
        logger.info(
          "[AgenticExtractor] Upgraded stale prompt %s/%s v%d → v%d",
          self.bot_id, step_name, old_version, old_version + 1,
        )
    logger.info("[AgenticExtractor] Seeded/verified all prompts for bot %s", self.bot_id)

  # ── Multi-Step Extraction ─────────────────────────────────────────

  async def extract_from_transcript(
    self,
    transcript: str,
    title: str = "",
    channel: str = "",
  ) -> dict[str, Any]:
    """Run the 3-step agentic extraction pipeline.

    Returns:
      {"tickers": [...], "trading_data": {...}, "follow_ups": {...}}
    """
    result: dict[str, Any] = {
      "tickers": [],
      "trading_data": None,
      "follow_ups": None,
      "extraction_meta": {
        "bot_id": self.bot_id,
        "steps_completed": 0,
      },
    }

    # ── Step 1: Summarize ──────────────────────────────────────
    summarize_prompt = self.get_prompt("extraction_summarize")
    user_msg = f"VIDEO: {title}\nCHANNEL: {channel}\n\nTRANSCRIPT:\n{transcript[:12000]}"

    summary = await self.llm.chat(
      system=summarize_prompt,
      user=user_msg,
      response_format="text",
      audit_step="agentic_summarize",
      audit_ticker=title[:30],
    )

    if not summary.strip():
      logger.warning("[AgenticExtractor] Step 1 (summarize) returned empty")
      return result

    result["extraction_meta"]["steps_completed"] = 1
    result["extraction_meta"]["summary"] = summary.strip()[:500]
    logger.info(
      "[AgenticExtractor] Step 1 done — summary: %s", summary.strip()[:100],
    )

    # ── Step 2: Extract tickers + data ─────────────────────────
    extract_prompt = self.get_prompt("extraction_extract")
    user_msg_2 = (
      f"VIDEO: {title}\nCHANNEL: {channel}\n\n"
      f"SUMMARY:\n{summary.strip()}"
    )

    raw_extract = await self.llm.chat(
      system=extract_prompt,
      user=user_msg_2,
      response_format="json",
      audit_step="agentic_extract",
      audit_ticker=title[:30],
    )

    try:
      cleaned = LLMService.clean_json_response(raw_extract)
      parsed = json.loads(cleaned)
      result["tickers"] = parsed.get("tickers", [])
      result["trading_data"] = parsed.get("trading_data")
      result["extraction_meta"]["steps_completed"] = 2
      logger.info(
        "[AgenticExtractor] Step 2 done — %d tickers extracted",
        len(result["tickers"]),
      )
    except (json.JSONDecodeError, Exception) as exc:
      logger.warning("[AgenticExtractor] Step 2 JSON parse failed: %s", exc)

    # ── Step 3: Self-question (only if tickers found) ──────────
    if result["tickers"]:
      self_q_prompt = self.get_prompt("extraction_self_question")
      # Give the LLM enough context to ANSWER its own questions
      trading_data_str = ""
      if result.get("trading_data"):
        trading_data_str = f"\nTrading Data: {json.dumps(result['trading_data'])}"
      user_msg_3 = (
        f"Tickers found: {', '.join(result['tickers'])}\n"
        f"Summary: {summary.strip()[:2000]}"
        f"{trading_data_str}\n\n"
        f"Generate 3 questions about these tickers and ANSWER each one "
        f"based on the information above. Focus on data that helps "
        f"decide BUY, SELL, or HOLD."
      )

      raw_questions = await self.llm.chat(
        system=self_q_prompt,
        user=user_msg_3,
        response_format="json",
        audit_step="agentic_self_question",
        audit_ticker=",".join(result["tickers"][:3]),
      )

      try:
        cleaned_q = LLMService.clean_json_response(raw_questions)
        result["follow_ups"] = json.loads(cleaned_q)
        result["extraction_meta"]["steps_completed"] = 3
        logger.info("[AgenticExtractor] Step 3 done — self-questions generated")
      except (json.JSONDecodeError, Exception):
        logger.warning("[AgenticExtractor] Step 3 JSON parse failed")

    return result

  # ── Prompt History ────────────────────────────────────────────────

  def get_prompt_history(self, step_name: str) -> list[dict]:
    """Get the version history for a step's prompts."""
    conn = get_db()
    rows = conn.execute(
      "SELECT version, performance_score, is_active, mutation_reason, "
      "created_at FROM model_logic_loops "
      "WHERE bot_id = ? AND step_name = ? "
      "ORDER BY version DESC LIMIT 10",
      [self.bot_id, step_name],
    ).fetchall()
    return [
      {
        "version": r[0],
        "score": r[1],
        "active": r[2],
        "reason": r[3],
        "created_at": str(r[4]),
      }
      for r in rows
    ]
