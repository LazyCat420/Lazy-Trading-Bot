# 03 — Collectors Folder Refactor

## Current State

```
app/collectors/
├── README.md                  (1.7 KB)
├── __init__.py
├── congress_collector.py      (17 KB)  — Fetches congressional stock trades via QuiverQuant
├── news_collector.py          (15 KB)  — Google News scraping for ticker news
├── reddit_collector.py        (17 KB)  — Reddit public JSON API ticker scraping
├── risk_computer.py           (16 KB)  — Computes risk metrics (Sharpe, VaR, MaxDD, etc.)
├── rss_news_collector.py      (17 KB)  — RSS feed news aggregation
├── sec_13f_collector.py       (33 KB)  — SEC EDGAR 13F institutional holdings
├── technical_computer.py      (18 KB)  — Computes 154 technical indicators via pandas-ta
├── ticker_scanner.py          (9 KB)   — Utility: scans text for stock ticker mentions
├── ticker_validator.py        (17 KB)  — LLM-assisted ticker validation (is "AI" a real ticker?)
├── yfinance_collector.py      (42 KB)  — The big one: OHLCV, fundamentals, financials, balance sheet, etc.
├── youtube_collector.py       (29 KB)  — YouTube search + transcript extraction
```

## What Each File Does

| File | Type | External API | Used By |
|------|------|-------------|---------|
| `yfinance_collector.py` | Data fetcher | yfinance (Yahoo Finance) | `pipeline_service.py`, `quant_signals.py` |
| `technical_computer.py` | Computation | pandas-ta (local) | `pipeline_service.py` |
| `risk_computer.py` | Computation | numpy (local) | `pipeline_service.py` |
| `news_collector.py` | Data fetcher | Google News RSS | `pipeline_service.py` |
| `rss_news_collector.py` | Data fetcher | Various RSS feeds | `discovery_service.py` |
| `youtube_collector.py` | Data fetcher | YouTube + youtube-transcript-api | `pipeline_service.py`, `discovery_service.py` |
| `reddit_collector.py` | Data fetcher | Reddit public JSON | `discovery_service.py` |
| `congress_collector.py` | Data fetcher | QuiverQuant API | `discovery_service.py` |
| `sec_13f_collector.py` | Data fetcher | SEC EDGAR | `discovery_service.py` |
| `ticker_scanner.py` | Utility | None | `reddit_collector.py`, `youtube_collector.py` |
| `ticker_validator.py` | LLM utility | Ollama + yfinance | `discovery_service.py` |

### The Problem

1. **"Collectors" is a confusing name.** Half of them collect data from APIs, the other half compute derived data locally. `risk_computer.py` and `technical_computer.py` are not collectors — they are computers/calculators.

2. **They already ARE services.** The `refactor.md` correctly notes these are "just data-fetching scripts." They follow the same pattern as files already in `app/services/` (standalone async classes with no state).

3. **Having a separate `collectors/` folder adds mental overhead.** When debugging "where does price data come from?", you have to check both `services/` and `collectors/`. Consolidating makes the codebase greppable.

4. **The `utils/` folder has related code.** `market_hours.py` is a utility that collectors use. Moving collectors into services means utils can stay lean.

---

## Proposed Refactor

### Goal: Move all collectors into `app/services/` and rename the computers for clarity

### Rename & Move Plan

| Current Path | New Path | Rename Reason |
|-------------|----------|---------------|
| `collectors/yfinance_collector.py` | `services/yfinance_service.py` | It's a service that fetches Yahoo Finance data |
| `collectors/technical_computer.py` | `services/technical_service.py` | Computes technicals — "service" is clearer |
| `collectors/risk_computer.py` | `services/risk_service.py` | Computes risk metrics — "service" is clearer |
| `collectors/news_collector.py` | `services/news_service.py` | Fetches news from Google |
| `collectors/rss_news_collector.py` | `services/rss_news_service.py` | Fetches RSS news |
| `collectors/youtube_collector.py` | `services/youtube_service.py` | Fetches YouTube data |
| `collectors/reddit_collector.py` | `services/reddit_service.py` | Fetches Reddit data |
| `collectors/congress_collector.py` | `services/congress_service.py` | Fetches congressional trade data |
| `collectors/sec_13f_collector.py` | `services/sec_13f_service.py` | Fetches SEC 13F data |
| `collectors/ticker_scanner.py` | `services/ticker_scanner.py` | Utility, keep name |
| `collectors/ticker_validator.py` | `services/ticker_validator.py` | Utility, keep name |
| `collectors/README.md` | **DELETE** | Will be outdated after move |
| `collectors/__init__.py` | **DELETE** | Folder removed |

### Import Updates Required

Every file that imports from `app.collectors.*` needs updating:

| Importing File | Current Import | New Import |
|---------------|----------------|------------|
| `services/pipeline_service.py` | `from app.collectors.yfinance_collector import ...` | `from app.services.yfinance_service import ...` |
| `services/pipeline_service.py` | `from app.collectors.technical_computer import ...` | `from app.services.technical_service import ...` |
| `services/pipeline_service.py` | `from app.collectors.risk_computer import ...` | `from app.services.risk_service import ...` |
| `services/pipeline_service.py` | `from app.collectors.news_collector import ...` | `from app.services.news_service import ...` |
| `services/pipeline_service.py` | `from app.collectors.youtube_collector import ...` | `from app.services.youtube_service import ...` |
| `services/discovery_service.py` | `from app.collectors.reddit_collector import ...` | `from app.services.reddit_service import ...` |
| `services/discovery_service.py` | `from app.collectors.youtube_collector import ...` | `from app.services.youtube_service import ...` |
| `services/discovery_service.py` | `from app.collectors.congress_collector import ...` | `from app.services.congress_service import ...` |
| `services/discovery_service.py` | `from app.collectors.sec_13f_collector import ...` | `from app.services.sec_13f_service import ...` |
| `services/discovery_service.py` | `from app.collectors.rss_news_collector import ...` | `from app.services.rss_news_service import ...` |
| `services/discovery_service.py` | `from app.collectors.ticker_validator import ...` | `from app.services.ticker_validator import ...` |
| `engine/quant_signals.py` | (if any collector imports) | Update accordingly |
| Various test files | `from app.collectors.*` | `from app.services.*` |

---

## Step-by-Step Execution Order

1. Move all 11 `.py` files from `app/collectors/` → `app/services/` with new names
2. Find and replace all `app.collectors.` imports across the entire codebase
3. Delete the `app/collectors/` folder
4. Verify no broken imports with `python -c "from app.services import ..."` for each moved file
5. Run ruff linter

## Risk Assessment

**Low risk.** This is a pure file move + rename. No logic changes. All tests should pass with just import updates.

## Files Affected

- **MOVE+RENAME:** 11 files from `collectors/` → `services/`
- **DELETE:** `app/collectors/` folder, `README.md`, `__init__.py`
- **MODIFY:** `app/services/pipeline_service.py` (import paths)
- **MODIFY:** `app/services/discovery_service.py` (import paths)
- **MODIFY:** `app/services/deep_analysis_service.py` (if it imports collectors)
- **MODIFY:** All test files that import collectors
