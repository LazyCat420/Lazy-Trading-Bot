# Issue 02 — BUY Bias Guardrails (nemotron-3-nano)

**Severity:** HIGH  
**Root Cause:** nemotron outputs 12+ consecutive BUYs including stocks flagged `bankruptcy_risk_high` and `drawdown_exceeds_20pct`. The system prompt says "reference data" but doesn't enforce hard rules about risk flags.

## Files to Modify

### 1. `app/services/trading_agent.py`

- `_SYSTEM_PROMPT` (line 17): Add explicit risk flag guardrails
- `_build_prompt()` (line 124): Surface risk flags prominently in the prompt (currently only shows quant_summary text, not the individual flags)

### 2. `app/services/autonomous_loop.py`

- `_do_trading()` (line 520): Add post-LLM sanity check — if quant conviction < 0.35 but LLM says BUY > 0.70, log a warning and downgrade to HOLD

## Specific Changes

```python
# trading_agent.py — _SYSTEM_PROMPT additions:
RISK OVERRIDE RULES (mandatory):
- If QUANT VERDICT is SELL, you MUST output HOLD or SELL. Never BUY against a SELL verdict.
- If risk flags include "bankruptcy_risk_high", confidence MUST be below 0.50.
- If risk flags include "drawdown_exceeds_20pct" AND "negative_sortino", output SELL.
- If conviction < 0.35, you MUST output HOLD or SELL.

# trading_agent.py — _build_prompt(): add quant flags
flags = ctx.get("quant_flags", [])
if flags:
    parts.append(f"\nRISK FLAGS: {', '.join(flags)}")
```

## Verification

### Automated Tests

- `pytest tests/test_trading_agent.py -v` — existing tests pass
- `pytest tests/test_trade_action.py -v` — schema validation unbroken
- New test: mock LLM returning BUY for a ticker with `bankruptcy_risk_high` flag and verify the post-LLM sanity check downgrades it

### Manual Verification

- Run one full loop and count BUY vs HOLD vs SELL distribution
- Verify no BUYs exist for tickers with `bankruptcy_risk_high` flag
