# Fix Broken LLM Pipeline

## Root Cause

The Node.js trading backend defaults to `ollama` + `gemma3:27b` everywhere:
- `config.js:16` → `DEFAULT_LLM_PROVIDER = 'ollama'`
- `config.js:17` → `DEFAULT_LLM_MODEL = 'gemma3:27b'`
- `llmHelper.js:15` → fallback `'ollama'`
- `prismClient.js:119` → `provider = 'ollama'`
- `configService.js:13` → `DEFAULT_LLM_CONFIG.llm_provider = DEFAULT_LLM_PROVIDER`

The Python `llm_config.json` (which has `vllm`) is **NOT read** by the Node.js backend.
So every LLM call from phases 3-5 (Import, Analysis, Trading) sends `provider: 'ollama'` to Prism,
which tries to talk to Ollama at `10.0.0.30:11434` which returns 404.

**Result:** 30 minutes of running, zero LLM queries hitting vLLM.

## Fix Plan

### 1. Fix Node.js defaults → `vllm` + correct model
- `config.js:16-17` → `DEFAULT_LLM_PROVIDER = 'vllm'`, `DEFAULT_LLM_MODEL` = correct Qwen model
- `llmHelper.js:15` → fallback to `'vllm'` instead of `'ollama'`
- `prismClient.js:119` → default provider to `'vllm'`

### 2. Add LLM health check at startup
- `autonomousLoop.js` → at loop start, before any phases, send a tiny test prompt through PrismClient
- If it fails, log a CLEAR error and abort the loop instead of silently continuing
- This prevents 30-minute "running but doing nothing" scenarios

### 3. Add LLM health check to Python server startup
- The Python server also needs a preflight check that the vLLM endpoint is reachable

## Files to Modify

| File | Change |
|------|--------|
| `tradingbackend/src/config.js` | Default provider → `vllm`, model → Qwen |
| `tradingbackend/src/services/llmHelper.js` | Fallback → `vllm` |
| `tradingbackend/src/services/prismClient.js` | Default provider → `vllm` |
| `tradingbackend/src/services/autonomousLoop.js` | Add LLM preflight check |

## Status: TODO
