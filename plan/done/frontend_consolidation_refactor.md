# Frontend Consolidation Refactor

Refactor the 8,627-line `terminal_app.js` monolith (7 routes) into a streamlined **4-tab** single-page layout with a universal data ingestion dropzone, reducing redundancy and making the trading bot collaborative.

## Current State (Audit Results)

| Component | Tech | Description |
|---|---|---|
| `Lazy-Trading-Bot/frontend/` | React via CDN + Babel, HashRouter, Tailwind CDN | 8,627-line monolith with 7 routes: `/` Watchlist, `/dashboard`, `/analysis/:ticker`, `/data` Data Explorer, `/monitor` Autobot Monitor, `/settings`, `/diagnostics` |
| `Lazy-Trading-Bot/ui/` | Vite + React + TS, ReactFlow, Zustand | Separate pipeline visualizer (not the main UI) |
| `tradingbackend/` | Express + MongoDB | 10 route files: data, bot, config, dashboard, portfolio, watchlist, pipeline, prism-proxy, stub, trading |
| `prism/` | Express backend | **DO NOT EDIT** — LLM orchestration backend with 14 route files |
| `retina/` | Next.js frontend | **DO NOT EDIT** — UI for prism (admin, console, conversations, media, models, text, workflows) |

### Current 7-Route Layout (Redundant)
```
/ ................. Watchlist table + chart + analysis panel
/dashboard ........ Dashboard with overview cards
/analysis/:ticker . Per-ticker deep analysis view
/data ............. Data Explorer (youtube, reddit, 13f, congress, ticker-tracker tabs)
/monitor .......... Bot run console + status
/settings ......... LLM config, model selection
/diagnostics ...... System health checks
```

## User Review Required

> [!IMPORTANT]
> **Prism/Retina are untouchable.** All integration is via API proxying through `tradingbackend/src/routes/prismProxyRoutes.js`. The diagnostics tab will call prism health endpoints but never modify prism/retina code.

> [!IMPORTANT]
> **Decision: LLM Serving Strategy** — For the "pick a model" question, the plan proposes a **Model Configuration Panel** that supports three backends (Ollama, LM Studio, vLLM) via a single `base_url` + `model_name` pattern. For vLLM, the user sets a fixed port (e.g., `8000`) when spinning up Docker containers, and the panel stores `http://localhost:8000/v1` as the endpoint. This avoids needing Docker API integration in the frontend for now. A future phase could add a "Launch vLLM Container" button that hits a backend endpoint wrapping `docker run`. **Please confirm if this approach works for you or if you want Docker container management from the UI in this phase.**

> [!IMPORTANT]
> **Decision: Data Organization** — The plan consolidates the current scattered data views into a single "Data Hub" tab with these categories:
> - **Market Intelligence** — watchlist, tracked funds (13F), ticker tracker
> - **Social Signals** — reddit mentions, congress trades
> - **Media & Research** — youtube transcripts, news articles, raw uploaded data
> - **Bot Performance** — portfolio, leaderboard, trade history
> 
> Each category is a sub-tab within the Data Hub, with a universal search bar at the top. **Confirm if these 4 categories make sense.**

---

## Proposed Changes

### New 4-Tab Layout

```
┌──────────────────────────────────────────────────────┐
│  [🤖 Command Center] [📊 Data Hub] [📡 Live Feed] [🔧 Diagnostics]  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  Tab content fills entire viewport                   │
│                                                      │
│  Each tab has its own sub-navigation where needed    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Tab 1: 🤖 Command Center** (merges: `/settings`, `/`, `/monitor`)
- Model picker (Ollama / LM Studio / vLLM endpoint config)
- Run controls (Run Analysis, Run All Bots)
- Bot watchlist & portfolio / leaderboard
- Run-all console (existing `RunAllConsole` component)

**Tab 2: 📊 Data Hub** (merges: `/data`, `/analysis/:ticker`, `/dashboard`)
- **Universal Dropzone** at top — drag & drop files (CSV, JSON, TXT, YouTube URLs, raw text) → auto-sorts into the correct sub-category
- Sub-tabs: Market Intelligence | Social Signals | Media & Research | Bot Performance
- Existing Data Explorer grid reused per sub-tab
- Per-ticker deep-dive as an expandable panel (not a separate route)

**Tab 3: 📡 Live Feed** (new — merges pieces of `/monitor` and `/dashboard`)
- Real-time scrolling log of all incoming data (WebSocket-driven)
- Filter by source: scrapers, reddit, youtube, news, trades
- Shows live analysis events (SSE stream) as they happen

**Tab 4: 🔧 Diagnostics** (merges: `/diagnostics` + prism/retina status)
- System health (Python backend, MongoDB, prism, retina)
- Prism/retina status via proxy endpoints
- Error log viewer
- DB stats

---

### Backend: Universal Data Ingestion

#### [NEW] [ingestRoutes.js](file:///D:/Github/TRADING-BOT/tradingbackend/src/routes/ingestRoutes.js)
New Express route for the universal dropzone:
- `POST /api/ingest` — accepts `multipart/form-data`
- Auto-classifies files by extension and content:
  - `.csv/.json` → parse and route to appropriate collection
  - `.txt` → check for YouTube URL patterns → transcribe or store as raw text
  - YouTube URLs → forward to Python `youtube_service.py` for processing
  - News article URLs → forward to Python `news_service.py`
- Returns classification result to frontend

#### [MODIFY] [index.js](file:///D:/Github/TRADING-BOT/tradingbackend/src/index.js)
- Mount `ingestRoutes` at `/api/ingest`
- Add `multer` middleware for file uploads

---

### Frontend: `terminal_app.js` Refactor

#### [MODIFY] [terminal_app.js](file:///D:/Github/TRADING-BOT/Lazy-Trading-Bot/frontend/static/terminal_app.js)

This is the big change. The file stays as a single monolith (to avoid build tooling changes) but the internal structure changes:

1. **Replace HashRouter 7-route setup** with a **4-tab** stateful layout (no routing needed — just `useState` for active tab)
2. **Extract reusable components** that already exist (ChartWidget, RunAllConsole, TickerDetailPanel, DataDetailModal) 
3. **Add new components:**
   - `UniversalDropzone` — drag & drop + paste area with file type auto-detection
   - `ModelPicker` — server type selector + endpoint URL + model name
   - `LiveFeedPanel` — WebSocket-connected scrolling log
   - `DiagnosticsPanel` — health checks for all services including prism/retina proxy
   - `DataHubView` — unified data view with 4 sub-category tabs
4. **Remove separate pages** (DashboardPage, SettingsPage, AnalysisPage) and fold their content into the relevant tabs

---

### Package Changes

#### [MODIFY] [package.json](file:///D:/Github/TRADING-BOT/tradingbackend/package.json)
- Add `multer` dependency for file upload handling

---

## Verification Plan

### Automated Tests

1. **Existing Python tests** (63 test files in `Lazy-Trading-Bot/tests/`):
   ```powershell
   cd D:\Github\TRADING-BOT\Lazy-Trading-Bot
   .\venv\Scripts\python.exe -m pytest tests/test_smoke.py -v
   ```
   These verify the Python backend isn't broken.

2. **Existing tradingbackend test**:
   ```powershell
   cd D:\Github\TRADING-BOT\tradingbackend
   npx vitest run
   ```

3. **New ingest route test** — will create `tradingbackend/tests/ingest.test.js` to verify:
   - File upload + auto-classification logic
   - YouTube URL detection
   - Error handling for unsupported file types

### Manual Verification

> [!NOTE]
> Since the frontend is a CDN-based React app with no build step, manual browser testing is the primary verification method.

1. **Start the server** (user runs `npm run dev` as usual)
2. **Open the frontend** in browser
3. **Verify 4-tab navigation** — all 4 tabs render without errors
4. **Test Command Center tab:**
   - Model picker shows/hides endpoint fields based on selected backend
   - Run controls trigger analysis
   - Watchlist table displays
5. **Test Data Hub tab:**
   - All 4 sub-tabs show data grids
   - Dropzone accepts files via drag & drop
   - Dropzone auto-classifies a `.csv` file
6. **Test Live Feed tab:**
   - WebSocket connects and shows real-time events
7. **Test Diagnostics tab:**
   - Shows health status for Python, MongoDB, prism, retina
   - DB stats load correctly
