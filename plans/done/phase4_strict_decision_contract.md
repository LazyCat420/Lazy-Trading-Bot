# Phase 4 — Strict Decision Contract (Structured Outputs + Repair)

## Goal

Ensure every LLM trading decision is **valid, tradeable, and explainable.** No more pipeline breakage on messy LLM outputs. Every decision gets persisted to DB for auditability.

---

## Current State (What Exists)

| Component | Status | Notes |
|---|---|---|
| `TradeAction` schema | ❌ Missing | No Pydantic model for LLM decisions. `PortfolioStrategist` uses raw JSON tool-calls with `ACTION_SCHEMA` (loose object schema). |
| JSON cleanup in `llm_service.py` | ✅ Exists | `_clean_json_response()` strips fences, extracts JSON. Battle-tested. |
| `response_format="json"` support | ✅ Exists | `LLMService.chat()` supports `response_format` param. |
| Auto-repair prompt loop | ❌ Missing | No retry-with-repair on validation failure. |
| Symbol validation post-decision | ✅ Partial | `symbol_filter.py` exists with full `FilterPipeline`, but not wired into any decision validation. |
| `rejected_symbols` table | ✅ Exists | In `database.py` + `symbol_filter.py`. |
| `trade_decisions` DB table | ❌ Missing | No audit trail for decisions. |
| `trade_executions` DB table | ❌ Missing | No audit trail for execution outcomes. |

---

## PR-by-PR Implementation

### PR 4.1: `TradeAction` Schema + Parser/Repair (No behavior change)

**File:** `app/models/trade_action.py` [NEW]

```python
from pydantic import BaseModel, Field
from typing import Literal

class TradeAction(BaseModel):
    """The one true decision schema for LLM trading decisions."""
    
    bot_id: str
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    risk_notes: str = ""
    risk_level: Literal["LOW", "MED", "HIGH"] = "MED"
    time_horizon: Literal["INTRADAY", "SWING", "POSITION"] = "SWING"
```

**File:** `app/services/trade_action_parser.py` [NEW]

```python
class TradeActionParser:
    """Parse + validate + auto-repair LLM output → TradeAction."""
    
    async def parse(self, raw_llm_text: str, bot_id: str, symbol: str) -> TradeAction:
        """
        1. Use llm_service._clean_json_response() to strip fences + extract JSON
        2. json.loads() → TradeAction.model_validate()
        3. If validation fails:
           a. Call LLMService.chat() with "repair this JSON" prompt (low temp)
           b. Re-parse and validate
           c. If still fails → force HOLD action with error rationale
        4. Post-validation: run symbol through FilterPipeline
           a. If symbol fails → force action="HOLD", log to rejected_symbols
        5. Return validated TradeAction
        """
```

**Unit tests:** `tests/test_trade_action.py` [NEW]

- Test valid JSON → parses correctly
- Test malformed JSON → repair prompt triggered → parses on retry
- Test invalid symbol → forced HOLD + rejection logged
- Test missing fields → defaults applied correctly
- Test confidence out of range → validation error

---

### PR 4.2: Persist Decisions + Executions (Audit Trail)

**File:** `app/database.py` [MODIFY]

Add two new tables:

```sql
CREATE TABLE IF NOT EXISTS trade_decisions (
    id VARCHAR PRIMARY KEY,
    bot_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action VARCHAR NOT NULL,        -- BUY/SELL/HOLD
    confidence DOUBLE,
    rationale TEXT,
    risk_level VARCHAR,
    time_horizon VARCHAR,
    raw_llm_response TEXT,          -- full LLM output for debugging
    status VARCHAR DEFAULT 'pending', -- pending/executed/rejected/error
    rejection_reason TEXT
);

CREATE TABLE IF NOT EXISTS trade_executions (
    id VARCHAR PRIMARY KEY,
    decision_id VARCHAR NOT NULL,   -- FK to trade_decisions.id
    order_id VARCHAR,               -- Links to paper_trader orders table
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    filled_qty INTEGER,
    avg_price DOUBLE,
    status VARCHAR DEFAULT 'pending', -- pending/filled/failed/skipped
    broker_error TEXT
);
```

**File:** `app/services/decision_logger.py` [NEW]

```python
class DecisionLogger:
    """Persist TradeAction decisions and execution outcomes to DuckDB."""
    
    def log_decision(self, action: TradeAction, raw_llm: str) -> str:
        """Insert into trade_decisions, return decision_id."""
    
    def log_execution(self, decision_id: str, order_id: str,
                      qty: int, price: float, status: str, error: str = "") -> str:
        """Insert into trade_executions, return execution_id."""
    
    def get_decisions(self, bot_id: str, limit: int = 50) -> list[dict]:
        """Query recent decisions for UI display."""
    
    def get_decision_with_execution(self, decision_id: str) -> dict:
        """Join decision + execution for debugging."""
```

---

### PR 4.3: Symbol Validation Post-Decision

Already partially built. Wire `symbol_filter.py` into `trade_action_parser.py`:

```python
# In parse(), after Pydantic validation passes:
from app.services.symbol_filter import get_filter_pipeline

result = get_filter_pipeline().run(
    action.symbol,
    {"source": "trade_decision", "bot_id": bot_id}
)
if not result.passed:
    action.action = "HOLD"
    action.rationale = f"Symbol rejected by filters: {result.reason}"
    _log_rejection(action.symbol, result.reason, {"source": "trade_decision"})
```

No new files — this is wired into `trade_action_parser.py` from PR 4.1.

---

### PR 4.4: API Endpoints for Decision History

**File:** `app/main.py` [MODIFY]

Add endpoints so the UI can query decisions/executions from DB:

```
GET /api/bots/{bot_id}/decisions?limit=50      → list of trade_decisions
GET /api/decisions/{decision_id}                → decision + execution detail
GET /api/bots/{bot_id}/decisions/summary        → counts by action/status
```

This makes the bot debuggable from the UI without reading terminal logs.

---

## Files Summary

| File | Action |
|---|---|
| `app/models/trade_action.py` | **NEW** |
| `app/services/trade_action_parser.py` | **NEW** |
| `app/services/decision_logger.py` | **NEW** |
| `app/database.py` | **MODIFY** (add 2 tables) |
| `app/main.py` | **MODIFY** (add decision history endpoints) |
| `tests/test_trade_action.py` | **NEW** |

## Dependency Order

> [!IMPORTANT]
> Phase 4 PR 4.1 (`TradeAction` schema) must be built **BEFORE** Phase 3 PR 3.1 (`TradingAgent`), because the agent returns a `TradeAction`.

Recommended build order across both phases:

```
PR 4.1 → PR 4.2 → PR 3.1 → PR 3.2 → PR 3.3 → PR 3.4 → PR 4.3 → PR 4.4 → PR 3.5
```

## Verification

- Unit test: `TradeAction` schema validates good JSON, rejects bad JSON
- Unit test: Parser handles malformed LLM output (markdown fences, missing fields, nested JSON)
- Unit test: Repair loop recovers broken JSON on second attempt
- Unit test: Symbol filter integration forces HOLD on invalid symbols
- Integration test: `DecisionLogger` writes and reads from DuckDB correctly
- Manual test: After a full trading cycle, check `trade_decisions` table has rows for every ticker analyzed

## Acceptance Criteria (Phase 3 + 4 Combined)

- [ ] One run cycle produces, for each ticker: either a validated `trade_decisions` row or a `rejected_symbols` record with a reason
- [ ] The bot can execute a BUY/SELL end-to-end in non-dry-run mode without any other LLM steps besides the single decision call
- [ ] Leaving/returning to UI pages never loses state — decisions/executions are queryable via API from DB
