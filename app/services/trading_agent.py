"""Trading Agent — multi-turn LLM agent per ticker → TradeAction JSON.

The LLM starts with precomputed context (price, technicals, dossier) and
can optionally call research tools to dig deeper before making its final
BUY/SELL/HOLD decision.

Loop: up to MAX_RESEARCH_TURNS of tool calls, then a final decision.
If the LLM outputs a TradeAction on the first turn, no tools are called.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from app.models.trade_action import TradeAction
from app.services.llm_service import LLMService
from app.services.research_tools import (
    RESEARCH_TOOL_NAMES,
    SEARCH_TOOL_DESCRIPTION,
    TOOL_REGISTRY,
)
from app.services.trade_action_parser import parse_trade_action
from app.utils.logger import logger

_llm = LLMService()


def _log_tool_usage(
    symbol: str,
    bot_id: str,
    tools_used: list[str],
    turns_taken: int,
) -> None:
    """Log tool usage to pipeline_events for diagnostics."""
    try:
        from app.database import get_db
        conn = get_db()
        event_type = "tool_usage" if tools_used else "no_tools_used"
        conn.execute(
            "INSERT INTO pipeline_events "
            "(bot_id, event_type, event_data, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                bot_id,
                f"trading_agent:{event_type}",
                json.dumps({
                    "symbol": symbol,
                    "tools_used": tools_used,
                    "tools_count": len(tools_used),
                    "turns_taken": turns_taken,
                }, default=str),
                datetime.now().isoformat(),
            ],
        )
    except Exception as exc:
        logger.debug("[TradingAgent] Failed to log tool event: %s", exc)


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
                tool_name = content[len("Research tool result (") : end]

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
        trimmed.insert(
            last_user_idx,
            {
                "role": "user",
                "content": f"SAVED FINDINGS (from your scratchpad):\n{findings_text}",
            },
        )

    if trimmed_count > 0:
        logger.info(
            "[TradingAgent] Context editing: trimmed %d old tool results (budget=%d tokens)",
            trimmed_count,
            budget,
        )

    return trimmed


_MAX_RESEARCH_TURNS = 4  # Max tool calls before forcing a decision


_SYSTEM_PROMPT = """\
You are an elite portfolio manager and capital allocator. You are not just making isolated predictions; you are managing a real pool of capital.
Your job: decide BUY, SELL, or HOLD based on the data provided, while strictly managing your remaining cash reserves.

## DECISION FLOW
1. Review the context provided below.
2. Call at least 1 research tool to verify your analysis BEFORE deciding.
   Skipping research is ONLY acceptable when data is already comprehensive.
3. When ready, output your final trading decision as JSON.

## HOW TO CALL A RESEARCH TOOL
To use a research tool, respond with ONLY this JSON:
{{"tool": "<tool_name>", "params": {{<tool_params>}}}}

You will receive the tool result, then can call another tool or decide.
You have {max_tools} research tool calls available.

## HOW TO OUTPUT YOUR FINAL DECISION
When you're ready to decide, respond with ONLY this JSON (no other text):
{{
  "action": "BUY",
  "symbol": "AAPL",
  "confidence": 0.82,
  "rationale": "THESIS: reason | KEY_DATA: data points | DIFFERENTIATOR: unique angle | CONFIDENCE_CALC: exact reason for this score",
  "risk_notes": "risks here",
  "risk_level": "MED",
  "time_horizon": "SWING"
}}

## STRICT FIELD RULES (you MUST follow these exactly):
- "action" → MUST be exactly one of: "BUY", "SELL", "HOLD" (uppercase, no other words)
- "symbol" → MUST be a valid US stock ticker in uppercase (e.g. "AAPL", "NVDA")
- "confidence" → MUST be a decimal between 0.0 and 1.0. You MUST use granular precision (e.g. 0.68, 0.73, 0.82). Do NOT simply bin to 0.75 or 0.85.
- "rationale" → MUST be a structured string with FOUR parts separated by " | ":
    1. THESIS: One sentence stating your core reasoning (cite a specific number)
    2. KEY_DATA: The 2-3 most important data points driving this decision
    3. DIFFERENTIATOR or EXIT_TRIGGER: If BUY/HOLD, what makes this ticker unique? If SELL, what exactly triggered the exit (e.g., stop-loss hit, capital reallocation, thesis broken)?
    4. CONFIDENCE_CALC: Logical justification for the EXACT confidence score chosen (e.g., "0.82 because baseline 0.60 + 0.15 for Sharpe > 2 + 0.07 for momentum").
    Example BUY: "THESIS: GEV's 85% conviction with +320% 12m return is exceptional | KEY_DATA: Sharpe 2.4, RSI 62 | DIFFERENTIATOR: Only position with >2.0 Sharpe | CONFIDENCE_CALC: 0.82 because base 0.60 + 0.22 for Sharpe > 2."
    Example SELL: "THESIS: Core momentum thesis is broken | KEY_DATA: Price dropped 8%, MACD bearish cross | EXIT_TRIGGER: Stop-loss parameter breached | CONFIDENCE_CALC: 0.90 because technical damage is severe."
- "risk_notes" → MUST be a string describing key risks with specific numbers
- "risk_level" → MUST be exactly one of: "LOW", "MED", "HIGH" (uppercase)
- "time_horizon" → MUST be exactly one of: "INTRADAY", "SWING", "POSITION" (uppercase)

## RATIONALE QUALITY RULES (mandatory — rationales that violate these are REJECTED):
- Do NOT start rationale with "Quant signals indicate" or "Quant conviction is".
- Your THESIS must cite at least ONE specific number from technical analysis (RSI, MACD, Sharpe, Sortino, momentum %, etc.)
- Your KEY_DATA must list 2-3 DIFFERENT data points — not just conviction %.
- Your DIFFERENTIATOR must explain why THIS ticker stands out vs the others in the current portfolio.
- Your CONFIDENCE_CALC must prove why you picked the exact granular decimal you did.
- Confidence must reflect genuine signal strength: 0.70, 0.73, 0.82, etc. — NOT always 0.75 or 0.85.
- Each ticker's rationale MUST be unique. Copying the same wording across tickers is prohibited.

DECISION RULES:
- Be DECISIVE, but be SMART. Your job is to find the best risk-adjusted trades, not to buy everything you see.
- You may ONLY cite numbers and facts explicitly present in the context below.
- Do NOT use outside knowledge or training data for price targets or fundamentals.

## CAPITAL PRESERVATION & RISK RULES:
- Check your PORTFOLIO Cash, Total Value, and Sector Breakdown. 
- You MUST preserve "dry powder" (cash). Do not spend all your remaining cash on mediocre setups.
- If your cash balance is low relative to the max position size, your threshold for a BUY must be EXTREMELY HIGH.
- Treat every BUY as an allocation of scarce capital. If the conviction isn't exceptional, use HOLD to save the cash for a better day.
- SECTOR CONCENTRATION: Review your current sector breakdown before buying. If you are already heavily overweight in this stock's sector, you are taking on higher correlation risk. You are allowed to take this risk if the conviction is incredible, but you must factor the lack of diversification into your decision and confidence score.

WHEN TO BUY (prefer BUY when these conditions are met AND you have the cash):
- QUANT VERDICT conviction >= 65% → strong BUY signal, favor action (if cash permits).
- Positive momentum (price up 1w/1m/3m) with rising volume → BUY.
- Sharpe > 1.5 and no major risk flags → BUY.
- Use HOLD only when signals are genuinely mixed or contradictory.

WHEN TO SELL:
- You must have a clear EXIT_TRIGGER. Do NOT sell just because you are bored or want to "secure profits" early.
- Valid EXIT_TRIGGERS:
  1. Thesis Broken: The original reason you bought it (e.g., strong momentum, high conviction) is gone.
  2. Stop-Loss / Take-Profit Hit: The price has hit a predetermined risk management level.
  3. Reallocation: You desperately need the capital for a significantly better opportunity (conviction > 85%).
- QUANT VERDICT is SELL or conviction < 30%.
- CRITICAL: Do NOT output SELL if EXISTING POSITION is "None".
  You can only SELL stocks you actually hold. If you don't hold it, output HOLD.

WHEN TO HOLD:
- Signals are genuinely mixed (some bullish, some bearish).
- Data quality is poor (missing fundamentals, no news).
- Do NOT default to HOLD out of caution if the quant signals are clear.

RISK OVERRIDE RULES (mandatory — these override all other reasoning):
- If QUANT VERDICT is SELL, you MUST output HOLD or SELL. Never BUY against a SELL verdict.
- If RISK FLAGS include "bankruptcy_risk_high", confidence MUST be below 0.50.
- If RISK FLAGS include "drawdown_exceeds_20pct" AND "negative_sortino", output SELL.
- If QUANT VERDICT conviction < 25%, you MUST output HOLD or SELL.
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
    ) -> tuple[TradeAction, str, dict]:
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
            (TradeAction, raw_llm_text, llm_meta) tuple.
            llm_meta contains: system_prompt, user_prompt, turns, tools_used, duration_s
        """
        symbol = context.get("symbol", "UNKNOWN")
        user_prompt = self._build_prompt(context)
        _decide_t0 = time.time()

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

        logger.info(
            "[TradingAgent] Analyzing %s (multi-turn, max %d tools)...", symbol, _MAX_RESEARCH_TURNS
        )

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
                turn + 1,
                _MAX_RESEARCH_TURNS + 1,
                symbol,
                len(raw_text),
            )

            # Try to parse as tool call or trade action
            cleaned = LLMService.clean_json_response(raw_text)
            try:
                parsed = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "[TradingAgent] Bad JSON from LLM (turn %d): %s",
                    turn,
                    raw_text[:200],
                )
                # Ask LLM to fix
                conversation.append({"role": "assistant", "content": raw_text})
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "ERROR: Invalid JSON. Respond with ONLY valid JSON — either a "
                            "research tool call or your final trading decision. No prose."
                        ),
                    }
                )
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
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "You have used all your research tool calls. "
                                "You MUST now output your final trading decision as JSON. "
                                "No more tool calls allowed."
                            ),
                        }
                    )
                    continue

                # Execute the research tool
                logger.info(
                    "[TradingAgent] %s calling tool: %s(%s)",
                    symbol,
                    tool_name,
                    json.dumps(tool_params)[:100],
                )
                try:
                    tool_func = TOOL_REGISTRY[tool_name]
                    tool_result = await tool_func(tool_params)
                except Exception as exc:
                    logger.warning(
                        "[TradingAgent] Tool %s failed: %s",
                        tool_name,
                        exc,
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
                    conversation,
                    budget,
                    findings,
                    _llm,
                )

                conversation.append({"role": "assistant", "content": raw_text})
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            f"Research tool result ({tool_name}):\n{result_text}\n\n"
                            f"Tools remaining: {_MAX_RESEARCH_TURNS - len(tools_used)}. "
                            f"Call another tool or output your final trading decision."
                        ),
                    }
                )

                logger.info(
                    "[TradingAgent] %s: tool %s returned %d chars (%d/%d tools used)",
                    symbol,
                    tool_name,
                    len(result_text),
                    len(tools_used),
                    _MAX_RESEARCH_TURNS,
                )
                continue

            # ── It's a trading decision ──
            if "action" in parsed and parsed.get("action", "").upper() in ("BUY", "SELL", "HOLD"):
                final_raw = raw_text

                # Log tool usage to DB for diagnostics
                _log_tool_usage(symbol, bot_id, tools_used, turn + 1)

                if tools_used:
                    logger.info(
                        "[TradingAgent] %s decided %s after %d research tool calls: %s",
                        symbol,
                        parsed["action"],
                        len(tools_used),
                        ", ".join(tools_used),
                    )
                else:
                    logger.info(
                        "[TradingAgent] %s decided %s (no research tools used)",
                        symbol,
                        parsed["action"],
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
                        symbol,
                        tool_name,
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
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                f"Research tool result ({tool_name}):\n{result_text}\n\n"
                                f"Tools remaining: {_MAX_RESEARCH_TURNS - len(tools_used)}. "
                                f"Now output your final trading decision as JSON with "
                                f'"action": "BUY"/"SELL"/"HOLD".'
                            ),
                        }
                    )
                    continue

            # Unrecognized response — ask for clarification
            logger.warning(
                "[TradingAgent] Unrecognized response for %s (turn %d): %s",
                symbol,
                turn,
                cleaned[:200],
            )
            conversation.append({"role": "assistant", "content": raw_text})
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        "I did not understand your response. You must output ONLY "
                        "one of these two JSON formats:\n"
                        '1. Research tool: {"tool": "<name>", "params": {...}}\n'
                        '2. Decision: {"action": "BUY"|"SELL"|"HOLD", "symbol": "...", ...}\n'
                        "Respond with valid JSON only."
                    ),
                }
            )
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

        llm_meta = {
            "system_prompt": system_prompt[:2000],
            "user_prompt": user_prompt[:2000],
            "raw_output": final_raw[:2000],
            "turns": turn + 1 if final_raw else 0,
            "tools_used": tools_used,
            "duration_s": round(time.time() - _decide_t0, 2),
            "model": _llm.model,
        }

        return action, final_raw, llm_meta

    @staticmethod
    def _build_prompt(ctx: dict) -> str:
        """Build the user prompt from context data."""
        symbol = ctx.get("symbol", "?")
        target_sector = ctx.get("target_sector", "Unknown")
        price = ctx.get("last_price", 0)
        change = ctx.get("today_change_pct", 0)
        volume = ctx.get("volume", 0)
        avg_vol = ctx.get("avg_volume", 0)

        parts = [
            f"TICKER: {symbol} (Sector: {target_sector})",
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

        # Analysis digest (chart, fundamentals, risk — from dossier)
        news = ctx.get("news_summary", "")
        if news:
            parts.append(f"\nANALYSIS DIGEST:\n{news}")

        # RAG-retrieved market intelligence
        rag = ctx.get("rag_context", "")
        if rag:
            parts.append(
                f"\nMARKET INTELLIGENCE (retrieved context from recent market sources):\n{rag}"
            )

        # Delta since last decision — what changed since the bot last looked
        delta = ctx.get("delta_since_last", "")
        if delta:
            parts.append(f"\nSINCE LAST DECISION:\n{delta}")

        # YouTube catalyst intelligence — fresh analyst perspectives
        yt_intel = ctx.get("youtube_intel", "")
        if yt_intel:
            parts.append(f"\nCATALYST INTELLIGENCE (from recent YouTube analysis):\n{yt_intel}")

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

        # Current holdings summary — so the LLM knows what it already owns
        # and can explain why THIS ticker adds unique value to the portfolio
        all_positions = ctx.get("all_positions", [])
        if all_positions:
            held_summaries = []
            for hp in all_positions[:10]:  # Cap at 10 to save context
                hp_ticker = hp.get("ticker", "?")
                hp_qty = hp.get("qty", 0)
                hp_entry = hp.get("avg_entry_price", 0)
                held_summaries.append(f"{hp_ticker}({hp_qty}@${hp_entry:.0f})")
            parts.append(
                f"\nCURRENT HOLDINGS: {', '.join(held_summaries)}"
            )
            
            # Sector breakdown
            sector_breakdown = ctx.get("sector_breakdown", {})
            if sector_breakdown:
                breakdown_parts = [f"{s}: ${v:,.0f}" for s, v in sorted(sector_breakdown.items(), key=lambda x: x[1], reverse=True)]
                parts.append(f"SECTOR EXPOSURE: {', '.join(breakdown_parts)}")

            parts.append(
                "Your rationale MUST explain why this ticker adds UNIQUE value "
                "beyond what is already held above."
            )
        else:
            parts.append("\nCURRENT HOLDINGS: None (empty portfolio)")

        return "\n".join(parts)
