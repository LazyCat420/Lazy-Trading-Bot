# 05 — Services + Pipeline Cleanup

## Current State

```
app/services/
├── __init__.py
├── autonomous_loop.py       (726 lines)  — Top-level orchestrator: Discovery → Collection → Analysis → Trading
├── bot_registry.py          (14 KB)      — Multi-bot management + leaderboard
├── deep_analysis_service.py (372 lines)  — Orchestrates the 4-Layer Analysis Funnel
├── discovery_service.py     (22 KB)      — Reddit + YouTube + SEC + Congress ticker discovery
├── event_logger.py          (4 KB)       — DuckDB event logging utility
├── llm_service.py           (50 KB)      — Ollama LLM wrapper + VRAM management
├── paper_trader.py          (29 KB)      — Paper trading: portfolio, orders, triggers
├── peer_fetcher.py          (2 KB)       — Fetches 2-3 sector peers for comparison
├── pipeline_health.py       (15 KB)      — Health monitoring dashboard
├── pipeline_service.py      (50 KB)      — 12-step data collection + 4 agent analysis pipeline
├── price_monitor.py         (6 KB)       — Monitors stop-loss / take-profit triggers
├── report_generator.py      (8 KB)       — Generates analysis reports
├── scheduler.py             (17 KB)      — APScheduler task scheduling
├── watchlist_manager.py     (16 KB)      — CRUD for the ticker watchlist
```

## The Problems in services/

### 1. `pipeline_service.py` (1095 lines) is massively bloated

This file does EVERYTHING:

- 12 data collection steps (prices, fundamentals, technicals, news, YouTube, etc.)
- 4 agent analysis runs
- Report aggregation via `Aggregator`
- Decision making via `RulesEngine`
- Report saving to disk
- Streaming progress events
- Has BOTH a `run()` (1-shot) and `run_streaming()` (SSE) method — ~500 lines duplicated

After the agents/engine refactor, this file needs to be dramatically simplified:

- Remove all agent imports and execution
- Remove `Aggregator` and `RulesEngine` imports
- The data collection steps (**keep**) should be the main job of this file
- Rename to make its purpose clear

### 2. `deep_analysis_service.py` orchestrates the 4-layer funnel

After the engine refactor (deleting layers 2-4), this file simplifies to:

- Run `QuantSignalEngine` (Layer 1 — keep)
- Run `DataDistiller` (keep)
- Package results into `TickerDossier`
- Store in DuckDB

That's it. No more `QuestionGenerator`, `RAGEngine`, or `DossierSynthesizer`.

### 3. `pipeline_service.py` vs `deep_analysis_service.py` overlap

Both services collect and analyze data. The difference:

- `pipeline_service.py` = **per-ticker synchronous pipeline** (used by frontend "Analyze" button)
- `deep_analysis_service.py` = **batch analysis service** (used by `autonomous_loop.py`)

After refactoring, these should be merged or at least have clear boundaries:

- **Collection** (fetching raw data from APIs) — one service
- **Analysis** (quant math + distilling) — one service
- **Trading** (strategist loop) — one service

### 4. `autonomous_loop.py` calls everything

This is the top-level orchestrator. After refactoring, its phases become:

1. **Discovery** — `discovery_service.py` (unchanged)
2. **Collection** — `pipeline_service.py` simplified (just data fetching)
3. **Analysis** — `deep_analysis_service.py` simplified (quant + distiller only)
4. **Trading** — `portfolio_strategist.py` (moved from engine)

---

## Proposed Refactor

### `pipeline_service.py` → Simplified Data Collector

**Strip out:**

- All agent imports (`TechnicalAgent`, `FundamentalAgent`, `SentimentAgent`, `RiskAgent`)
- All `Aggregator` usage
- All `RulesEngine` usage
- All `FinalDecision` model imports
- The `run_agent()` / `_run_agent_streaming()` inner functions
- The decision-making section at the bottom of `run()` and `run_streaming()`

**Keep:**

- The 12 data collection steps (prices, fundamentals, financials, balance sheet, cash flow, analysts, insiders, earnings, technicals, risk metrics, news, YouTube)
- The `_step()` / `_step_cached()` tracking helpers
- The `_save_reports()` method
- The streaming infrastructure

**Rename:** `pipeline_service.py` → Consider keeping name since it IS the data pipeline. Just make it clear it only collects data.

### `deep_analysis_service.py` → Simplified Analysis Service

**Strip out:**

- `QuestionGenerator` import and call (Layer 2)
- `RAGEngine` import and call (Layer 3)
- `DossierSynthesizer` import and call (Layer 4)

**Simplify to:**

```python
async def analyze_ticker(self, ticker, portfolio_context=None, bot_id=None):
    # Layer 1: Pure quant math
    scorecard = await self._quant.build_scorecard(ticker)
    
    # Layer 1.5: Data distillation (pure Python pre-analysis)
    prices, technicals, fundamentals, ... = fetch_from_db(ticker)
    price_analysis = self._distiller.distill_price_action(prices, technicals, scorecard)
    fund_analysis = self._distiller.distill_fundamentals(fundamentals, ...)
    risk_analysis = self._distiller.distill_risk(risk_metrics, scorecard)
    sentiment_analysis = self._distiller.distill_sentiment(news, transcripts)
    
    # Package into simplified TickerDossier
    dossier = TickerDossier(
        ticker=ticker,
        quant_scorecard=scorecard,
        price_action_analysis=price_analysis,
        fundamental_analysis=fund_analysis,
        risk_analysis=risk_analysis,
        sentiment_analysis=sentiment_analysis,
    )
    
    # Store in DuckDB
    self._store_dossier(dossier)
    return dossier
```

That's ~50 lines instead of ~145 lines. Zero LLM calls.

### `autonomous_loop.py` → Cleaner Phase Flow

After all refactors, the autonomous loop becomes:

```python
async def run_full_loop(self):
    # Phase 1: Discover tickers (Reddit + YouTube + SEC + Congress)
    await self._do_discovery()
    
    # Phase 2: Collect raw data for all tickers
    await self._do_collection()  # Uses simplified pipeline_service
    
    # Phase 3: Run quant analysis + data distillation
    await self._do_analysis()  # Uses simplified deep_analysis_service
    
    # Phase 4: Let the strategist trade
    await self._do_trading()  # Uses portfolio_strategist
```

Each phase is clean and does one thing.

---

## Other services/ files — No Changes Needed

| File | Status | Reason |
|------|--------|--------|
| `llm_service.py` | **KEEP + add JSON rescue utils** | Core LLM wrapper. Add rescue methods from base_agent |
| `paper_trader.py` | **KEEP as-is** | Paper trading engine. Clean. |
| `bot_registry.py` | **KEEP as-is** | Multi-bot management. Clean. |
| `discovery_service.py` | **KEEP, update imports** | Just update collector imports to new paths |
| `event_logger.py` | **KEEP as-is** | Simple utility |
| `peer_fetcher.py` | **KEEP as-is** | Small utility used by strategist |
| `pipeline_health.py` | **KEEP as-is** | Monitoring dashboard |
| `price_monitor.py` | **KEEP as-is** | Trigger execution |
| `report_generator.py` | **KEEP as-is** | Report generation |
| `scheduler.py` | **KEEP as-is** | Task scheduling |
| `watchlist_manager.py` | **KEEP as-is** | Watchlist CRUD |

---

## After All Refactors: Final `app/` Structure

```
app/
├── config.py                           — Settings (unchanged)
├── database.py                         — DuckDB connector (unchanged)
├── main.py                             — FastAPI app (update imports)
├── models/
│   ├── dossier.py                      — QuantScorecard, TickerDossier (simplified)
│   ├── market_data.py                  — OHLCVRow, FundamentalSnapshot, TechnicalRow, etc.
│   ├── trading.py                      — Position, Order, PortfolioSnapshot, PriceTrigger
│   ├── watchlist.py                    — WatchlistEntry, WatchlistSummary
│   └── discovery.py                    — ScoredTicker, DiscoveryResult
├── prompts/
│   └── portfolio_strategist.md         — The ONE prompt for the strategist
├── services/
│   ├── llm_service.py                  — Ollama LLM wrapper + JSON rescue utils
│   ├── autonomous_loop.py              — Top-level orchestrator
│   ├── pipeline_service.py             — Data collection (12 steps, no agents)
│   ├── deep_analysis_service.py        — Quant engine + data distiller
│   ├── portfolio_strategist.py         — Tool-calling LLM trader (from engine/)
│   ├── strategist_audit.py             — Strategist debug logger (from engine/)
│   ├── quant_engine.py                 — Pure math signals (from engine/)
│   ├── data_distiller.py               — Pure Python data pre-analysis (from engine/)
│   ├── paper_trader.py                 — Paper trading
│   ├── bot_registry.py                 — Multi-bot management
│   ├── discovery_service.py            — Ticker discovery
│   ├── yfinance_service.py             — Yahoo Finance data (from collectors/)
│   ├── technical_service.py            — Technical indicators (from collectors/)
│   ├── risk_service.py                 — Risk metrics (from collectors/)
│   ├── news_service.py                 — Google News (from collectors/)
│   ├── rss_news_service.py             — RSS feeds (from collectors/)
│   ├── youtube_service.py              — YouTube data (from collectors/)
│   ├── reddit_service.py               — Reddit data (from collectors/)
│   ├── congress_service.py             — Congressional trades (from collectors/)
│   ├── sec_13f_service.py              — SEC 13F data (from collectors/)
│   ├── ticker_scanner.py               — Ticker text scanner (from collectors/)
│   ├── ticker_validator.py             — LLM ticker validation (from collectors/)
│   ├── peer_fetcher.py                 — Sector peer lookup
│   ├── event_logger.py                 — Event logging
│   ├── pipeline_health.py              — Health monitoring
│   ├── price_monitor.py                — Price trigger monitor
│   ├── report_generator.py             — Report generation
│   ├── scheduler.py                    — Task scheduling
│   └── watchlist_manager.py            — Watchlist CRUD
├── templates/                          — Jinja2 templates (unchanged)
├── static/                             — Frontend assets (unchanged)
├── user_config/                        — User settings (unchanged)
└── utils/
    ├── logger.py                       — Logging config (unchanged)
    └── market_hours.py                 — Market hours utility (unchanged)
```

### Folder count: 7 → 5 (models, prompts, services, templates, static, user_config, utils)

### Deleted folders: `agents/`, `collectors/`, `engine/`

### Total files deleted: ~20

---

## Step-by-Step Execution Order

1. Simplify `deep_analysis_service.py` (remove Layer 2/3/4 calls)
2. Simplify `pipeline_service.py` (remove all agent + decision code)
3. Update `autonomous_loop.py` to use simplified services
4. Update `main.py` imports if needed
5. Run ruff + mypy across entire codebase
6. Run pytest to verify nothing is broken

## Files Affected

- **MODIFY:** `app/services/pipeline_service.py` (major simplification)
- **MODIFY:** `app/services/deep_analysis_service.py` (major simplification)
- **MODIFY:** `app/services/autonomous_loop.py` (update imports + phase flow)
- **MODIFY:** `app/main.py` (update imports if any reference deleted modules)
- **VERIFY:** All test files for broken imports
