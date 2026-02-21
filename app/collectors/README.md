# Data Collectors

This folder contains all data collection modules for the trading bot pipeline.

## Collectors

| Collector | Source | Data | Rate Limit |
|-----------|--------|------|------------|
| `yfinance_collector.py` | Yahoo Finance API | Price history, fundamentals, analyst data, insider activity | yfinance internal |
| `reddit_collector.py` | Reddit public JSON API | Trending tickers from financial subreddits | ~2 req/sec |
| `youtube_collector.py` | YouTube + transcript API | Video transcripts for ticker mentions | YouTube TOS |
| `news_collector.py` | Various news sites | Financial news articles | Varies |
| `sec_13f_collector.py` | SEC EDGAR API | Institutional 13F holdings (hedge fund positions) | 10 req/sec |
| `congress_collector.py` | Senate eFD system | Congressional stock trades | 2s between reqs |

## SEC 13F Collector (`sec_13f_collector.py`)

Scrapes institutional holdings from SEC EDGAR's submissions API.

**Endpoints:**

- `data.sec.gov/submissions/CIK{cik}.json` — filing index
- `www.sec.gov/Archives/edgar/data/{cik}/...` — filing documents

**Configuration:**

- `SEC_USER_AGENT` env var (required by SEC EDGAR)
- Default watchlist: 15 major hedge funds (Berkshire, Citadel, Renaissance, etc.)

**DuckDB Tables:** `sec_13f_filers`, `sec_13f_holdings`

## Congressional Trades Collector (`congress_collector.py`)

Scrapes the Senate Electronic Financial Disclosure system.

**Endpoints:**

- `efdsearch.senate.gov/search/home/` — CSRF + terms agreement
- `efdsearch.senate.gov/search/report/data/` — report listing API

**Configuration:** No API key needed. Uses CSRF token + session cookie.

**DuckDB Table:** `congressional_trades`
