Now I have everything needed. Here's the full plan:

***

# Lazy Trading Bot — Node-Based Pipeline UI
## Project Plan & Dev Checklist

**Goal:** Build a real-time, node-based visual UI that shows every stage of the trading bot's pipeline — what data is being collected, where it's flowing, and what decisions are being made — by connecting to the existing FastAPI backend via WebSocket.

**Repo:** [LazyCat420/Lazy-Trading-Bot](https://github.com/LazyCat420/Lazy-Trading-Bot)

***

## Overview of the Pipeline (What We're Visualizing)

The bot already has a fully documented 5-phase autonomous loop in `app/services/autonomous_loop.py` :

1. **Discovery** — Reddit + YouTube scan for tickers
2. **Import** — Top tickers → Watchlist
3. **Collection** — Financial data fetch (yFinance, News, RSS, SEC, Congress)
4. **Embedding** — RAG vector indexing of collected data
5. **Analysis** — 4-layer deep analysis + conviction scoring per ticker
6. **Trading** — LLM signal → ExecutionService → PaperTrader

Each phase already calls `log_event()` and updates `_state` — meaning the hooks are already there . We just need to pipe them to the frontend.

***

## Phase 1 — Backend: WebSocket Event Bus

**Goal:** Emit structured pipeline events from Python to any connected frontend client in real time.

### Tasks

- [ ] **Create `/app/services/ws_broadcaster.py`**
  - Singleton list of connected WebSocket clients
  - `async def broadcast(event: dict)` function that iterates all clients and sends JSON
  - Handle disconnected clients gracefully (try/except, remove on error)

- [ ] **Register WebSocket endpoint in `server.py`**
  - Add `GET /ws/pipeline` WebSocket route
  - On connect: add client to broadcaster list, send current `loop_state` immediately as a "snapshot" event
  - On disconnect: remove from list

- [ ] **Wire `broadcast()` into `autonomous_loop.py`**
  - In `_run_phase()`, after each `log_event()` call, also call `await broadcast({...})`
  - Emit a standard event shape (see spec below) at phase start, phase complete, and phase error
  - Add broadcaster calls inside `_do_discovery`, `_do_collection`, `_do_embedding`, `_do_deep_analysis`, `_do_trading` at key data checkpoints (e.g. "ticker X analysis done", "order placed")

- [ ] **Define the standard event payload shape**
  ```json
  {
    "type": "phase_update",
    "node": "discovery",
    "status": "running | done | error | idle",
    "label": "Found 12 tickers",
    "data_in": "Reddit + YouTube",
    "data_out": "12 tickers",
    "timestamp": 1234567890.0,
    "meta": {}
  }
  ```

- [ ] **Add a REST endpoint `GET /api/pipeline/snapshot`**
  - Returns `autonomous_loop.get_status()` — current phase states, log, and last run summary
  - Used by frontend on first load to get current state before WebSocket events start flowing

- [ ] **Test the WebSocket manually**
  - Use `wscat` or a browser console `new WebSocket("ws://localhost:8000/ws/pipeline")` to confirm events are firing correctly during a loop run

***

## Phase 2 — Frontend: React Flow App Scaffold

**Goal:** Set up the React + React Flow frontend project inside the repo.

### Tasks

- [ ] **Scaffold the UI project**
  ```bash
  cd /path/to/Lazy-Trading-Bot
  npm create vite@latest ui -- --template react-ts
  cd ui
  npm install @xyflow/react
  npm install zustand          # for state management
  npm install lucide-react     # for icons on nodes
  ```

- [ ] **Configure Vite proxy** in `ui/vite.config.ts`
  ```ts
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true }
    }
  }
  ```

- [ ] **Set up folder structure**
  ```
  ui/src/
    components/
      nodes/           ← custom node components
      edges/           ← animated edge components
    hooks/
      usePipelineSocket.ts
    store/
      pipelineStore.ts
    data/
      initialNodes.ts  ← static node/edge definitions
    App.tsx
  ```

***

## Phase 3 — Frontend: Node & Edge Definitions

**Goal:** Define the static graph structure that mirrors the actual pipeline.

### Tasks

- [ ] **Create `initialNodes.ts`** — define all pipeline nodes with positions

  Map every service to a node. Based on the codebase , the nodes should be:

  | Node ID | Label | Group |
  |---|---|---|
  | `reddit` | Reddit Scanner | Data Sources |
  | `youtube` | YouTube Scanner | Data Sources |
  | `rss_news` | RSS News | Data Sources |
  | `sec_13f` | SEC 13F | Data Sources |
  | `congress` | Congress Trades | Data Sources |
  | `discovery` | Discovery Service | Phase 1 |
  | `watchlist` | Watchlist Import | Phase 2 |
  | `yfinance` | yFinance Fetcher | Phase 3 |
  | `technical` | Technical Service | Phase 3 |
  | `data_distiller` | Data Distiller | Phase 3 |
  | `embedding` | Embedding / RAG | Phase 4 |
  | `llm` | LLM Service | Phase 5 |
  | `deep_analysis` | Deep Analysis | Phase 5 |
  | `quant_engine` | Quant Engine | Phase 5 |
  | `risk` | Risk Service | Phase 6 |
  | `execution` | Execution Service | Phase 6 |
  | `paper_trader` | Paper Trader | Phase 6 |

- [ ] **Create `initialEdges.ts`** — define directed edges between nodes
  - `reddit → discovery`, `youtube → discovery`, `rss_news → discovery`, `sec_13f → discovery`, `congress → discovery`
  - `discovery → watchlist`
  - `watchlist → yfinance`, `watchlist → technical`, `watchlist → data_distiller`
  - `yfinance → data_distiller`, `technical → data_distiller`
  - `data_distiller → embedding`
  - `embedding → deep_analysis`, `embedding → llm`
  - `quant_engine → deep_analysis`
  - `deep_analysis → risk`
  - `llm → execution`
  - `risk → execution`
  - `execution → paper_trader`

- [ ] **Set up animated edges** — use React Flow's built-in `animated: true` on edges so data flow is visually implied, or install `@xyflow/react` animated edge plugin for a flowing-particle effect

***

## Phase 4 — Frontend: Custom Node Component

**Goal:** Each node shows live status, last data in/out, and a timestamp.

### Tasks

- [ ] **Create `PipelineNode.tsx`** — custom React Flow node component
  - Props: `id`, `data: { label, status, dataIn, dataOut, lastUpdated }`
  - Status-based border/background color:
    - `idle` → gray border
    - `running` → yellow/amber pulsing border (CSS animation)
    - `done` → green border
    - `error` → red border
  - Show inside the node card:
    - Node label (bold)
    - Status badge
    - `data_in` and `data_out` text (small, muted)
    - Last updated timestamp

- [ ] **Register the custom node type** in `App.tsx`
  ```ts
  const nodeTypes = { pipeline: PipelineNode };
  ```

- [ ] **Add a sidebar / detail panel**
  - On node click, show expanded info: full meta payload from last event, duration, error message if any
  - Implemented as a slide-in panel, not a modal

***

## Phase 5 — Frontend: Live WebSocket Integration

**Goal:** Connect the graph to the backend and update node states in real time.

### Tasks

- [ ] **Create `usePipelineSocket.ts` hook**
  - Opens `WebSocket` to `ws://localhost:8000/ws/pipeline` on mount
  - On `message`: parse JSON, dispatch to `pipelineStore`
  - Auto-reconnect on disconnect (exponential backoff, max 5 retries)
  - Returns `{ connected: boolean, lastEvent }`

- [ ] **Create `pipelineStore.ts` (Zustand)**
  - State: `nodeStatuses: Record<nodeId, { status, label, dataIn, dataOut, timestamp, meta }>`
  - Action: `updateNode(nodeId, payload)` — called by the WebSocket hook on each event
  - Action: `loadSnapshot(snapshotData)` — called once on page load from `GET /api/pipeline/snapshot`

- [ ] **Wire store into the React Flow graph**
  - On each render, merge `initialNodes` with current status from `pipelineStore` to produce the live node array passed to `<ReactFlow nodes={...} />`
  - This keeps the graph structure static but the node data dynamic

- [ ] **Fetch snapshot on app load**
  - On `App.tsx` mount, call `GET /api/pipeline/snapshot`, call `loadSnapshot()` to pre-populate node states before any WebSocket events arrive

***

## Phase 6 — Activity Feed & Status Bar

**Goal:** Add a scrolling event log and a top-level status bar so users can see the live text log alongside the graph.

### Tasks

- [ ] **Activity Feed panel** (bottom or right sidebar)
  - Scrolling list of `log_event` messages from the WebSocket stream
  - Each entry shows: timestamp, phase tag (color-coded), message text
  - Auto-scrolls to bottom on new events
  - Filter by phase (All / Discovery / Collection / Analysis / Trading)

- [ ] **Top status bar**
  - Shows: current loop phase (e.g. "▶ Running: Deep Analysis"), elapsed time, WebSocket connection status (green dot = connected)
  - A "Run Loop" button that calls `POST /api/loop/start` to trigger a new run

- [ ] **Node hover tooltip**
  - On hover, show the full `meta` JSON payload from the last event for that node (useful for debugging)

***

## Phase 7 — Polish & Deployment

### Tasks

- [ ] **Serve the built UI from FastAPI**
  - In `server.py`, mount the Vite build output: `app.mount("/", StaticFiles(directory="ui/dist", html=True))`
  - Add a `build` script to `run.sh`: `cd ui && npm run build`

- [ ] **Add the UI startup to `run.sh`**
  - In dev mode: `cd ui && npm run dev &` before starting uvicorn

- [ ] **Dark mode styling**
  - Set React Flow background to dark (`<Background color="#111" />`), match the rest of the existing dashboard theme

- [ ] **README update**
  - Document the UI setup steps, how to run in dev, how to build for prod

***

## Key Files to Create/Modify

| File | Action |
|---|---|
| `app/services/ws_broadcaster.py` | **Create new** |
| `server.py` | **Modify** — add `/ws/pipeline` and `/api/pipeline/snapshot` |
| `app/services/autonomous_loop.py` | **Modify** — add `broadcast()` calls in `_run_phase()` and phase methods |
| `ui/` (entire folder) | **Create new** — Vite + React + React Flow app |
| `ui/src/data/initialNodes.ts` | **Create new** — static node/edge graph |
| `ui/src/components/nodes/PipelineNode.tsx` | **Create new** — custom node component |
| `ui/src/hooks/usePipelineSocket.ts` | **Create new** — WebSocket hook |
| `ui/src/store/pipelineStore.ts` | **Create new** — Zustand state store |
| `run.sh` | **Modify** — add UI build/dev start |

***

## Dependencies

**Python (no new deps needed)** — FastAPI WebSocket support is built-in via `starlette`

**Node/npm:**
```
@xyflow/react      ← the node graph engine
zustand            ← lightweight state management
lucide-react       ← icons
vite               ← build tool (already via scaffold)
```

***

## Suggested Dev Order

1. Backend WebSocket broadcaster + snapshot endpoint (Phase 1) — unblock everything
2. React scaffold + static graph (Phases 2–3) — visible immediately, no backend needed
3. WebSocket hook + store (Phase 5) — connects the two halves
4. Custom node styling (Phase 4) — makes it look good
5. Activity feed + status bar (Phase 6) — adds depth
6. Build pipeline + `run.sh` integration (Phase 7) — productionize