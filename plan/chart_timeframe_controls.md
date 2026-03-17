# Chart Timeframe Controls + Full History Backfill

## Goal
Add timeframe selectors (1D/1W/1M/3M/1Y/ALL) to the price chart and switch price collection to full history with incremental daily fetching.

## Changes Made

### Frontend — `terminal_app.js`
- Added `TIMEFRAMES` constant and `days` prop to `ChartWidget`
- Added `chartDays` state + timeframe buttons in the Overview tab header
- SMAs automatically hide for short timeframes (SMA20 ≤7d, SMA50 ≤30d)

### Styling — `style.css`
- Added `.timeframe-btn` and `.timeframe-btn.active` CSS classes

### Backend — `yfinance_service.py`
- Changed `period="1y"` → `period="max"` for initial backfill
- Added incremental fetch logic: checks latest stored date and only fetches the gap
- First run per ticker: full history (one-time, ~5-10 seconds)
- Every subsequent run: only new rows since last fetch (~instant)

## How It Works
1. **First pipeline run**: yfinance fetches entire stock history → stored in DuckDB
2. **Daily runs after**: checks `MAX(date)` in DB, fetches only the gap → 1-5 rows
3. **Chart UI**: user clicks 1D/1W/1M/3M/1Y/ALL → chart re-fetches with different `?days=N`
4. **No stale re-downloads**: the daily guard + incremental fetch means zero wasted API calls
