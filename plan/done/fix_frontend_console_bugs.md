# Fix Frontend Console Bugs

## Problem
1. Duplicate `MU` key warning — watchlist has `MU` listed twice, React key collision
2. `/api/quotes` returns 404 — route is `/api/data/quotes`, but frontend calls `/api/quotes`
3. `/api/dashboard/db-stats` returns 404 — route doesn't exist, closest is `/api/data/db-stats`
4. `sw.js` / chrome-extension errors — from Yoroi wallet extension (NOT our code)

## Root Cause
- Watchlist fetches from MongoDB without dedup, duplicates cause key collision
- API path mismatch between frontend and backend route definitions

## Fix
1. **Dedup watchlist** — `[...new Set(tickers)]` at load time (terminal_app.js line 310)
2. **Index-based React keys** — `key={${ticker}-${i}}` instead of `key={ticker}` for safety
3. **Add `/quotes` alias** in `dataRoutes.js` — delegates to Python service or MongoDB
4. **Add `/dashboard/db-stats`** in `dashboardRoutes.js` — returns collection stats

## Files Changed
- `terminal_app.js` — dedup + index keys
- `dataRoutes.js` — `/quotes` alias
- `dashboardRoutes.js` — `/dashboard/db-stats` alias
