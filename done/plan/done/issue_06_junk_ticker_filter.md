# Issue 06 — Junk Tickers Leaking Through (CNBC, QQQM.MX)

**Severity:** MEDIUM  
**Root Cause:** CNBC passes validation (real closed-end fund ticker) but has no usable data. QQQM.MX is a Mexican-exchange ETF that crashes AllStudy with NaN metrics.

## Files to Modify

### 1. Exclusion list config (wherever `exclusion_list` is defined)

- Add: `CNBC`
- Add pattern: reject all `.MX` suffixed tickers

### 2. `app/services/data_pipeline.py` (or collection orchestrator)

- Fail-fast in collection phase: if `price_history` returns 0 rows, skip ALL downstream steps for that ticker immediately and log a warning
- If `risk_metrics` returns all NaN, remove ticker from watchlist before analysis phase

## Verification

### Automated Tests

- `pytest tests/test_discovery.py -v`
- `pytest tests/test_data_pipeline.py -v`

### Manual Verification

- Grep logs after a run: no CNBC or .MX tickers should appear past the discovery phase
