# Issue 01 — LLM Timeout Optimization (olmo-3:32b)

**Severity:** CRITICAL  
**Root Cause:** 32B model generates tokens too slowly on Jetson Orin AGX. Thinking chains of 5,000-9,000 chars exhaust the 180s timeout. Parallel LLM requests during collection compound the issue.

## Files to Modify

### 1. `app/services/llm_service.py`

- `_send_ollama_request()` (line 241): Add `num_predict` limit to cap thinking/generation tokens
- Add per-model timeout config: 32B models get 300s, 8B models keep 180s
- Log the full error body when Ollama returns non-200 (currently logs empty string)

### 2. `app/services/autonomous_loop.py`

- `_do_collection()` (line 320): Serialize LLM calls for 32B models (currently fires 3+ parallel Ollama requests at lines 459-461 of the run log)
- Add model-size detection: if model file > 15GB, set concurrency=1 for LLM calls

### 3. `app/services/trading_agent.py`

- `decide()` (line 45): Pass `max_tokens=1024` to `_llm.chat()` to cap generation length — trading decisions only need ~100-200 tokens of output, but the model burns 2,000-5,000 chars on thinking

## Specific Changes

```python
# llm_service.py — _send_ollama_request
# Add num_predict to the options dict, default 2048 for trading, higher for distillation
"options": {
    "num_ctx": effective_ctx,
    "num_predict": max_tokens or 2048,  # Cap generation length
    "temperature": temperature,
}

# llm_service.py — _send_ollama_request timeout
# Dynamic timeout based on model size
model_timeout = 300.0 if self._model_file_size_gb > 15 else 180.0
```

## Verification

### Automated Tests

- `pytest tests/test_trading_agent.py -v` — ensure existing tests still pass
- `pytest tests/test_vram_oom.py -v` — verify VRAM safety checks unbroken

### Manual Verification

- Run a single bot loop with olmo-3:32b and observe:
  - Zero timeouts in the health report
  - Average LLM call duration < 90s
  - Pipeline total time < 45 min
