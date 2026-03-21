# Database Separation: DuckDB (Financial) + MongoDB (Bot/Trading)

## Principle

- **DuckDB** = Financial data + analytical queries. "Is the data collection working?"
- **MongoDB** = Bot state + trading behavior. "Is the bot behaving correctly?"

## Current State — Overlap Map

> [!CAUTION]
> The same data exists in BOTH databases for many tables. This causes bugs — the portfolio fix earlier was caused by `portfolioService.js` reading from MongoDB while Python wrote to DuckDB.

### Data currently in BOTH (needs deduplication)

| Data | DuckDB Table | MongoDB Collection | Source of Truth |
|------|-------------|-------------------|-----------------|
| Watchlist | `watchlist` | `watchlist` | → **MongoDB** |
| Orders | `orders` | `orders` | → **MongoDB** |
| Positions | `positions` | `portfolio` | → **MongoDB** |
| Trade decisions | `trade_decisions` | `trade_decisions` | → **MongoDB** |
| Bots | `bots` | `bots` | → **MongoDB** |
| Discovered tickers | `discovered_tickers` | `discovery_results` | → **MongoDB** |
| Ticker scores | `ticker_scores` | `ticker_scores` | → **MongoDB** |
| Dossiers | `ticker_dossiers` | `dossiers` | → **MongoDB** |
| News | `news_articles` | `news` | → **DuckDB** |
| Technicals | `technicals` | `technicals` | → **DuckDB** |
| Fundamentals | `fundamentals` | `stocks` | → **DuckDB** |
| Pipeline events | `pipeline_events` | `pipeline_events` | → **MongoDB** |
| Scorecards | `quant_scorecards` | `scorecards` | → **DuckDB** |

### Keep in DuckDB (financial/analytical — Python writes, API reads for dashboards)

| Table | Purpose | Rows |
|-------|---------|------|
| `price_history` | OHLCV candles | 183K |
| `technicals` | 154 pandas-ta indicators | 189K |
| `fundamentals` | Ticker info snapshots | 108 |
| `financial_history` | Multi-year income statements | 107 |
| `balance_sheet` | Multi-year balance sheets | 115 |
| `cash_flows` | Multi-year cash flows | 113 |
| `analyst_data` | Price targets + recommendations | 78 |
| `insider_activity` | Insider transactions | 78 |
| `earnings_calendar` | Earnings dates | 78 |
| `risk_metrics` | Quant risk metrics | 73 |
| `quant_scorecards` | QuantSignalEngine flags | 25 |
| `news_articles` | All news sources | 669 |
| `youtube_transcripts` | YouTube transcripts | 82 |
| `news_full_articles` | Full RSS articles | 72 |
| `sec_13f_filers` | Institutional filer registry | 15 |
| `sec_13f_holdings` | 13F holdings data | 148 |
| `congressional_trades` | Congress stock trades | 224 |
| `embeddings` | RAG text embeddings | 1635 |
| `llm_audit_logs` | LLM call audit trail | 473 |
| `llm_conversations` | Conversation records | 120 |
| `pipeline_telemetry` | Tool execution timing | 156 |
| `source_credibility` | Source reliability scores | — |
| `benchmark_stats` | Performance benchmarks | — |

### Keep in MongoDB (bot behavior/trading — Node.js writes, frontend reads)

| Collection | Purpose |
|-----------|---------|
| `bots` | Registered bot models + stats |
| `config` | LLM config, risk params, strategy |
| `portfolio` | Open/closed positions per bot |
| `orders` | Order history per bot |
| `watchlist` | Active watchlist entries |
| `discovery_results` | Discovered tickers from scanning |
| `ticker_scores` | Scored tickers from discovery |
| `dossiers` | Deep analysis dossiers |
| `trade_decisions` | LLM trading decisions |
| `trades` | Executed trades |
| `pipeline_events` | Activity tab events (real-time) |
| `exclusions` | User-excluded tickers |
| `reddit_threads` | Reddit thread data |
| `transcripts` | Transcript documents |
| `market_data` | Real-time market snapshots |
| `scorecards` | Copy of quant scorecards for frontend |

### Remove from DuckDB (duplicated, MongoDB is source of truth)

- `watchlist`, `orders`, `positions`, `bots`
- `trade_decisions`, `trade_executions`, `portfolio_snapshots`
- `discovered_tickers`, `ticker_scores`, `ticker_dossiers`
- `pipeline_events` (move to MongoDB-only)
- `scheduler_runs`, `reports`, `pipeline_workflows`
- `reddit_threads`, `user_exclusions`, `rejected_symbols`, `ticker_blacklist`
- `circuit_breaker_state`, `model_logic_loops`, `bot_audit_reports`

---

## Migration Phases

### Phase 1: Stop writing duplicates (low risk)
Remove DuckDB writes for tables that MongoDB already handles:
- `pipeline_service.py` → stop writing to DuckDB `watchlist`, `positions`, `orders`
- `event_logger.py` → stop writing to DuckDB `pipeline_events` (MongoDB handles this)
- `decision_logger.py` → stop writing to DuckDB `trade_decisions`

### Phase 2: Update Python API endpoints
Endpoints in `main.py` that read from DuckDB duplicates → read from MongoDB instead:
- Watchlist endpoints → MongoDB
- Portfolio/positions endpoints → MongoDB
- Trade decisions endpoints → MongoDB
- Pipeline events → MongoDB

### Phase 3: Update audit script
`full_pipeline_audit.py` → only check DuckDB financial tables, check MongoDB for bot tables.

### Phase 4: Drop orphan DuckDB tables
Remove the ~18 DuckDB tables that are now MongoDB-only.

---

## Verification Plan

### After Phase 1
- Run trading bot → confirm no DuckDB write errors
- Run `--baseline` audit → confirm financial tables still populated

### After Phase 2  
- Test all API endpoints via frontend → confirm dashboard still works
- Verify Autobot Monitor, Portfolio, Activity tab all load

### After Phase 4
- Run full audit → should pass with only DuckDB financial tables
- Verify MongoDB has all bot/trading data intact
