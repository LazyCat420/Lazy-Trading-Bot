# Pipeline Refactor — Progressive Summarization Architecture

The trading bot collects 8 categories of data per ticker but only distills 3 (price, fundamentals, risk). News, YouTube, Reddit, 13F, Congress, analyst, insider, and earnings data are collected and stored to DB but never pre-digested for the LLM. This refactor adds 8 new distill methods + 1 cross-signal synthesizer to process all data sources before the LLM sees them.

## Proposed Changes

### Dead Variable Cleanup

#### [MODIFY] [pipeline_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/pipeline_service.py)
Remove 12 dead variable assignments (`analyst_data`, `insider_activity`, `earnings_calendar`) and their `# noqa: F841` suppressions in `run()` and `run_streaming()`.

---

### Schema + Model (Phase 1 — must come first)

#### [MODIFY] [dossier.py](file:///home/braindead/github/Lazy-Trading-Bot/app/models/dossier.py)
Add 9 new `str` fields to `TickerDossier` Pydantic model (all `default=""`): `news_analysis`, `youtube_analysis`, `smart_money_analysis`, `reddit_analysis`, `peer_analysis`, `analyst_consensus_analysis`, `insider_activity_analysis`, `earnings_catalyst_analysis`, `cross_signal_summary`.

#### [MODIFY] [database.py](file:///home/braindead/github/Lazy-Trading-Bot/app/database.py)
Add 9 idempotent `ALTER TABLE ticker_dossiers ADD COLUMN` statements (one per column, wrapped in try/except). DuckDB requires single-column ALTER.

#### [MODIFY] [deep_analysis_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/deep_analysis_service.py)
- `_store_dossier()`: Update INSERT from 12 → 21 columns
- `get_latest_dossier()`: Switch from `row[N]` indexing to `dict(zip(cols, row))`, add 9 new column names to SELECT

#### [MODIFY] [portfolio_strategist.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/portfolio_strategist.py)
- `_tool_get_dossier()`: Add 9 new fields to return dict, extend `data_gaps` checks, compute `target_upside_pct`

---

### Distill Methods (Phase 2)

#### [MODIFY] [data_distiller.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/data_distiller.py)
Add 9 new methods (pure transformers, `list[dict]` → `str`, no DB access):

| Method | Sources | Cap |
|---|---|---|
| `distill_news()` | `news_articles` + `news_full_articles` rows | 1500 |
| `distill_youtube()` | `youtube_transcripts` + `youtube_trading_data` rows | 1000 |
| `distill_smart_money()` | `sec_13f_holdings` + `congressional_trades` rows | 800 |
| `distill_reddit()` | `ticker_scores` + `discovered_tickers` rows | 500 |
| `distill_peers()` | peer vs primary fundamentals rows | 1000 |
| `distill_analyst_consensus()` | `analyst_data` rows (no `current_price`) | 500 |
| `distill_insider_activity()` | `insider_activity` rows (`json.loads(raw_transactions)`) | 500 |
| `distill_earnings_catalyst()` | `earnings_calendar` rows (incl. `previous_estimate`) | 500 |
| `distill_cross_signals()` | 11 `str` params (3 existing + 8 new distill outputs) | 1000 |

---

### Wire Into Analysis Pipeline (Phase 3)

#### [MODIFY] [deep_analysis_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/deep_analysis_service.py)
Add 11 new DB queries to `analyze_ticker()` (7 existing + 11 new = 18 total). Call all 8 distill methods + `distill_cross_signals()`. Truncate each to its char cap. Build `TickerDossier` with all new fields populated.

## Verification Plan

### Automated Tests
- `py_compile` all 6 modified files
- `python -m pytest` (if tests exist)
- Run `npm run lint` / `eslint` equivalent

### Manual Verification
- Start trading bot, trigger analysis for one ticker, verify dossier contains new fields
