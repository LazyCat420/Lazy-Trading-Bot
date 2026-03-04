# News & Data Pipeline — Step-by-Step Verification Checklist

This document maps every step of the news/data collection pipeline from source → LLM decision.
We verify each step one at a time.

---

## Pipeline Architecture Overview

```
autonomous_loop.run_full_loop()
  │
  ├─ Phase 1: DISCOVERY  (find new tickers)
  │   ├─ Reddit scraper   → scored tickers
  │   ├─ YouTube scraper   → general market transcripts → LLM ticker extraction
  │   ├─ SEC 13F filings   → institutional holdings
  │   ├─ Congress trades   → politician stock trades
  │   └─ RSS News scraper  → CNBC/MarketWatch/Yahoo/Benzinga/Investing.com → ticker extraction
  │
  ├─ Phase 2: IMPORT  (top N discovery tickers → watchlist)
  │
  ├─ Phase 3: COLLECTION  (PipelineService.run(ticker, mode='data'))
  │   ├─ Steps 1-9:  yfinance data (price, fundamentals, financials, etc.)
  │   ├─ Step 4:     Technical indicators (pandas-ta, depends on price)
  │   ├─ Step 10:    Risk metrics (depends on price)
  │   ├─ Step 11:    Per-ticker NEWS (yfinance + Google News RSS + SEC EDGAR) → DuckDB
  │   └─ Step 12:    Per-ticker YouTube (yt-dlp transcripts) → DuckDB
  │
  ├─ Phase 4: DEEP ANALYSIS  (per ticker)
  │   ├─ Layer 1: QuantSignalEngine.compute()  → QuantScorecard (pure math)
  │   └─ Layer 2: DataDistiller (pure Python) → text summaries for LLM
  │       ├─ distill_price_action()  (reads price_history + technicals from DB)
  │       ├─ distill_fundamentals()  (reads fundamentals from DB)
  │       └─ distill_risk()          (reads risk_metrics from DB)
  │
  └─ Phase 5: TRADING  (TradingPipelineService)
      ├─ _build_context() — loads dossier + portfolio data
      └─ TradingAgent.decide() — one LLM call per ticker → BUY/SELL/HOLD
```

---

## Verification Checklist

### Phase 1: Discovery Sources

- [ ] **1.1 — RSS News Scraper (`rss_news_service.py`)**
  - Does `scrape_all_feeds()` successfully fetch from all 5 RSS feeds?
  - Are articles being stored in `news_full_articles` table?
  - Does `_extract_article_content()` (newspaper3k) extract full text?
  - Does `_extract_tickers_from_text()` find real tickers?
  - **Files:** `app/services/rss_news_service.py`
  - **DB table:** `news_full_articles`

- [ ] **1.2 — YouTube General Market Scraper**
  - Does `collect_general_market()` find new videos?
  - Are transcripts stored in `youtube_transcripts` table?
  - Does the LLM ticker extraction work? (Bug #3 dict fix was applied)
  - **Files:** `app/services/youtube_service.py`, `app/services/ticker_scanner.py`
  - **DB table:** `youtube_transcripts`

- [ ] **1.3 — Reddit Scraper**
  - Does `collect()` pull from financial subreddits?
  - Are tickers scored and validated via yfinance?
  - **Files:** `app/services/reddit_service.py`

- [ ] **1.4 — SEC 13F + Congress**
  - Are institutional holdings and congress trades being fetched?
  - Known issue: CIK0001116304 returns 404
  - **Files:** `app/services/sec_13f_service.py`, `app/services/congress_service.py`

### Phase 2: Import to Watchlist

- [ ] **2.1 — Discovery → Watchlist Import**
  - Do discovered tickers make it into `watchlist` table correctly?
  - Is deduplication working? (Audit found BEPC 7 times active)
  - **Files:** `app/services/watchlist_manager.py`

### Phase 3: Per-Ticker Data Collection

- [ ] **3.1 — Step 11: Per-Ticker News Collection (`news_service.py`)**
  - Does `NewsCollector.collect(ticker)` fetch from all 3 sources?
    - yfinance `.news` property
    - Google News RSS (search by ticker)
    - SEC EDGAR full-text search
  - Are articles stored in `news_articles` table with dedup?
  - Does `get_all_historical(ticker)` return accumulated history?
  - **Files:** `app/services/news_service.py`
  - **DB table:** `news_articles`

- [ ] **3.2 — Step 12: Per-Ticker YouTube Collection**
  - Does `YouTubeCollector.collect(ticker)` find ticker-specific videos?
  - Are transcripts stored and retrievable?
  - **Files:** `app/services/youtube_service.py`
  - **DB table:** `youtube_transcripts`

- [ ] **3.3 — Step 14c: RSS Articles Matched to Ticker**
  - Does `rss_news.get_articles_for_ticker(ticker)` find matching articles?
  - Is the ticker matching logic accurate? (text search vs exact match)
  - **Files:** `app/services/rss_news_service.py`

### Phase 4: Data Distillation (News → LLM-Ready Text)

- [ ] **4.1 — DataDistiller Price Analysis**
  - Does `distill_price_action()` produce real output? (Bug #1 was fixed)
  - Are trend regime, crossovers, divergences, S/R zones populated?
  - **Files:** `app/services/data_distiller.py`

- [ ] **4.2 — DataDistiller Fundamentals**
  - Does `distill_fundamentals()` include valuation, revenue, cash flow?
  - Note: `financial_history`, `balance_sheet`, `cashflow` are passed as empty `[]`
  - **Files:** `app/services/deep_analysis_service.py:128-131`

- [ ] **4.3 — Dossier News Fields**
  - Does the `TickerDossier` actually contain news text?
  - Currently: `executive_summary = price_analysis[:500]`, `bull_case = fund_analysis[:300]`, `bear_case = risk_analysis[:300]`
  - **No news headlines or article summaries are in the dossier at all**
  - **Files:** `app/services/deep_analysis_service.py:133-143`

### Phase 5: News → LLM Decision Context

- [ ] **5.1 — TradingPipelineService._build_context()**
  - What fields from the dossier reach the LLM?
  - Is any news text actually piped to the trading agent?
  - **Files:** `app/services/trading_pipeline_service.py`

- [ ] **5.2 — TradingAgent._build_prompt()**
  - Does the prompt include a NEWS section?
  - What does the LLM actually see for each ticker?
  - **Files:** `app/services/trading_agent.py`

---

## Status: Step 1.1 checked first (below)
