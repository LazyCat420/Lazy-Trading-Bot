# Deep Refactor (Phase 4+)

## Summary
- **Consolidated Loop Endpoints**: Removed duplicate `POST /api/bots/run-all` and `GET /api/bots/run-all/status` from `botRoutes.js`. Modified `frontend/static/terminal_app.js` to strictly use the existing identical loop control logic in `tradingRoutes.js` (`POST /api/bot/run-loop` and `GET /api/bot/loop-status`).
- **Fixed Stale Data**: The `deepAnalysis.js` and `tradingAgent.js` background services previously queried the MongoDB `stocks` collection for current prices, which were no longer updated by the backend. Rewrote both services to synthesize their context objects using live DuckDB data fetched from `pythonClient.getDashboardOverview(ticker)`.
- **Extracted LLM Stats Service**: Removed 250+ lines of inline Prism AI gateway logging and parsing arithmetic from `configRoutes.js` and abstracted it into `src/services/llmStatsService.js`.
- **Plan Updated**: Completed remaining checklist items and relocated `deep_refactor_audit.md` to the `done` folder.

---

# Navigation Merge: Unified Left Sidebar

## Summary

Consolidated the dual navigation system (top bar tabs + left sidebar) into a single, unified left sidebar menu with route-based navigation via React Router's `HashRouter`.

## Changes Made

### `frontend/static/terminal_app.js`

#### Imports (Line 3)
- Added `useLocation` to the `ReactRouterDOM` destructuring

#### `SidebarLayout` Component (~Line 2033)
- Removed `active` prop — now auto-detects active page from URL hash via `useLocation()`
- Added route-to-active-id mapping for all routes
- Added **Data Ingestion** nav items to sidebar
- Removed **Live Feed** (Diagnostics already has live stream)

#### Page Components (Wrapper Removal)
Removed `<SidebarLayout>` wrapper from all 7 page components — the App root now provides a single SidebarLayout:
- `DashboardPage`, `WatchlistPage`, `AnalysisPage`, `SettingsPage`, `AutobotMonitorPage`, `DiagnosticsPage`, `DataExplorerPage`

#### Deleted Components
- `TAB_CONFIG`, `TopBar`, `CommandCenterTab`, `DataHubTab`

#### New Components
- `IngestPage` — standalone page for Universal Dropzone
- `AppRoutes` — unified routing with `SidebarLayout` → `Routes`

---

# Watchlist & Monitor Fixes

## Watchlist Data Loading Fix
- **Root cause**: Frontend called `/api/data/overview/{ticker}` (MongoDB) which returns market data nested in a `market` object with `closes` array, `rsi`, `marketCap`, `changePct`. Frontend expected DuckDB-style flat shape (`price.close`, `fundamentals.market_cap`, `technicals.rsi_14`).
- **Fix**: Added data transformation in `fetchOverview` to map MongoDB `market` fields:
  - `closes[-1]` → `price.close` (current price)
  - `closes[-2]` → `prev_price.close` (previous close)
  - `marketCap` → `fundamentals.market_cap`
  - `rsi` → `technicals.rsi_14`
  - `changePct` → fallback for CHANGE column

## Watchlist UI Fixes
- Fixed corrupted arrow characters in CHANGE column (`-2`/`-1/4` → `▲`/`▼`)
- Replaced skeleton loaders with `-` dashes for tickers without data
- Added fallback to `changePct` from MongoDB when `prev_close` not available

## DashboardPage Crash Fix
- Added optional chaining to `bot.positions?.length`, `bot.recent_trades?.length`
- Added fallback for `(bot.max_drawdown || 0)` to prevent multiply-on-undefined

## Duplicate MU Key Fix
- Added deduplication in `useMonitorData.fetchWatchlist()` using `Set` filter
- Backend `/api/watchlist` returns duplicate `MU` entries; frontend now filters them
