"""Trade Action Parser — parse + validate + auto-repair LLM output → TradeAction.

Flow:
  1. LLMService.clean_json_response() strips fences + extracts JSON
  2. json.loads() → TradeAction.model_validate()
  3. If validation fails → LLM repair prompt (low temperature) → re-parse
  4. Post-parse: run symbol through FilterPipeline
  5. If symbol fails → force HOLD + log rejection
"""

from __future__ import annotations

import json

from app.models.trade_action import TradeAction
from app.services.llm_service import LLMService
from app.services.symbol_filter import get_filter_pipeline, _log_rejection
from app.utils.logger import logger

_llm = LLMService()

_REPAIR_SYSTEM = (
    "You are a JSON repair assistant. The user will give you a broken JSON "
    "object that was supposed to match a specific schema. Fix it so it is "
    "valid JSON matching the schema exactly. Return ONLY the fixed JSON, "
    "nothing else."
)

_REPAIR_SCHEMA = (
    "Required schema:\n"
    '{"action": "BUY"|"SELL"|"HOLD", "symbol": "<TICKER>", '
    '"confidence": 0.0-1.0, "rationale": "<string>", '
    '"risk_notes": "<string>", "risk_level": "LOW"|"MED"|"HIGH", '
    '"time_horizon": "INTRADAY"|"SWING"|"POSITION"}\n'
    "All fields except risk_notes, risk_level, time_horizon are required.\n"
)


async def parse_trade_action(
    raw_llm_text: str,
    bot_id: str,
    symbol: str,
    *,
    max_repairs: int = 1,
) -> TradeAction:
    """Parse raw LLM text → validated TradeAction.

    Args:
        raw_llm_text: Raw text from LLMService.chat()
        bot_id: The bot that requested this decision
        symbol: The ticker being analyzed (used to backfill if LLM omits it)
        max_repairs: How many LLM repair attempts before giving up

    Returns:
        A validated TradeAction (may be forced to HOLD on failure)
    """
    # ── Step 1: Clean + extract JSON ──────────────────────────────
    cleaned = LLMService.clean_json_response(raw_llm_text)

    # ── Step 2: Try to parse ──────────────────────────────────────
    action = _try_parse(cleaned, bot_id, symbol)
    if action is not None:
        return _post_validate(action, bot_id)

    # ── Step 3: Repair loop ───────────────────────────────────────
    for attempt in range(max_repairs):
        logger.warning(
            "[TradeActionParser] Repair attempt %d/%d for %s",
            attempt + 1,
            max_repairs,
            symbol,
        )
        repaired_text = await _llm.chat(
            system=_REPAIR_SYSTEM,
            user=f"{_REPAIR_SCHEMA}\n\nBroken JSON:\n{cleaned}",
            response_format="json",
            temperature=0.1,
        )
        repaired_cleaned = LLMService.clean_json_response(repaired_text)
        action = _try_parse(repaired_cleaned, bot_id, symbol)
        if action is not None:
            logger.info(
                "[TradeActionParser] Repair succeeded for %s on attempt %d",
                symbol,
                attempt + 1,
            )
            return _post_validate(action, bot_id)

    # ── Step 4: Give up → forced HOLD ─────────────────────────────
    logger.error(
        "[TradeActionParser] All repair attempts failed for %s — forcing HOLD",
        symbol,
    )
    return TradeAction(
        bot_id=bot_id,
        symbol=symbol.upper(),
        action="HOLD",
        confidence=0.0,
        rationale=f"LLM output could not be parsed after {max_repairs} repair attempts",
        risk_notes="parse_failure",
        risk_level="HIGH",
    )


def _try_parse(
    cleaned_json: str,
    bot_id: str,
    expected_symbol: str,
) -> TradeAction | None:
    """Attempt to parse cleaned JSON into a TradeAction. Returns None on failure."""
    try:
        data = json.loads(cleaned_json)
    except (json.JSONDecodeError, TypeError):
        logger.debug("[TradeActionParser] json.loads() failed")
        return None

    if not isinstance(data, dict):
        return None

    # Backfill bot_id and symbol if LLM didn't include them
    data.setdefault("bot_id", bot_id)
    data.setdefault("symbol", expected_symbol.upper())

    # Normalize symbol
    if isinstance(data.get("symbol"), str):
        data["symbol"] = data["symbol"].strip().upper().lstrip("$")

    # Normalize action
    if isinstance(data.get("action"), str):
        data["action"] = data["action"].strip().upper()

    try:
        return TradeAction.model_validate(data)
    except Exception as exc:
        logger.debug("[TradeActionParser] Pydantic validation failed: %s", exc)
        return None


def _post_validate(action: TradeAction, bot_id: str) -> TradeAction:
    """Run symbol through FilterPipeline after parsing.

    If the symbol is rejected, force action to HOLD and log the rejection.
    """
    result = get_filter_pipeline().run(
        action.symbol,
        {"source": "trade_decision", "bot_id": bot_id},
    )
    if not result.passed:
        logger.warning(
            "[TradeActionParser] Symbol %s rejected by filters: %s — forcing HOLD",
            action.symbol,
            result.reason,
        )
        _log_rejection(
            action.symbol,
            result.reason,
            {"source": "trade_decision", "bot_id": bot_id},
        )
        # Return a new action forced to HOLD
        return action.model_copy(
            update={
                "action": "HOLD",
                "rationale": f"Symbol rejected: {result.reason}. Original: {action.rationale}",
                "risk_notes": f"symbol_rejected:{result.reason}",
            },
        )
    return action
