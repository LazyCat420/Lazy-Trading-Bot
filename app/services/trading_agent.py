"""Trading Agent — multi-turn LLM agent per ticker → TradeAction JSON.

The LLM starts with precomputed context (price, technicals, dossier) and
can optionally call research tools to dig deeper before making its final
BUY/SELL/HOLD decision.

Loop: up to MAX_RESEARCH_TURNS of tool calls, then a final decision.
If the LLM outputs a TradeAction on the first turn, no tools are called.
"""

from __future__ import annotations

import json

from app.models.trade_action import TRADE_ACTION_SCHEMA, TradeAction
from app.services.llm_service import LLMService
from app.services.research_tools import (
  RESEARCH_TOOL_NAMES,
  SEARCH_TOOL_DESCRIPTION,
  TOOL_REGISTRY,
)
from app.services.trade_action_parser import parse_trade_action
from app.utils.logger import logger

_llm = LLMService()


# ------------------------------------------------------------------
# Phase 2: Context Editing — trim old tool results when over budget
# ------------------------------------------------------------------

def _trim_context(
  conversation: list[dict],
  budget: int,
  findings: list[str],
  llm: LLMService,
) -> list[dict]:
  """Trim old tool results from conversation when over 75% budget.

  Rules:
  - Never trim system prompt (index 0) or original user context (index 1)
  - Never trim the most recent tool result
  - Replace trimmed results with a 1-line summary
  - Inject memory findings after trimming
  - Only activates when total tokens exceed budget
  """
  total_text = "".join(m.get("content", "") for m in conversation)
  total_tokens = llm.estimate_tokens(total_text)

  if total_tokens <= budget:
    return conversation

  # Find tool result messages (user messages that start with "Research tool result")
  tool_result_indices = []
  for i, msg in enumerate(conversation):
    if (
      i > 1  # Skip system + original user
      and msg.get("role") == "user"
      and msg.get("content", "").startswith("Research tool result")
    ):
      tool_result_indices.append(i)

  if len(tool_result_indices) <= 1:
    # Only one tool result (or none) — nothing to trim
    return conversation

  # Trim all but the most recent tool result
  trimmed = list(conversation)
  trimmed_count = 0
  for idx in tool_result_indices[:-1]:  # Keep the last one
    content = trimmed[idx].get("content", "")
    # Extract tool name from "Research tool result (tool_name):"
    tool_name = "unknown"
    if content.startswith("Research tool result ("):
      end = content.find("):")
      if end > 0:
        tool_name = content[len("Research tool result ("):end]

    # Extract a 1-line summary (first line of the JSON result)
    lines = content.split("\n")
    summary_line = lines[1] if len(lines) > 1 else "result truncated"
    if len(summary_line) > 120:
      summary_line = summary_line[:120] + "…"

    trimmed[idx] = {
      "role": "user",
      "content": (
        f"[Trimmed] Previously called {tool_name}: {summary_line}\n"
        f"(Full result removed to save context. "
        f"Use save_finding to preserve important data.)"
      ),
    }
    trimmed_count += 1

  # Inject findings summary if we had to trim
  if findings and trimmed_count > 0:
    findings_text = "\n".join(f"• {f}" for f in findings)
    # Insert findings reminder before the last user message
    last_user_idx = len(trimmed) - 1
    for i in range(len(trimmed) - 1, -1, -1):
      if trimmed[i].get("role") == "user":
        last_user_idx = i
        break
    trimmed.insert(last_user_idx, {
      "role": "user",
      "content": f"SAVED FINDINGS (from your scratchpad):\n{findings_text}",
    })

  if trimmed_count > 0:
    logger.info(
      "[TradingAgent] Context editing: trimmed %d old tool results "
      "(budget=%d tokens)",
      trimmed_count, budget,
    )

  return trimmed


_MAX_RESEARCH_TURNS = 4  # Max tool calls before forcing a decision


_SYSTEM_PROMPT = """\
You are an autonomous stock trading bot analyzing a single ticker.
Your job: decide BUY, SELL, or HOLD based on the data provided.

## DECISION FLOW
1. Review the context provided below.
2. If you need more information, call a research tool (see below).
3. When ready, output your final trading decision as JSON.

## HOW TO CALL A RESEARCH TOOL
To use a research tool, respond with ONLY this JSON:
{{"tool": "<tool_name>", "params": {{<tool_params>}}}}

You will receive the tool result, then can call another tool or decide.

## HOW TO OUTPUT YOUR FINAL DECISION
When you're ready to decide, respond with ONLY this JSON:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "symbol": "<TICKER>",
  "confidence": 0.0 to 1.0,
  "rationale": "1-3 sentence explanation",
  "risk_notes": "key risks if any",
  "risk_level": "LOW" | "MED" | "HIGH",
  "time_horizon": "INTRADAY" | "SWING" | "POSITION"
}}

RULES:
- Be decisive. If unsure, output HOLD with low confidence.
- Your rationale must reference at least one data point from the context.
- You may ONLY cite numbers and facts explicitly present in the context below.
- Do NOT use outside knowledge or training data for price targets or fundamentals.
- If the context does not support a clear trade, you MUST output HOLD.
- Do NOT suggest further research after your decision. Make a call now.
- You have up to {max_tools} research tool calls available. Use them wisely.

RISK OVERRIDE RULES (mandatory — these override all other reasoning):
- If QUANT VERDICT is SELL, you MUST output HOLD or SELL. Never BUY against a SELL verdict.
- If RISK FLAGS include "bankruptcy_risk_high", confidence MUST be below 0.50.
- If RISK FLAGS include "drawdown_exceeds_20pct" AND "negative_sortino", output SELL.
- If QUANT VERDICT conviction < 35%, you MUST output HOLD or SELL.
- If RISK FLAGS include "piotroski_weak", confidence MUST be below 0.60.

{research_tools}
"""


class TradingAgent:
  """Multi-turn LLM agent per ticker → TradeAction.

  The agent can optionally call research tools before deciding.
  """

  async def decide(
    self,
    context: dict,
    bot_id: str = "default",
  ) -> tuple[TradeAction, str]:
    """Analyze a single ticker and return a trading decision.

    The agent runs a multi-turn loop:
      1. LLM sees context + tool descriptions
      2. LLM outputs either a tool call or a final TradeAction
      3. If tool call → execute tool, feed result back, repeat
      4. If TradeAction → parse and return

    Args:
        context: Dict with symbol, price, technicals, dossier, portfolio, etc.
        bot_id: Bot identifier

    Returns:
        (TradeAction, raw_llm_text) tuple
    """
    symbol = context.get("symbol", "UNKNOWN")
    user_prompt = self._build_prompt(context)

    # Build system prompt with compact search_tools meta-tool
    # (~100 tokens vs ~800 for full tool descriptions)
    system_prompt = _SYSTEM_PROMPT.format(
      max_tools=_MAX_RESEARCH_TURNS,
      research_tools=SEARCH_TOOL_DESCRIPTION,
    )

    # ── Context window budget guard ─────────────────────────
    from app.config import settings

    max_ctx = getattr(settings, "LLM_CONTEXT_SIZE", 32768)
    budget = int(max_ctx * 0.75)  # Leave 25% for LLM + tool results
    total_tokens = _llm.estimate_tokens(system_prompt + user_prompt)

    if total_tokens > budget:
      news = context.get("news_summary", "")
      if news:
        overshoot = total_tokens - budget
        chars_to_cut = overshoot * 4
        trimmed_news = news[: max(0, len(news) - chars_to_cut)]
        if trimmed_news != news:
          context["news_summary"] = trimmed_news + "\n[...truncated for context budget]"
          user_prompt = self._build_prompt(context)
          logger.warning(
            "[TradingAgent] Context budget guard: trimmed news for %s "
            "(%d tokens → ~%d tokens, budget=%d)",
            symbol,
            total_tokens,
            _llm.estimate_tokens(system_prompt + user_prompt),
            budget,
          )

    logger.info("[TradingAgent] Analyzing %s (multi-turn, max %d tools)...", symbol, _MAX_RESEARCH_TURNS)

    # ── Multi-turn loop ─────────────────────────────────────
    conversation: list[dict] = [
      {"role": "system", "content": system_prompt},
      {"role": "user", "content": user_prompt},
    ]

    tools_used: list[str] = []
    findings: list[str] = []  # Memory scratchpad for save_finding
    final_raw = ""

    for turn in range(_MAX_RESEARCH_TURNS + 1):  # +1 for final decision turn
      raw_text = await _llm.chat(
        messages=conversation,
        response_format="json",
        temperature=0.2,
        audit_ticker=symbol,
        audit_step=f"trading_decision_turn_{turn}",
      )

      logger.info(
        "[TradingAgent] Turn %d/%d for %s: got %d chars",
        turn + 1, _MAX_RESEARCH_TURNS + 1, symbol, len(raw_text),
      )

      # Try to parse as tool call or trade action
      cleaned = LLMService.clean_json_response(raw_text)
      try:
        parsed = json.loads(cleaned)
      except (json.JSONDecodeError, ValueError):
        logger.warning(
          "[TradingAgent] Bad JSON from LLM (turn %d): %s",
          turn, raw_text[:200],
        )
        # Ask LLM to fix
        conversation.append({"role": "assistant", "content": raw_text})
        conversation.append({
          "role": "user",
          "content": (
            "ERROR: Invalid JSON. Respond with ONLY valid JSON — either a "
            "research tool call or your final trading decision. No prose."
          ),
        })
        continue

      # ── Check if it's a tool call ──
      if "tool" in parsed and parsed.get("tool") in RESEARCH_TOOL_NAMES:
        tool_name = parsed["tool"]
        tool_params = parsed.get("params", {})

        # Safety: don't allow more than MAX_RESEARCH_TURNS tool calls
        if len(tools_used) >= _MAX_RESEARCH_TURNS:
          logger.info(
            "[TradingAgent] Max research turns reached — forcing decision for %s",
            symbol,
          )
          conversation.append({"role": "assistant", "content": raw_text})
          conversation.append({
            "role": "user",
            "content": (
              "You have used all your research tool calls. "
              "You MUST now output your final trading decision as JSON. "
              "No more tool calls allowed."
            ),
          })
          continue

        # Execute the research tool
        logger.info(
          "[TradingAgent] %s calling tool: %s(%s)",
          symbol, tool_name, json.dumps(tool_params)[:100],
        )
        try:
          tool_func = TOOL_REGISTRY[tool_name]
          tool_result = await tool_func(tool_params)
        except Exception as exc:
          logger.warning(
            "[TradingAgent] Tool %s failed: %s", tool_name, exc,
          )
          tool_result = {"error": f"Tool {tool_name} failed: {exc}"}

        # ── Memory tool: intercept save_finding ──
        if tool_name == "save_finding" and tool_result.get("status") == "saved":
          note = tool_result.get("note", "")
          findings.append(note)
          logger.info("[TradingAgent] %s: saved finding: %s", symbol, note[:80])
          tool_result = {"status": "saved", "findings_count": len(findings)}

        # ── Memory tool: inject findings on recall ──
        if tool_name == "recall_findings":
          tool_result = {
            "status": "recalled",
            "findings_count": len(findings),
            "findings": findings if findings else ["No findings saved yet."],
          }

        tools_used.append(tool_name)

        # Feed tool result back to LLM
        result_text = json.dumps(tool_result, indent=2, default=str)
        # Cap tool result size to avoid blowing context
        if len(result_text) > 3000:
          result_text = result_text[:3000] + "\n[...truncated]"

        # ── Phase 2: Context editing — trim old tool results ──
        conversation = _trim_context(
          conversation, budget, findings, _llm,
        )

        conversation.append({"role": "assistant", "content": raw_text})
        conversation.append({
          "role": "user",
          "content": (
            f"Research tool result ({tool_name}):\n{result_text}\n\n"
            f"Tools remaining: {_MAX_RESEARCH_TURNS - len(tools_used)}. "
            f"Call another tool or output your final trading decision."
          ),
        })

        logger.info(
          "[TradingAgent] %s: tool %s returned %d chars (%d/%d tools used)",
          symbol, tool_name, len(result_text),
          len(tools_used), _MAX_RESEARCH_TURNS,
        )
        continue

      # ── It's a trading decision ──
      if "action" in parsed and parsed.get("action") in ("BUY", "SELL", "HOLD"):
        final_raw = raw_text
        if tools_used:
          logger.info(
            "[TradingAgent] %s decided %s after %d research tool calls: %s",
            symbol, parsed["action"], len(tools_used),
            ", ".join(tools_used),
          )
        else:
          logger.info(
            "[TradingAgent] %s decided %s (no research tools used)",
            symbol, parsed["action"],
          )
        break

      # ── Unknown response — might be a tool call with wrong key ──
      # Check for common LLM mistakes (e.g. "action" used as tool name)
      if "action" in parsed and parsed.get("action") in RESEARCH_TOOL_NAMES:
        # LLM used portfolio strategist format {"action": "tool_name"}
        tool_name = parsed["action"]
        tool_params = parsed.get("params", {})
        if len(tools_used) < _MAX_RESEARCH_TURNS:
          logger.info(
            "[TradingAgent] %s: LLM used 'action' key for tool call, adapting: %s",
            symbol, tool_name,
          )
          try:
            tool_func = TOOL_REGISTRY[tool_name]
            tool_result = await tool_func(tool_params)
          except Exception as exc:
            tool_result = {"error": f"Tool {tool_name} failed: {exc}"}

          tools_used.append(tool_name)
          result_text = json.dumps(tool_result, indent=2, default=str)
          if len(result_text) > 3000:
            result_text = result_text[:3000] + "\n[...truncated]"

          conversation.append({"role": "assistant", "content": raw_text})
          conversation.append({
            "role": "user",
            "content": (
              f"Research tool result ({tool_name}):\n{result_text}\n\n"
              f"Tools remaining: {_MAX_RESEARCH_TURNS - len(tools_used)}. "
              f"Now output your final trading decision as JSON with "
              f"\"action\": \"BUY\"/\"SELL\"/\"HOLD\"."
            ),
          })
          continue

      # Unrecognized response — ask for clarification
      logger.warning(
        "[TradingAgent] Unrecognized response for %s (turn %d): %s",
        symbol, turn, cleaned[:200],
      )
      conversation.append({"role": "assistant", "content": raw_text})
      conversation.append({
        "role": "user",
        "content": (
          "I did not understand your response. You must output ONLY "
          "one of these two JSON formats:\n"
          "1. Research tool: {\"tool\": \"<name>\", \"params\": {...}}\n"
          "2. Decision: {\"action\": \"BUY\"|\"SELL\"|\"HOLD\", \"symbol\": \"...\", ...}\n"
          "Respond with valid JSON only."
        ),
      })
    else:
      # Loop exhausted without a final decision — use last raw text
      if not final_raw:
        final_raw = raw_text
        logger.warning(
          "[TradingAgent] Max turns exhausted for %s — using last response",
          symbol,
        )

    # ── Parse the final decision ──
    action = await parse_trade_action(final_raw, bot_id, symbol)

    logger.info(
      "[TradingAgent] Decision for %s: %s (confidence=%.2f, tools_used=%s)",
      symbol,
      action.action,
      action.confidence,
      tools_used or "none",
    )

    return action, final_raw

  @staticmethod
  def _build_prompt(ctx: dict) -> str:
    """Build the user prompt from context data."""
    symbol = ctx.get("symbol", "?")
    price = ctx.get("last_price", 0)
    change = ctx.get("today_change_pct", 0)
    volume = ctx.get("volume", 0)
    avg_vol = ctx.get("avg_volume", 0)

    parts = [
      f"TICKER: {symbol}",
      f"PRICE: ${price:.2f}  |  TODAY: {change:+.2f}%",
      f"VOLUME: {volume:,.0f}  |  AVG VOLUME: {avg_vol:,.0f}",
    ]

    # Technical summary
    tech = ctx.get("technical_summary", "")
    if tech:
      parts.append(f"\nTECHNICAL ANALYSIS:\n{tech}")

    # Quant scorecard summary
    quant = ctx.get("quant_summary", "")
    if quant:
      parts.append(f"\nQUANT SIGNALS:\n{quant}")

    # News
    news = ctx.get("news_summary", "")
    if news:
      parts.append(f"\nNEWS DIGEST:\n{news}")

    # RAG-retrieved market intelligence
    rag = ctx.get("rag_context", "")
    if rag:
      parts.append(
        f"\nMARKET INTELLIGENCE (retrieved context from recent "
        f"market sources):\n{rag}"
      )

    # Portfolio context
    cash = ctx.get("portfolio_cash", 0)
    pv = ctx.get("portfolio_value", 0)
    max_pct = ctx.get("max_position_pct", 15)
    parts.append(
      f"\nPORTFOLIO: Cash=${cash:,.0f}  |  Total=${pv:,.0f}  |  Max position={max_pct}%"
    )

    # Quant verdict from deep analysis
    conv = ctx.get("dossier_conviction", 0)
    sig = ctx.get("dossier_signal", "UNKNOWN")
    if conv or sig != "UNKNOWN":
      parts.append(f"\nQUANT VERDICT: {sig} (conviction={conv:.0%})")

    # Risk flags from quant scorecard
    flags = ctx.get("quant_flags", [])
    if flags:
      parts.append(f"RISK FLAGS: {', '.join(flags)}")

    # Existing position
    pos = ctx.get("existing_position", {})
    if pos and pos.get("qty", 0) > 0:
      parts.append(
        f"EXISTING POSITION: {pos['qty']} shares @ "
        f"${pos.get('avg_entry', 0):.2f}  |  "
        f"P&L: ${pos.get('unrealized_pnl', 0):.2f}"
      )
    else:
      parts.append("EXISTING POSITION: None")

    return "\n".join(parts)
