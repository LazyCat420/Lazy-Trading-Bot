"""Cross-Bot Auditor — independent audit of one bot's work by a different bot.

Prevents the "doom loop" where a model checks its own work and spirals.
A RANDOMLY SELECTED different model audits against a fixed checklist.

Key design:
- Audit prompts are IMMUTABLE (hardcoded, not in model_logic_loops)
- The auditor bot is always DIFFERENT from the bot being audited
- Results feed into PromptEvolver as objective external feedback
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

from app.database import get_db
from app.services.bot_registry import BotRegistry
from app.services.llm_service import LLMService
from app.utils.logger import logger

# ── IMMUTABLE Audit Prompts ───────────────────────────────────────────
# These are NEVER stored in model_logic_loops and NEVER modified by any
# self-improvement process. They provide an independent, objective
# evaluation framework.

_AUDIT_SYSTEM_PROMPT = """You are an independent trading bot auditor.
Your job is to objectively evaluate another bot's trading run.
You MUST be critical and honest. Do NOT be generous — flag every issue.
Score each category 0-10 where 10 is perfect.
Return ONLY valid JSON."""

_AUDIT_CHECKLIST_PROMPT = """Audit this bot's trading run against the checklist below.
For each item, score 0-10 and give a brief reason.

BOT BEING AUDITED: {audited_bot_name} ({audited_model})
YOUR IDENTITY: {auditor_bot_name} ({auditor_model})

=== DATA FROM THE AUDITED BOT'S RUN ===
Tickers discovered: {tickers_discovered}
Tickers analyzed: {tickers_analyzed}
Extraction quality: {extraction_summary}
Trades placed: {trades_placed}
Portfolio P&L: {portfolio_pnl}
Run duration: {run_duration}s

=== AUDIT CHECKLIST ===
1. DATA_COVERAGE: Did the bot discover enough tickers? (>5 is good, >10 is great)
2. EXTRACTION_QUALITY: Were extracted tickers valid US stocks? Any junk/ETF/index tickers?
3. ANALYSIS_DEPTH: Did dossiers contain useful data? (price levels, catalysts, risks)
4. TRADING_DECISIONS: Were buy/sell/hold decisions reasonable given the data?
5. RISK_MANAGEMENT: Did the bot consider risks? Position sizing? Diversification?
6. PROMPT_QUALITY: Based on the results, are the bot's prompts producing good output?

Return JSON:
{{
  "overall_score": 7.5,
  "categories": {{
    "data_coverage": {{"score": 8, "reason": "Found 12 tickers across multiple sources"}},
    "extraction_quality": {{"score": 6, "reason": "2 of 12 were ETFs that slipped through"}},
    "analysis_depth": {{"score": 7, "reason": "Good price levels but missing catalyst data"}},
    "trading_decisions": {{"score": 5, "reason": "Too many HOLD decisions, not decisive enough"}},
    "risk_management": {{"score": 4, "reason": "No position sizing, over-concentrated"}},
    "prompt_quality": {{"score": 6, "reason": "Extraction prompt too verbose, trading prompt lacks clarity"}}
  }},
  "recommendations": [
    "Shorten extraction prompt — too many examples cause confusion",
    "Add explicit position sizing rules to trading prompt",
    "Filter ETFs more aggressively during extraction"
  ],
  "critical_issues": [
    "ETF tickers slipping through validates extraction"
  ]
}}"""


class CrossBotAuditor:
  """Randomly selects a different bot to audit another bot's work."""

  def __init__(self) -> None:
    pass

  async def audit_bot_run(
    self,
    audited_bot_id: str,
    run_report: dict[str, Any],
  ) -> dict[str, Any] | None:
    """Select a random auditor bot and have it audit the given bot's run.

    Args:
      audited_bot_id: The bot that just finished its run
      run_report: The report dict from autonomous_loop.run_full_loop()

    Returns:
      Audit report dict, or None if no auditor available.
    """
    # ── Pick a random DIFFERENT bot ────────────────────────────
    auditor_bot = self._select_auditor(audited_bot_id)
    if not auditor_bot:
      logger.info(
        "[CrossAudit] No other bots available to audit %s — skipping",
        audited_bot_id,
      )
      return None

    auditor_bot_id = auditor_bot["bot_id"]
    auditor_model = auditor_bot["model_name"]
    auditor_name = auditor_bot.get("display_name", auditor_model)

    # ── Get the audited bot's info ─────────────────────────────
    audited_bot = BotRegistry.get_bot(audited_bot_id)
    if not audited_bot:
      return None

    audited_model = audited_bot["model_name"]
    audited_name = audited_bot.get("display_name", audited_model)

    logger.info(
      "[CrossAudit] 🔍 %s (%s) will audit %s (%s)",
      auditor_name, auditor_model, audited_name, audited_model,
    )

    # ── Build audit context from the run report ────────────────
    phases = run_report.get("phases", {})
    discovery = phases.get("discovery", {})
    analysis = phases.get("analysis", {})
    trading = phases.get("trading", {})

    tickers_discovered = discovery.get("tickers_found", 0)
    tickers_analyzed = analysis.get("analyzed", 0)
    trades_placed = trading.get("orders", 0)

    # Get portfolio P&L for the audited bot
    portfolio_pnl = self._get_bot_pnl(audited_bot_id)

    # Build extraction summary
    extraction_summary = (
      f"{tickers_discovered} tickers discovered, "
      f"{tickers_analyzed} analyzed, "
      f"{trades_placed} trades placed"
    )

    # ── Run the audit using the AUDITOR's model ────────────────
    # Create an LLM service configured for the auditor's model
    llm = LLMService(model_override=auditor_model)

    audit_user_msg = _AUDIT_CHECKLIST_PROMPT.format(
      audited_bot_name=audited_name,
      audited_model=audited_model,
      auditor_bot_name=auditor_name,
      auditor_model=auditor_model,
      tickers_discovered=tickers_discovered,
      tickers_analyzed=tickers_analyzed,
      extraction_summary=extraction_summary,
      trades_placed=trades_placed,
      portfolio_pnl=f"${portfolio_pnl:.2f}",
      run_duration=run_report.get("total_seconds", 0),
    )

    try:
      raw = await llm.chat(
        system=_AUDIT_SYSTEM_PROMPT,
        user=audit_user_msg,
        response_format="json",
        audit_step="cross_bot_audit",
        audit_ticker=f"{audited_name}→{auditor_name}",
      )

      cleaned = LLMService.clean_json_response(raw)
      audit_result = json.loads(cleaned)
    except Exception as exc:
      logger.warning(
        "[CrossAudit] Audit failed for %s by %s: %s",
        audited_name, auditor_name, exc,
      )
      return {
        "status": "error",
        "error": str(exc),
        "auditor_bot_id": auditor_bot_id,
        "audited_bot_id": audited_bot_id,
      }

    # ── Store the audit report ─────────────────────────────────
    report = {
      "audited_bot_id": audited_bot_id,
      "audited_model": audited_model,
      "audited_name": audited_name,
      "auditor_bot_id": auditor_bot_id,
      "auditor_model": auditor_model,
      "auditor_name": auditor_name,
      "overall_score": audit_result.get("overall_score", 0),
      "categories": audit_result.get("categories", {}),
      "recommendations": audit_result.get("recommendations", []),
      "critical_issues": audit_result.get("critical_issues", []),
      "timestamp": datetime.now().isoformat(),
    }

    self._store_report(report)

    logger.info(
      "[CrossAudit] ✅ Audit complete: %s scored %.1f/10 by %s",
      audited_name,
      report["overall_score"],
      auditor_name,
    )

    return report

  # ── Auditor Selection ─────────────────────────────────────────────

  def _select_auditor(self, exclude_bot_id: str) -> dict[str, Any] | None:
    """Randomly select a different active bot to be the auditor."""
    all_bots = BotRegistry.list_bots()
    candidates = [b for b in all_bots if b["bot_id"] != exclude_bot_id]

    if not candidates:
      return None

    return random.choice(candidates)

  # ── Persistence ───────────────────────────────────────────────────

  def _store_report(self, report: dict[str, Any]) -> None:
    """Store the audit report in the database."""
    conn = get_db()
    try:
      conn.execute(
        "INSERT INTO bot_audit_reports "
        "(audited_bot_id, auditor_bot_id, overall_score, "
        " categories, recommendations, critical_issues) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
          report["audited_bot_id"],
          report["auditor_bot_id"],
          report["overall_score"],
          json.dumps(report["categories"]),
          json.dumps(report["recommendations"]),
          json.dumps(report["critical_issues"]),
        ],
      )
    except Exception as exc:
      logger.warning("[CrossAudit] Failed to store report: %s", exc)

  def _get_bot_pnl(self, bot_id: str) -> float:
    """Get the current P&L for a bot."""
    conn = get_db()
    try:
      latest = conn.execute(
        "SELECT total_portfolio_value FROM portfolio_snapshots "
        "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT 1",
        [bot_id],
      ).fetchone()
      first = conn.execute(
        "SELECT total_portfolio_value FROM portfolio_snapshots "
        "WHERE bot_id = ? ORDER BY timestamp ASC LIMIT 1",
        [bot_id],
      ).fetchone()
      if latest and first:
        return (latest[0] or 0) - (first[0] or 0)
    except Exception:
      pass
    return 0.0

  # ── Query ─────────────────────────────────────────────────────────

  @staticmethod
  def get_recent_audits(
    bot_id: str | None = None, limit: int = 10,
  ) -> list[dict]:
    """Get recent audit reports, optionally filtered by bot."""
    conn = get_db()
    try:
      if bot_id:
        rows = conn.execute(
          "SELECT * FROM bot_audit_reports "
          "WHERE audited_bot_id = ? "
          "ORDER BY created_at DESC LIMIT ?",
          [bot_id, limit],
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM bot_audit_reports "
          "ORDER BY created_at DESC LIMIT ?",
          [limit],
        ).fetchall()

      if not rows:
        return []

      cols = [d[0] for d in conn.execute(
        "SELECT * FROM bot_audit_reports LIMIT 0",
      ).description]
      return [dict(zip(cols, r)) for r in rows]
    except Exception:
      return []

  @staticmethod
  def get_bot_audit_score(bot_id: str) -> float | None:
    """Get the average audit score for a bot across all audits."""
    conn = get_db()
    try:
      row = conn.execute(
        "SELECT AVG(overall_score) FROM bot_audit_reports "
        "WHERE audited_bot_id = ?",
        [bot_id],
      ).fetchone()
      return row[0] if row and row[0] is not None else None
    except Exception:
      return None
