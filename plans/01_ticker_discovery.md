# Phase 1 — Ticker Discovery Pipeline

> **Goal**: Automatically find trending stock tickers from YouTube transcripts and Reddit,
> score them by conviction, validate they are real tradeable symbols, and feed them
> into the watchlist system.

---

## 1.1 — YouTube Transcript Ticker Scanner

### What it does

Scans YouTube transcripts already stored in DuckDB (`youtube_transcripts` table)
for mentions of stock tickers. These transcripts are collected by the existing
`youtube_collector.py` from curated finance channels.

### How it works

```
youtube_transcripts (DuckDB)
        │
        ▼
┌──────────────────────┐
│  Transcript Scanner  │
│  (app/collectors/    │
│   ticker_scanner.py) │
└──────────┬───────────┘
           │
    ┌──────▼──────┐
    │  LLM Pass   │  "Extract all stock tickers mentioned in this transcript"
    │  (batch)    │  Returns: [{ticker, context_snippet, sentiment_hint}]
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │  Scorer     │  Weight by: source reputation, mention count, recency
    └──────┬──────┘
           │
    Scored Ticker Candidates
```

### Implementation Details

#### New file: `app/collectors/ticker_scanner.py`

```python
class TickerScanner:
    """Extracts ticker mentions from YouTube transcripts stored in DuckDB."""

    async def scan_recent_transcripts(self, hours: int = 24) -> list[ScoredTicker]:
        """
        1. Query DuckDB for transcripts from the last N hours
        2. For each transcript, chunk into ~2000 char segments
        3. Send each chunk to LLM with extraction prompt
        4. Collect raw ticker mentions with context snippets
        5. Validate with yfinance (fast_info)
        6. Return scored list
        """

    def _build_extraction_prompt(self, text_chunk: str) -> str:
        """
        Prompt asks LLM to extract:
        - Ticker symbol (e.g., NVDA, TSLA)
        - Brief context (why mentioned — bullish/bearish catalyst?)
        - Conviction hint (strong mention vs passing reference)
        """
```

#### Scoring Formula

Each ticker gets a **discovery score** based on:

| Factor | Weight | Description |
|--------|--------|-------------|
| `mention_count` | 1 pt each | Raw number of times mentioned across all transcripts |
| `title_mention` | +3 pts | Ticker appears in the video title |
| `channel_trust` | ×1.5 | From a curated/trusted channel (already in config) |
| `recency` | ×decay | Score decays: 1.0 (today) → 0.5 (yesterday) → 0.25 (2d ago) |
| `sentiment_hint` | +2 / -1 | LLM says bullish (+2) or bearish (-1) |

#### New model: `app/models/discovery.py`

```python
class ScoredTicker(BaseModel):
    ticker: str
    discovery_score: float
    sources: list[str]           # ["youtube:channel_name", "reddit:r/wallstreetbets"]
    first_seen: datetime
    last_seen: datetime
    sentiment_hint: str          # "bullish" / "bearish" / "neutral"
    context_snippets: list[str]  # Brief excerpts explaining WHY mentioned
```

---

## 1.2 — Reddit Scraper

### What it does

Scans financial subreddits for trending ticker mentions using Reddit's public
JSON API (no auth needed). Based on the `RedditPurgeScraper` reference implementation
in `example_repos/`.

### How it works

```
Reddit Public JSON API
        │
        ▼
┌──────────────────────┐
│  Step 1: Get Priority │  Stickied "Daily Discussion" / "Moves Tomorrow" threads
│  Threads (Hot/Sticky) │  from r/wallstreetbets, r/stocks, r/pennystocks, r/options
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  Step 2: Get Rising   │  Fresh candidates from r/wallstreetbets, r/pennystocks,
│  Candidates           │  r/ShortSqueeze, r/options
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  Step 3: LLM Filter   │  Send batch of titles to LLM
│  (Thread Selection)   │  → Pick threads likely discussing stock catalysts
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  Step 4: Deep Scrape  │  For each selected thread:
│  (Title + Body +      │  → Fetch title, selftext, top 30 comments
│   Comments)           │  → Extract tickers via regex + exclusion list
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│  Step 5: Validate     │  yfinance history(period='1d') check
│  & Score              │  → Weighted scoring (title=3, body=2, comment=1)
└──────────┬───────────┘
           │
    Scored Ticker Candidates
```

### Implementation Details

#### New file: `app/collectors/reddit_collector.py`

Adapted from `example_repos/RedditPurgeScraper/bot.py` but integrated into
the existing collector pattern.

```python
class RedditCollector:
    """Scrapes financial subreddits for trending ticker mentions."""

    # Configurable
    SUBREDDITS_PRIORITY = ["wallstreetbets", "stocks", "pennystocks", "options"]
    SUBREDDITS_TRENDING = ["wallstreetbets", "pennystocks", "ShortSqueeze", "options"]

    EXCLUSION_LIST = {
        "YOLO", "DD", "ATH", "IMO", "USA", "GDP", "CEO", "EOD", "SEC", "WSB",
        "OP", "EDIT", "AI", "TLDR", "THE", "US", "LOVE", "NOT", "FEED", "ON",
        "FOR", "AND", "OR", "IF", "BUT", "SO", "AT", "BY", "TO", "OF", "IN",
        "IT", "IS", "BE", "AS", "DO", "WE", "UP", "MY", "GO", "ME",
    }

    async def collect(self) -> list[ScoredTicker]:
        """Full pipeline: priority threads → trending → LLM filter → scrape → score."""

    def _get_subreddit_posts(self, sub: str, listing: str, limit: int) -> list[dict]:
        """Fetch posts via https://reddit.com/r/{sub}/{listing}.json"""

    def _filter_with_llm(self, candidates: list[dict]) -> list[dict]:
        """Send batch of titles to LLM, get indexes of promising threads."""

    def _get_thread_data(self, permalink: str) -> tuple[str, str, list[str]]:
        """Fetch full thread (title, body, top 30 comments) via JSON API."""

    def _extract_tickers(self, text: str) -> list[str]:
        """Regex extraction + exclusion list filtering."""

    def _validate_ticker(self, ticker: str) -> bool:
        """yfinance fast_info check — is this a real, active ticker?"""
```

Key differences from the reference `RedditPurgeScraper`:

- **Async**: Uses `asyncio` + `aiohttp` instead of blocking `requests`
- **Rate limiting**: Respects Reddit's 1 req/sec limit with backoff
- **LLM integration**: Uses our existing `LLMService` instead of raw Ollama calls
- **Persistence**: Saves results to DuckDB instead of JSON files
- **Scoring**: Uses same `ScoredTicker` model as YouTube scanner

---

## 1.3 — Ticker Validator

### What it does

Final validation layer before any ticker enters the watchlist.
Prevents noise words (AI, DD, CEO) and dead/delisted tickers.

### Three-Layer Validation

```
Candidate Ticker
      │
      ▼
┌─────────────────┐
│ 1. Exclusion    │  Hard-coded list of known noise words
│    List          │  (YOLO, DD, ATH, IMO, CEO, SEC, etc.)
└────────┬────────┘
         │ (pass)
┌────────▼────────┐
│ 2. yFinance     │  yf.Ticker(sym).fast_info → has last_price?
│    Validation    │  yf.Ticker(sym).history(period='1d') → not empty?
└────────┬────────┘
         │ (pass)
┌────────▼────────┐
│ 3. LLM Logic    │  "Is 'XX' a legitimate stock ticker or a common
│    Check         │  English word/abbreviation?" → True/False
└────────┬────────┘
         │ (pass)
    ✅ Validated Ticker
```

#### New file: `app/collectors/ticker_validator.py`

```python
class TickerValidator:
    """Three-layer validation: exclusion list → yfinance → LLM logic check."""

    def __init__(self):
        self._cache: dict[str, bool] = {}  # Memory cache per-run

    async def validate(self, ticker: str) -> bool:
        """Returns True if ticker is a real, actively traded stock."""

    async def validate_batch(self, tickers: list[str]) -> list[str]:
        """Validate multiple tickers in parallel, return valid ones."""
```

---

## 1.4 — DuckDB Persistence

### New tables

```sql
-- Stores discovery results from YouTube + Reddit
CREATE TABLE IF NOT EXISTS discovered_tickers (
    ticker          VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,      -- 'youtube' | 'reddit'
    source_detail   VARCHAR DEFAULT '',    -- channel name or subreddit
    discovery_score DOUBLE DEFAULT 0.0,
    sentiment_hint  VARCHAR DEFAULT 'neutral',
    context_snippet VARCHAR DEFAULT '',
    discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, source, discovered_at)
);

-- Aggregated scores (recalculated each run)
CREATE TABLE IF NOT EXISTS ticker_scores (
    ticker          VARCHAR PRIMARY KEY,
    total_score     DOUBLE DEFAULT 0.0,
    youtube_score   DOUBLE DEFAULT 0.0,
    reddit_score    DOUBLE DEFAULT 0.0,
    mention_count   INTEGER DEFAULT 0,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP,
    sentiment_hint  VARCHAR DEFAULT 'neutral',
    is_validated    BOOLEAN DEFAULT FALSE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 1.5 — API Endpoints

```
GET  /api/discovery/run          → Trigger a discovery scan (YouTube + Reddit)
GET  /api/discovery/results      → Get latest scored tickers
GET  /api/discovery/history      → Get discovery history with timestamps
```

---

## 1.6 — Frontend Integration

Add a **"Discovery" tab** on the dashboard showing:

- Top scored tickers from latest scan
- Source breakdown (YouTube vs Reddit)
- Context snippets (why each ticker is trending)
- One-click "Add to Watchlist" button
- "Run Discovery" button to trigger manual scan

---

## Testing Plan

1. **Unit tests** for `_extract_tickers()` regex logic
2. **Unit tests** for exclusion list filtering
3. **Integration test**: Mock Reddit JSON responses → verify scored output
4. **Integration test**: Mock LLM responses → verify thread filtering
5. **Live test**: Run against real Reddit/YouTube → verify no crashes, reasonable output
6. **Edge cases**: Empty subreddits, rate-limited responses, LLM timeout

## Dependencies

- `aiohttp` — async HTTP requests for Reddit scraping
- `fake-useragent` — rotate User-Agent headers to avoid blocks
- Existing: `yfinance`, `LLMService`, `DuckDB`
