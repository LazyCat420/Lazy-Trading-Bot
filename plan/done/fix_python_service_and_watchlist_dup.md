# Plan: Fix Python Service + Watchlist Duplicate Key

## Issues Found

### 1. Python service not available
The Python FastAPI service (uvicorn on :8000) was not running during the autonomous loop.
The `npm run dev` in Lazy-Trading-Bot only serves the frontend (live-server on :3000 proxied to :4000).
The Python backend must be started separately:
```
cd Lazy-Trading-Bot && source venv/bin/activate && uvicorn app.main:app --port 8000
```

### 2. E11000 duplicate key on watchlist
MongoDB had a unique index on `watchlist.ticker` alone.
With multi-bot support, the same ticker can appear for different bots.
The `importEvaluator.js` correctly uses `{ ticker, bot_id }` for upserts,
but the index blocked inserts when the ticker already existed under any bot_id.

## Fix Applied
- Changed `db.js` watchlist index from `{ ticker: 1 }` (unique) to `{ ticker: 1, bot_id: 1 }` (unique).
- Added `dropIndex('ticker_1')` to clean up the legacy index on next startup.

## Status: DONE
