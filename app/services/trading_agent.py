"""Trading Agent — single LLM call per ticker → TradeAction JSON.

Replaces the multi-turn PortfolioStrategist loop with ONE compact prompt
per ticker. The LLM's job is narrow: interpret precomputed data and output
BUY/SELL/HOLD with a rationale. All indicators are computed locally.
"""

from __future__ import annotations

from app.models.trade_action import TRADE_ACTION_SCHEMA, TradeAction
from app.services.llm_service import LLMService
from app.services.trade_action_parser import parse_trade_action
from app.utils.logger import logger

_llm = LLMService()

_SYSTEM_PROMPT = """\
You are an autonomous stock trading bot analyzing a single ticker.
Your job: decide BUY, SELL, or HOLD based on the data provided.

RULES:
- Be decisive. If unsure, output HOLD with low confidence.
- Your rationale must reference at least one data point.
- Do NOT invent numbers. Use only the data given to you.
- Do NOT suggest further research. Make a call now.

OUTPUT FORMAT (JSON only, no markdown):
{
  "action": "BUY" | "SELL" | "HOLD",
  "symbol": "<TICKER>",
  "confidence": 0.0 to 1.0,
  "rationale": "1-3 sentence explanation",
  "risk_notes": "key risks if any",
  "risk_level": "LOW" | "MED" | "HIGH",
  "time_horizon": "INTRADAY" | "SWING" | "POSITION"
}
"""


class TradingAgent:
    """One LLM call per ticker → TradeAction."""

    async def decide(
        self,
        context: dict,
        bot_id: str = "default",
    ) -> tuple[TradeAction, str]:
        """Analyze a single ticker and return a trading decision.

        Args:
            context: Dict with keys:
                - symbol, last_price, today_change_pct, volume, avg_volume
                - technical_summary (precomputed text from DataDistiller)
                - quant_summary (precomputed from QuantScorecard)
                - news_summary (2-3 sentence digest)
                - portfolio_cash, portfolio_value, max_position_pct
                - existing_position (qty, avg_entry, unrealized_pnl)
            bot_id: Bot identifier

        Returns:
            (TradeAction, raw_llm_text) tuple
        """
        symbol = context.get("symbol", "UNKNOWN")
        user_prompt = self._build_prompt(context)

        logger.info("[TradingAgent] Analyzing %s...", symbol)

        raw_text = await _llm.chat(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            response_format="json",
            schema=TRADE_ACTION_SCHEMA,
            temperature=0.2,
            audit_ticker=symbol,
            audit_step="trading_decision",
        )

        logger.info(
            "[TradingAgent] Got %d chars for %s, parsing...",
            len(raw_text),
            symbol,
        )

        action = await parse_trade_action(raw_text, bot_id, symbol)

        logger.info(
            "[TradingAgent] Decision for %s: %s (confidence=%.2f)",
            symbol,
            action.action,
            action.confidence,
        )

        return action, raw_text

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

        # Portfolio context
        cash = ctx.get("portfolio_cash", 0)
        pv = ctx.get("portfolio_value", 0)
        max_pct = ctx.get("max_position_pct", 15)
        parts.append(
            f"\nPORTFOLIO: Cash=${cash:,.0f}  |  "
            f"Total=${pv:,.0f}  |  "
            f"Max position={max_pct}%"
        )

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
