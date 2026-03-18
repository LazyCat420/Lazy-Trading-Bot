# LLM Streaming Timeout Fix Plan

## Problem
The LLM service was using non-streaming requests with a hard 180s wall-clock timeout.
Thinking models (Qwen3) generate extensive reasoning tokens before the final JSON answer,
easily exceeding 180s. The request would get killed mid-generation even while the model
was actively producing tokens.

## Fix: Streaming with Activity-Based Idle Timeout

### vLLM Path (`_send_vllm_request`)
- Switched from `stream=false` to `stream=true`
- Parse SSE chunks incrementally via `resp.aiter_lines()`
- Accumulate content tokens and reasoning_content tokens separately
- Dedicated httpx.AsyncClient with `read=LLM_IDLE_TIMEOUT_SECONDS` as the idle window
- Only timeout if model goes completely silent for 120s
- Log progress every 30s: chunk count, content chars, reasoning chars
- Partial response recovery: if stream breaks after receiving some data, use what we have

### Prism Path (`_send_prism_request`)
- Increased timeout to 600s minimum (from 180s)
- Prism/Ollama streaming would need separate investigation of Prism's SSE format

### Config
- Added `LLM_IDLE_TIMEOUT_SECONDS = 120` to `config.py`
- Configurable via `LLM_IDLE_TIMEOUT_SECONDS` env var
- `LLM_CALL_TIMEOUT_SECONDS` now only used for initial connection timeout

## Files Changed
- `app/config.py` — added `LLM_IDLE_TIMEOUT_SECONDS`
- `app/services/llm_service.py` — rewrote `_send_vllm_request` for streaming
