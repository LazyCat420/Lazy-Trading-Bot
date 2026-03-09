# Issue 03 — Strategist Cash Check (Legacy Pipeline)

**Severity:** HIGH  
**Root Cause:** The legacy PortfolioStrategist burns all 10 turns trying to buy stocks it can't afford ($13.27 cash). No pre-check, no early termination.

## Files to Modify

### 1. `app/services/portfolio_strategist.py` (if legacy pipeline still used)

- Inject `Available cash: $X.XX` into the system prompt header as the FIRST line
- Pre-filter candidates by affordability in `get_market_overview` tool — hide tickers where `price > available_cash`
- Add early termination: if 3 consecutive buy attempts return "max safe qty is 0", stop the loop and log `insufficient_cash`

### 2. `app/services/autonomous_loop.py`

- `_do_trading()`: Before entering the strategist loop, check if `cash < min_trade_value` (e.g., $50). If so, skip the strategist entirely and log "Insufficient cash for trading".

## Specific Changes

```python
# autonomous_loop.py — _do_trading() cash pre-check
cash = paper_trader.get_cash()
if cash < 50:
    logger.warning("[AutoLoop] Skipping trading phase: cash=$%.2f below minimum", cash)
    return {"skipped": "insufficient_cash", "cash": cash}
```

## Verification

### Automated Tests

- `pytest tests/test_portfolio_strategist.py -v`
- `pytest tests/test_execution_service.py -v`

### Manual Verification

- Set a bot's cash to $10 and run a loop — verify it skips trading phase immediately instead of burning 10 turns
