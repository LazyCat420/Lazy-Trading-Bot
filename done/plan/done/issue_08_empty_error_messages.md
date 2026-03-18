# Issue 08 — Empty Error Messages from Ollama

**Severity:** LOW  
**Root Cause:** When Ollama returns a non-200 status, the error message logged is empty: `Ollama request FAILED -> 49.6s:`. The response body isn't being captured.

## Files to Modify

### 1. `app/services/llm_service.py`

- `_send_ollama_request()` (line 241): When response status != 200, log `response.text` or `response.json()` to capture the actual error message from Ollama
- Differentiate FAILED (non-200) vs TIMEOUT (exceeded limit) in log messages

## Specific Changes

```python
# In _send_ollama_request error handling:
if response.status_code != 200:
    error_body = response.text[:500]  # Cap to avoid log spam
    logger.warning(
        "Ollama request FAILED -> %.1fs: HTTP %d — %s",
        elapsed, response.status_code, error_body,
    )
```

## Verification

### Automated Tests

- `pytest tests/test_trading_agent.py -v`

### Manual Verification

- Trigger a bad request (e.g., invalid model name) and verify the log shows the actual Ollama error message
