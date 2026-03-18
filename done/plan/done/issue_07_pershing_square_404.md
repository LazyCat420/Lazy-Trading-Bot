# Issue 07 — Pershing Square 404 (Recurring)

**Severity:** MEDIUM  
**Root Cause:** CIK `0001116304` for Pershing Square Capital returns 404 on every run. Wasted HTTP call + log noise.

## Files to Modify

### 1. SEC 13F filers config

- Find the 13F filer list (likely in `app/services/sec_13f_service.py` or a config file)
- Either remove Pershing Square or update the CIK to the correct one

## Verification

### Automated Tests

- `pytest tests/test_sec_13f.py -v`

### Manual Verification

- Run discovery and confirm no 404 warning for Pershing Square in logs
