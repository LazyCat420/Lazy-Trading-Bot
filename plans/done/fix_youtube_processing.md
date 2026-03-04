# Fix: YouTube Transcript Processing Issues

## Root Cause Analysis

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| `scanned_for_tickers=False` on all samples | Only 5/704 are actually unscanned; the DB diagnostic queried newest rows which happened to be unscanned | Scanner IS running — 699/704 already processed |
| 0 LLM audit log entries | `ticker_scanner.py` called `llm.chat()` without `audit_step` kwarg, so logs had empty `agent_step` | Added `audit_step="youtube_ticker_scan"` |
| `ticker=NEXT` false positive | RSS scraper extracted "NEXT" as a ticker from article titles → discovery → YouTube search | Added `NEXT` to RSS exclusion list |
| Only 3 discovery tickers from 699 scans | yfinance validation rejects most LLM-extracted tickers (expected behavior, not a bug) | Audit logging now enables debugging extraction quality |

## Files Changed

- `app/services/ticker_scanner.py` — Added `audit_step` + `audit_ticker` kwargs to LLM call
- `app/services/rss_news_service.py` — Expanded exclusion list (includes NEXT, CNBC, WSJ, etc.)
- `app/services/news_service.py` — SEC EDGAR rich summaries

## Verification

- All 3 files pass `ruff format` and `ruff check`  
- DB confirms 699/704 transcripts scanned  
- Next discovery run will log all LLM calls under `youtube_ticker_scan` agent_step
