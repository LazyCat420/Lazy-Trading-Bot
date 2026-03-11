# Route All LLM Calls Through Prism AI Gateway

## Done

All trading bot LLM calls now route through Prism (`POST /text-to-text`) instead of
directly to Ollama `/api/chat`. Prism centralizes logging and usage tracking.

### Files Changed
- **MODIFIED** `app/config.py` — Added `PRISM_URL`, `PRISM_SECRET`, `PRISM_PROJECT`. `LLM_BASE_URL` → Prism URL.
- **MODIFIED** `app/services/llm_service.py` — `_call_prism`/`_send_prism_request` replace old Ollama calls. Added `fetch_models_from_prism`.
- **MODIFIED** `app/main.py` — `/api/llm-models` fetches from Prism by default.
- **MODIFIED** `app/static/terminal_app.js` — Settings UI: Prism URL + Secret fields replace Ollama URL.

### What Goes Through Prism
- All `chat()` calls (text generation via `/text-to-text`)
- Model listing (via Prism `/config`)

### What Stays Direct (Ollama)
- `verify_and_warm_ollama_model` (model loading)
- `estimate_model_vram` (architecture query)
- VRAM estimation display

### Tests: 4/4 passed
