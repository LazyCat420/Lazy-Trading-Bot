# Fix All Audit Findings

## Proposed Changes

### Buy-Loop Prevention

The core issue: `execution_service.py` Gate 3 only blocks duplicate trades within a 5-minute in-memory window that resets on restart. Across bot runs hours apart, the same ticker (KO) gets re-bought.

#### [MODIFY] [execution_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/execution_service.py)

1. **Add Gate 1.5: "Already holding" block** — Before computing qty, check if we already hold this ticker. If so, block the BUY entirely (DCA is handled separately by the strategist's explicit logic). This mirrors the guard already in `portfolio_strategist.py:783-794`.

2. **Add Gate 1.6: Buy cooldown from DB** — Query `orders` table for last BUY of this ticker. If bought within the last 24 hours, block. This persists across restarts unlike the in-memory `_recent_trades` list.

---

### JSON Parse Robustness

#### [MODIFY] [llm_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/llm_service.py)

1. **Add JSON repair** to `clean_json_response`: fix trailing commas, strip comments, handle `NaN`/`Infinity` literals.
2. **Add retry with explicit JSON instruction** when first parse fails — already exists as dual-mode retry for empty responses, extend to malformed JSON.

---

### Ruff Lint

#### Run `ruff check --fix app/` and `ruff format app/`

Auto-fix 75 issues, skip the remaining 76 unsafe fixes.

## Verification Plan
- Query DB to verify buy-loop guard blocks repeat KO buys
- Test JSON parsing with known-bad LLM output samples
- Run `ruff check app/` and confirm 0 errors
