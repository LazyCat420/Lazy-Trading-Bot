# Ticker Context Disambiguation — Fix False Positives for Ambiguous Tickers

Short words like "AI", "IT", "A", "ON", "GO" are valid US stock tickers **and** extremely common English words/acronyms. The current pipeline can't distinguish between "AI is the future" (talking about artificial intelligence) vs. "$AI is a buy" (talking about C3.ai stock). This causes false positives where common words get picked up as stock mentions.

## The Problem

| Word | Ticker? | Common usage? | Example false positive |
|------|---------|---------------|----------------------|
| AI | C3.ai ($AI) | "Artificial Intelligence" | "AI is transforming the market" |
| IT | Gartner ($IT) | "Information Technology" | "IT infrastructure spending" |
| A | Agilent ($A) | Article "a" | "a stock that could 10x" |
| ON | ON Semi ($ON) | Preposition "on" | "on the rise" |
| GO | Grocery Outlet ($GO) | Verb "go" | "go all in" |
| REAL | RealReal ($REAL) | Adjective "real" | "real value play" |
| ALL | Allstate ($ALL) | Pronoun "all" | "all stocks are down" |

## Solution: LLM Context Disambiguation Filter

Add a new **`ContextDisambiguator`** service that performs a focused LLM call to validate ambiguous tickers against their surrounding text. This runs **after** initial extraction and **before** DB persistence.

### Architecture

```
Extraction (regex/LLM) → Disambiguator (LLM context check) → Validation (yfinance) → DB
                              ↑
                     Only runs for tickers in AMBIGUOUS_TICKERS set
                     (non-ambiguous tickers skip this step)
```

---

### Core Component

#### [NEW] [ContextDisambiguator.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/ContextDisambiguator.py)

New service containing:
- `AMBIGUOUS_TICKERS`: curated `set[str]` of ~30 tickers that are also common English words (AI, IT, A, ON, GO, ALL, REAL, etc.)
- `disambiguate(tickers, source_text) → list[str]`: Takes extracted tickers + surrounding context text, sends ambiguous ones to LLM for a yes/no "is this actually referring to the stock?", returns only confirmed tickers.
- The LLM prompt asks: "Given this text snippet, is [TICKER] being discussed as a stock/company, or as a regular English word?"
- Batches all ambiguous tickers into a single LLM call for efficiency.
- Non-ambiguous tickers pass through untouched.

---

### Integration Points

#### [MODIFY] [symbol_filter.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/symbol_filter.py)

Add a new `AmbiguousTickerFilter` stage to the `FilterPipeline` that checks if a symbol is in `AMBIGUOUS_TICKERS` and flags it for context disambiguation. This filter needs `source_text` in the `ctx` dict.

#### [MODIFY] [ticker_scanner.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/ticker_scanner.py)

After LLM extraction returns tickers, pass them through `ContextDisambiguator.disambiguate()` with the transcript text before scoring/validation.

#### [MODIFY] [reddit_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/reddit_service.py)

After regex extraction in `_sync_scrape_threads()`, collect ambiguous candidates and batch-disambiguate using thread text context.

#### [MODIFY] [AgenticExtractor.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/AgenticExtractor.py)

Add disambiguation as Step 2.5 between extraction and self-question. Uses the summary from Step 1 as context.

#### [MODIFY] [rss_news_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/rss_news_service.py)

After `_extract_tickers_from_text()`, run disambiguator on any ambiguous matches using the article content.

---

## Verification Plan

### Automated Tests

#### [NEW] [test_context_disambiguator.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_context_disambiguator.py)

Test cases:
- "AI is transforming healthcare" → AI should be **rejected** (common word usage)
- "$AI reported strong earnings" → AI should be **accepted** (stock reference)
- "NVDA is up 10%" → NVDA passes through (not ambiguous, no LLM call)
- Mixed: "NVDA and AI are leading the market" with AI meaning artificial intelligence → NVDA accepted, AI rejected
- "C3.ai stock (AI) is a buy" → AI should be **accepted** (explicit stock reference)
- Empty/no ambiguous tickers → no LLM call, all pass through

```bash
# Activate venv first
source /home/braindead/github/Lazy-Trading-Bot/venv/bin/activate

# Run unit tests
pytest tests/test_context_disambiguator.py -v

# Run full suite to check no regressions
pytest tests/ -v --timeout=60
```

### Manual Verification

Check the live logs for a discovery cycle:
1. Run the bot with `bash run.sh`
2. Trigger a discovery cycle from the UI
3. Verify in logs that ambiguous tickers like "AI" show `[Disambiguator]` log lines
4. Confirm that generic "AI" mentions (artificial intelligence) are NOT appearing in the scoreboard
