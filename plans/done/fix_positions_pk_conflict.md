# Fix: Positions Table PK Conflict — DONE

## Problem

BUY orders for KO and NVDA failed with:

```
Constraint Error: Duplicate key "ticker: KO" violates primary key constraint
```

## Root Cause

`positions` table had `ticker VARCHAR PRIMARY KEY` — single-column PK.
Multi-bot system needs `PRIMARY KEY (ticker, bot_id)` so each bot can hold the same ticker independently.

`PaperTrader._get_position_row()` correctly filtered by `bot_id` in WHERE clauses,
but the INSERT hit the ticker-only PK constraint when a different bot already owned the position.

## Fix Applied

1. **`database.py`**: Changed positions DDL to `PRIMARY KEY (ticker, bot_id)`
2. **Runtime migration**: Detects old schema (no `bot_id` column), recreates table preserving data
3. **Also added `bot_id`** column to `orders`, `price_triggers`, `portfolio_snapshots` DDL

## Verification

- 66 tests pass, ruff clean
- Server restart will trigger migration automatically
