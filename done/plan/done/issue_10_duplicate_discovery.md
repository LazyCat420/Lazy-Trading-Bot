# Issue 10 — Duplicate Discovery Across Bots

**Severity:** LOW  
**Root Cause:** When RunAll processes multiple bots, each bot runs its own full discovery scan. Both bots scrape the same Reddit threads, 13F data, and Congress data — returning near-identical ticker lists. This doubles the time spent on discovery (247s + 271s = ~8 min wasted).

## Files to Modify

### 1. `app/services/autonomous_loop.py` or RunAll orchestrator

- Run discovery ONCE before the bot loop
- Pass shared discovery results to each bot's loop
- Each bot still does its own analysis/trading (model-specific)

### 2. Data collection dedup

- Mark data as "collected today" per-ticker (already partially implemented via "already collected today" checks in logs)
- Ensure the second bot skips re-collection entirely

## Verification

### Manual Verification

- Run RunAll with 2 bots and verify:
  - Discovery runs once, not twice
  - Total pipeline time reduced by ~4-5 min
