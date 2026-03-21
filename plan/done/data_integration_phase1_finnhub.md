# Data Integration Architecture — Master Plan

Build out **6 phases** of data infrastructure for the trading bot. Each phase is self-contained and shippable independently.

---

## Current State (What Already Exists)

| Component | Status | File |
|---|---|---|
| Congress trades | ✅ Working | `congress_service.py` — Senate eFD scraper, 90-day window, DuckDB |
| SEC 13F holdings | ✅ Working | `sec_13f_service.py` — 15 default filers, quarterly backfill |
| OpenBB SDK | ✅ Integrated | `openbb_service.py` — lazy-load, 11 data methods, `collect_all()` |
| yFinance | ✅ Working | `yfinance_service.py` — price/fundamentals/financials/analyst/insider |
| Reddit scraper | ✅ Working | `reddit_service.py` — 6-step pipeline, LLM filter, search |
| Data Distiller | ✅ Working | `data_distiller.py` — 8 distill methods, LLM-ready summaries |
| Scheduler | ✅ Working | `scheduler.py` — pre-market, midday, EOD automation |
| Finnhub | ❌ Not started | No code exists |
| Alert system | ❌ Not started | No code exists |
| Custom data bucket | ❌ Not started | No code exists |
| Multi-market (intl/crypto/commodities/futures) | ❌ Not started | OpenBB has the capability but not wired |

---

## Data Source Strategy (Resolving Redundancy)

**Core principle**: Each source provides UNIQUE data. No source duplicates another.

| Data Type | Primary Source | Why NOT the others |
|---|---|---|
| **OHLCV price history** | yFinance | Free, unlimited history, no API key |
| **Real-time quotes** | Finnhub | WebSocket streaming, sub-second updates |
| **Company fundamentals** | yFinance | Richest `.info` dict, no key needed |
| **Financial statements** | yFinance | Multi-year income/balance/cashflow built-in |
| **Analyst recommendations** | Finnhub | More granular than yFinance (individual upgrades/downgrades) |
| **Earnings surprises** | Finnhub | Beat/miss history, EPS surprise %, yFinance doesn't have this |
| **Insider sentiment** | Finnhub | Aggregated MSPR score, yFinance only has raw transactions |
| **Company news** | Finnhub | Real-time, category-tagged, sentiment scores |
| **SEC filings (13F)** | SEC EDGAR direct | Already built, more control than OpenBB |
| **Congressional trades** | Senate eFD direct | Already built, OpenBB's coverage is spotty |
| **International stocks** | OpenBB | Multi-exchange support (LSE, TSE, XETRA) |
| **Commodities** | OpenBB | Gold, oil, agriculture via FRED/Yahoo |
| **Futures** | OpenBB | CME data via multiple providers |
| **Crypto** | OpenBB | CoinGecko/Yahoo crypto provider |
| **Economic indicators** | OpenBB | FRED series (GDP, CPI, rates) — already coded |
| **Options chain** | OpenBB | Already coded, yFinance fallback |
| **Reddit sentiment** | Direct scraper | Already built, richer than any API |

---

## Phase 1: Finnhub Integration + Data Source Router

Finnhub fills 4 gaps: real-time quotes, earnings surprises, aggregated insider sentiment (MSPR), and granular analyst changes.

- **NEW** `finnhub_service.py` — 7 methods with DuckDB daily guards
- **NEW** `data_source_router.py` — Central source selection per data type
- **MODIFY** `config.py` — Add `FINNHUB_API_KEY`, `FINNHUB_RATE_LIMIT`

## Phase 2: Smart Money Alert System

Detect when tracked funds change positions, congress members trade, or institutional behavior is abnormal.

- **NEW** `alert_service.py` — Detection methods + alert management
- **NEW** DB tables: `alerts`, `tracked_funds`
- **MODIFY** `scheduler.py` — Add `alert_check` job (7 AM + 5 PM ET)
- **MODIFY** `ws_broadcaster.py` — Add `broadcast_alert()`

## Phase 3: Custom Data Collaboration Bucket

User drops files into a folder → auto-classify → parse → LLM-ready.

- **NEW** `custom_data_service.py` — File handlers for CSV/JSON/TXT/PDF
- **NEW** DB table: `custom_data`
- **NEW** API endpoints: upload, list, delete

## Phase 4: Reddit Scraping Improvements

- Per-thread sentiment scoring (keyword ratios)
- Subreddit-level aggregation
- Thread freshness weighting (exponential decay)
- OAuth support via PRAW (60 req/min)
- Expanded subreddit list (crypto, international)

## Phase 5: OpenBB Multi-Market Integration

Wire existing OpenBB capabilities for international, commodities, futures, crypto.

- **MODIFY** `openbb_service.py` — Add 7 new methods
- **MODIFY** `data_distiller.py` — Add `distill_macro_context()`, `distill_crypto()`
- **NEW** DB tables: `commodity_prices`, `crypto_prices`

## Phase 6: LLM Data Feed Pipeline

Wire all new sources into the LLM analysis chain.

- **MODIFY** `data_distiller.py` — 5 new distill methods
- **MODIFY** `deep_analysis_service.py` — Include all new data in dossier

## Build Order

P1 → P4 → P2 → P3 → P5 → P6

- P1 first: data source router affects how all collectors wire up
- P4 next: low-risk improvement to existing code  
- P2 before P3: alerts are higher-impact
- P5 before P6: all sources must exist before wiring into LLM
