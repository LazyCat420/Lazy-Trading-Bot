# Graceful Shutdown + Emergency Stop

## Problem
When the server is killed (Ctrl+C) while Ollama is processing a request,
Ollama continues processing because it's a separate service. There was also
no way to stop bots mid-run from the UI.

## Solution — 3 Layers of Protection

### Layer 1: Shutdown Handler (automatic)
When the server is killed with Ctrl+C or SIGTERM, the `@app.on_event("shutdown")`
handler automatically:
- Sets the shutdown flag to block all new LLM requests
- Cancels running asyncio tasks (loop, run-all)
- Closes the shared HTTP client (aborts in-flight requests)
- Unloads the Ollama model from VRAM via `keep_alive: "0"`

### Layer 2: Emergency Stop Button (UI)
Red "Emergency Stop" button appears in the sidebar whenever bots are running.
Calls `POST /api/bot/emergency-stop` which does the same as Layer 1 but
keeps the server running.

### Layer 3: LLM Queue Gate (per-request)
Every LLM request checks `_shutdown_requested` before entering the queue.
If shutdown is active, requests return empty immediately instead of waiting.

## Files Changed
- `app/services/llm_service.py` — shutdown flag + gate
- `app/services/autonomous_loop.py` — cancel() method
- `app/main.py` — shutdown handler + 2 new endpoints
- `app/static/terminal_app.js` — Emergency Stop button

## API Endpoints
- `POST /api/bot/stop-loop` — stop single-bot loop
- `POST /api/bot/emergency-stop` — nuclear stop all operations
