"""Trade Action Parser — parse + validate + auto-repair LLM output → TradeAction.

Flow:
  1. LLMService.clean_json_response() strips fences + extracts JSON
  2. json.loads() → TradeAction.model_validate()
  3. If validation fails → LLM repair prompt (low temperature) → re-parse
  4. Post-parse: run symbol through FilterPipeline
  5. If symbol fails → force HOLD + log rejection
  6. All parse/repair events logged to pipeline_events for diagnostics
"""

from __future__ import annotations

import json
from datetime import datetime

from app.models.trade_action import TradeAction
from app.services.llm_service import LLMService
from app.services.symbol_filter import _log_rejection, get_filter_pipeline
from app.utils.logger import logger

try:
    from json_repair import repair_json as _repair_json
except ImportError:
    _repair_json = None  # Graceful fallback if not installed

_llm = LLMService()

_REPAIR_SYSTEM = (
    "You are a JSON repair assistant. Fix the broken JSON below so it is "
    "valid JSON matching the schema. Return ONLY the fixed JSON — "
    "no thinking, no explanations, no markdown fences, just pure JSON."
)

_REPAIR_SCHEMA = (
    "Required schema:\n"
    '{"action": "BUY"|"SELL"|"HOLD", "symbol": "<TICKER>", '
    '"confidence": 0.0-1.0, "rationale": "<string>", '
    '"risk_notes": "<string>", "risk_level": "LOW"|"MED"|"HIGH", '
    '"time_horizon": "INTRADAY"|"SWING"|"POSITION"}\n'
    "All fields except risk_notes, risk_level, time_horizon are required.\n\n"
    "Example of valid output:\n"
    '{"action": "HOLD", "symbol": "AAPL", "confidence": 0.45, '
    '"rationale": "Mixed signals on RSI and MACD", '
    '"risk_notes": "Earnings next week", "risk_level": "MED", '
    '"time_horizon": "SWING"}\n'
)


def _log_parse_event(
    symbol: str,
    bot_id: str,
    event_type: str,
    details: dict,
) -> None:
    """Log a parse/repair event to pipeline_events for diagnostics."""
    try:
        from app.database import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO pipeline_events "
            "(bot_id, event_type, event_data, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                bot_id,
                f"trade_parse:{event_type}",
                json.dumps({"symbol": symbol, **details}, default=str),
                datetime.now().isoformat(),
            ],
        )
    except Exception as exc:
        logger.debug("[TradeActionParser] Failed to log event: %s", exc)


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
    action, error = _try_parse(cleaned, bot_id, symbol)
    if action is not None:
        _log_parse_event(symbol, bot_id, "parse_ok", {
            "action": action.action,
            "confidence": action.confidence,
            "attempt": 0,
        })
        return _post_validate(action, bot_id)

    # ── Log the broken JSON and error ─────────────────────────────
    logger.warning(
        "[TradeActionParser] ❌ Parse FAILED for %s — error: %s | raw (first 500 chars): %s",
        symbol,
        error,
        cleaned[:500],
    )
    _log_parse_event(symbol, bot_id, "parse_failed", {
        "error": str(error),
        "raw_json_preview": cleaned[:500],
    })

    # ── Step 2.5: Try json_repair (fast, no LLM round-trip) ───────
    if _repair_json is not None and cleaned.strip():
        try:
            repaired_str = _repair_json(cleaned, return_objects=False)
            if isinstance(repaired_str, str) and repaired_str.strip():
                action, repair_err = _try_parse(repaired_str, bot_id, symbol)
                if action is not None:
                    logger.info(
                        "[TradeActionParser] ✅ json_repair fixed %s (no LLM needed)",
                        symbol,
                    )
                    _log_parse_event(symbol, bot_id, "json_repair_ok", {
                        "action": action.action,
                        "confidence": action.confidence,
                    })
                    return _post_validate(action, bot_id)
        except Exception as exc:
            logger.debug("[TradeActionParser] json_repair failed for %s: %s", symbol, exc)

    # ── Step 3: LLM Repair loop ──────────────────────────────────
    # Skip repair if the original response was empty (model timeout).
    # Sending another request to the same timed-out model will just
    # waste another 180s with the same result.
    if not cleaned.strip():
        logger.warning(
            "[TradeActionParser] ⚠️ Skipping repair for %s — "
            "original response was empty (model likely timed out)",
            symbol,
        )
        _log_parse_event(symbol, bot_id, "skip_repair_empty", {
            "reason": "empty_response_timeout",
        })
        max_repairs = 0  # Skip the repair loop below

    for attempt in range(max_repairs):
        logger.warning(
            "[TradeActionParser] 🔧 Repair attempt %d/%d for %s — sending to LLM for fixing",
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
        action, repair_error = _try_parse(repaired_cleaned, bot_id, symbol)

        if action is not None:
            logger.info(
                "[TradeActionParser] ✅ Repair SUCCEEDED for %s on attempt %d — "
                "action=%s confidence=%.2f",
                symbol,
                attempt + 1,
                action.action,
                action.confidence,
            )
            _log_parse_event(symbol, bot_id, "repair_succeeded", {
                "attempt": attempt + 1,
                "action": action.action,
                "confidence": action.confidence,
                "original_error": str(error),
            })
            return _post_validate(action, bot_id)

        logger.warning(
            "[TradeActionParser] ❌ Repair attempt %d FAILED for %s — "
            "error: %s | repaired (first 500 chars): %s",
            attempt + 1,
            symbol,
            repair_error,
            repaired_cleaned[:500],
        )
        _log_parse_event(symbol, bot_id, "repair_failed", {
            "attempt": attempt + 1,
            "error": str(repair_error),
            "repaired_json_preview": repaired_cleaned[:500],
        })

    # ── Step 4: Give up → forced HOLD ─────────────────────────────
    logger.error(
        "[TradeActionParser] 🚫 All %d repair attempts failed for %s — "
        "forcing HOLD. Original error: %s",
        max_repairs,
        symbol,
        error,
    )
    _log_parse_event(symbol, bot_id, "forced_hold", {
        "max_repairs_exhausted": max_repairs,
        "original_error": str(error),
        "raw_json_preview": cleaned[:500],
    })

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
) -> tuple[TradeAction | None, str | None]:
    """Attempt to parse cleaned JSON into a TradeAction.

    Returns:
        (TradeAction, None) on success
        (None, error_message) on failure
    """
    # Try parsing with strict=False to handle control chars (newlines, tabs)
    # inside JSON string values — LLMs frequently emit these.
    try:
        data = json.loads(cleaned_json, strict=False)
    except (json.JSONDecodeError, TypeError):
        # Fallback: strip all C0 control characters except structural whitespace
        import re as _re
        sanitized = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', cleaned_json)
        try:
            data = json.loads(sanitized, strict=False)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, f"json.loads() failed: {exc}"

    if not isinstance(data, dict):
        return None, f"Expected dict, got {type(data).__name__}: {str(data)[:200]}"

    # Backfill bot_id and symbol if LLM didn't include them
    data.setdefault("bot_id", bot_id)
    data.setdefault("symbol", expected_symbol.upper())

    # Normalize symbol
    if isinstance(data.get("symbol"), str):
        data["symbol"] = data["symbol"].strip().upper().lstrip("$")

    # Normalize action
    if isinstance(data.get("action"), str):
        data["action"] = data["action"].strip().upper()

    # Normalize confidence to float
    conf = data.get("confidence")
    if isinstance(conf, str):
        try:
            data["confidence"] = float(conf)
        except ValueError:
            # Map text to numbers
            conf_map = {"low": 0.3, "medium": 0.5, "high": 0.8, "very high": 0.9}
            data["confidence"] = conf_map.get(conf.lower(), 0.5)

    try:
        return TradeAction.model_validate(data), None
    except Exception as exc:
        # Build a diagnostic message showing what fields are present/missing
        fields_present = list(data.keys())
        fields_values = {
            k: f"{type(v).__name__}={str(v)[:50]}"
            for k, v in data.items()
            if k != "bot_id"
        }
        return None, (
            f"Pydantic validation: {exc} | "
            f"fields_present={fields_present} | "
            f"values={fields_values}"
        )


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
