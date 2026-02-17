# Phase 8: Data Collection Hardening

> Immediate fixes to make data collection production-ready.

## 8A — YouTube: 24-Hour Filter + Curated Channel List

### Problem

The current YouTube collector searches generic queries (`"{ticker} stock analysis"`) and pulls videos of any age. We need:

1. **Recency filter**: Only pull videos from the last 24 hours
2. **Curated channels**: Use the proven channel list from `Youtube-News-Extracter` (filtered to stock-relevant channels only)
3. **Cap at 3 videos** during testing

### Curated Channel List (Stock-Relevant Only)

Filtered from the example repo's `channels.json`, removing crypto-only and non-finance channels:

| Channel | Why |
|---------|-----|
| CNBC | Major financial news |
| CNBC Television | Live market coverage |
| Bloomberg Television | Global markets |
| Yahoo Finance | Market data + analysis |
| Wall Street Journal | Business/financial news |
| Financial Times | Global finance |
| Clear Value Tax | Market analysis |
| Deadnsyde | Stock picks + analysis |
| The Compound News | Market commentary |
| Aswath Damodaran | Valuation expert |
| Fundstrat | Institutional research |
| RealEismanPlaybook | Market analysis |

### Implementation

#### [MODIFY] `app/collectors/youtube_collector.py`

1. **Add `CURATED_CHANNELS` list** — channel names/IDs from above
2. **Add `--dateafter now-1d`** to the yt-dlp search command to only get last 24 hours
3. **Change search strategy**: Instead of generic `"{ticker} stock analysis"`, search each curated channel for the ticker:

   ```
   yt-dlp "ytsearch3:site:youtube.com {channel_name} {ticker}" --dateafter now-1d
   ```

4. **Also keep a general market search**: `"stock market news today"` — these channels often cover multiple tickers in one video

#### [NEW] `app/user_config/youtube_channels.json`

Configurable channel list so the user can add/remove channels without code changes.

---

## 8B — yFinance Data Verification

### Problem

The yFinance collector code exists but hasn't been tested end-to-end with real data. We need to:

1. Run each collector method (`collect_price_history`, `collect_fundamentals`, `collect_financial_history`) for NVDA
2. Verify data lands in DuckDB correctly
3. Print key metrics to console so the user can sanity-check values

### Implementation

#### [NEW] `tests/test_yfinance_live.py`

A live integration test (requires network) that:

- Collects price history for NVDA (last 30 days)
- Collects fundamentals
- Collects financial history
- Queries DuckDB and prints results to console
- Verifies row counts and key field values are non-zero

#### Console Output Format

```
═══ NVDA Price History ═══
Last 5 days:
  2026-02-14: O=134.21 H=135.80 L=133.50 C=135.12 V=42,000,000
  ...
Total rows stored: 252

═══ NVDA Fundamentals ═══
  Market Cap: $3.2T
  P/E (trailing): 65.2
  Revenue Growth: 122.4%
  Sector: Technology
  ...

═══ NVDA Financial History ═══
  2024: Rev=$60.9B, NI=$29.8B, GM=72.7%
  2023: Rev=$27.0B, NI=$4.4B, GM=56.9%
  ...
```

---

## Verification Plan

1. Run `ruff check app/` — must pass clean
2. Run `pytest tests/test_youtube_collector.py -v` — all pass
3. Run live YouTube test with 24-hour filter, verify only recent videos returned
4. Run live yFinance test, verify data in DB with console output
