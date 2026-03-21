# Database Separation — Phase 1 Complete

## Done
- `event_logger.py` — disabled DuckDB INSERT for `pipeline_events` (WebSocket broadcast preserved)
- `decision_logger.py` — disabled DuckDB INSERT for `trade_decisions` + `trade_executions`

## Found: Two Separate Trading Systems
- **Python `paper_trader.py`** — 777-line DuckDB-based trading engine (positions, orders, snapshots, triggers)
- **Node.js `tradingAgent.js` + `portfolioService.js`** — MongoDB-based trading system

These are completely separate. The Node.js system is what runs in the autonomous loop.

## Blocked (Needs Phase 2)
The following files have deep **read+write** DuckDB dependencies and cannot be disabled without migrating reads to MongoDB first:
- `paper_trader.py` (positions, orders, portfolio_snapshots, price_triggers)
- `bot_registry.py` (bots table — leaderboard, stats)
- `watchlist_manager.py` (watchlist — entire pipeline reads this)
- `discovery_service.py` (discovered_tickers, ticker_scores — scorer reads/writes)
