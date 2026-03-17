# LLM Streaming Timeout Fix — Done

## What was fixed
1. vLLM path now uses **SSE streaming** instead of waiting for the full response
2. **Activity-based idle timeout** (120s): only aborts if model goes completely silent
3. Tokens flowing = **never timeout** — model can generate for hours if needed
4. Prism/Ollama path increased to 600s generous timeout
5. Progress logging every 30s during streaming

## Files Changed
- `app/config.py` — added `LLM_IDLE_TIMEOUT_SECONDS = 120`
- `app/services/llm_service.py` — rewrote `_send_vllm_request()` for streaming
- `app/services/llm_service.py` — increased Prism timeout to 600s

## Test Results
- 25/25 tests pass in `test_shared_pipeline.py`
- LLMService imports clean
