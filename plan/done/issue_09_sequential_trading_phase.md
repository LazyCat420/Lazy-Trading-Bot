# Issue 09 — Sequential Trading Phase (Nemotron)

**Severity:** LOW  
**Root Cause:** 45 tickers processed one-at-a-time at ~30s each = 22+ min. Nemotron (8B) has plenty of VRAM headroom for parallelism but isn't using it.

## Files to Modify

### 1. `app/services/autonomous_loop.py`

- `_do_trading()` (line 520): Add bounded parallel processing for trading decisions
- Use model size to determine concurrency: 8B models → 2-3 parallel, 32B models → 1 (sequential)

### 2. Priority ordering

- Sort tickers before processing: BUY-signal tickers first, then HOLD, then SELL
- Add early termination: if daily trade limit reached, skip remaining tickers

## Verification

### Automated Tests

- `pytest tests/test_trading.py -v`

### Manual Verification

- Run a full loop with nemotron and verify:
  - Trading phase < 12 min (down from 22 min)
  - No LLM errors from parallel calls
