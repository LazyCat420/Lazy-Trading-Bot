# Improve Reddit Data Collection Quality

The current Reddit scraper discovers tickers via generic subreddit browsing but only stores tiny 60-80 char snippet fragments. The pipeline **already** implements the RedditPurgeScraper pattern (priority threads → trending → LLM filter → deep scrape → extract tickers), but the data quality is weak because:

1. **Snippets are too short** — only `[comment] {comment[:60]}` or `[body] {body[:80]}` is stored
2. **No stock-specific search** — only browses generic hot/rising feeds, never searches for a specific ticker
3. **Low post limits** — `MAX_POSTS_PER_SUB=3` means most relevant threads are missed
4. **No dedicated Reddit storage** — thread content goes into a single `context_snippet VARCHAR` field, losing the full thread
5. **Embeddings on fragments** — the embedding pipeline works on these tiny snippets, producing weak semantic vectors

## User Review Required

> [!IMPORTANT]
> This plan adds a new `reddit_threads` table and changes what data gets stored. Existing Reddit discovery data will continue working — this is purely additive. New data will be significantly richer.

> [!IMPORTANT]
> The stock-specific search feature scrapes Reddit's search endpoint for each ticker on the scoreboard/watchlist. This means more HTTP requests to Reddit. The 1s rate-limit delay is preserved, but scraping ~10 tickers at once will add ~30-60s to the Reddit collection phase.

## Proposed Changes

### Database Schema

#### [MODIFY] [database.py](file:///home/braindead/github/Lazy-Trading-Bot/app/database.py)

Add `reddit_threads` table to persist full thread data:

```sql
CREATE TABLE IF NOT EXISTS reddit_threads (
    thread_id       VARCHAR PRIMARY KEY,   -- Reddit post ID
    subreddit       VARCHAR NOT NULL,
    title           VARCHAR NOT NULL,
    selftext        TEXT DEFAULT '',
    permalink       VARCHAR NOT NULL,
    score           INTEGER DEFAULT 0,
    num_comments    INTEGER DEFAULT 0,
    comments_json   TEXT DEFAULT '[]',     -- top N comments as JSON array
    tickers_found   VARCHAR DEFAULT '',    -- comma-separated tickers
    search_ticker   VARCHAR DEFAULT '',    -- if found via stock-specific search
    collected_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

This gives the embedding pipeline rich text to work with instead of 60-char fragments.

---

### Reddit Service

#### [MODIFY] [reddit_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/reddit_service.py)

**Changes:**

1. **Add stock-specific search method** — `search_for_ticker(ticker)` scrapes `reddit.com/search.json?q=$TICKER&sort=new&t=week` across financial subreddits. This finds threads discussing a specific stock rather than hoping it appears in generic feeds.

2. **Expand subreddit lists and limits:**
   - Add `Daytrading`, `ValueInvesting`, `thetagang`, `SPACs` to trending list
   - Increase `MAX_POSTS_PER_SUB` default from 3 → 10
   - Increase `MAX_THREADS_TO_SCRAPE` from 8 → 15

3. **Store full thread data to `reddit_threads` table:**
   - In `_sync_scrape_threads`, after fetching thread data, INSERT full title + body + comments into `reddit_threads`
   - Build richer `context_snippets` by combining title + body excerpt + top comment excerpts (up to 500 chars total per snippet instead of 60)

4. **Add `collect_for_ticker(ticker)` method** — targeted search for a specific stock, used by the deep analysis pipeline to gather Reddit sentiment on stocks already in the watchlist.

5. **Improve context quality** — Instead of `f"[comment] {comment[:60]}"`, create a composite context:
   ```
   Thread: {title} | Body: {body[:200]} | Top comments: {comment1[:100]}; {comment2[:100]}
   ```

---

### Embedding Service

#### [MODIFY] [embedding_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py)

Update `embed_reddit_posts()` to:
- Pull from the new `reddit_threads` table instead of the `discovered_tickers.context_snippet` field
- Embed full thread content (title + selftext + comments) as a single document
- This gives the RAG system actual substance to retrieve from

---

### Data Distiller

#### [MODIFY] [data_distiller.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/data_distiller.py)

Update `distill_reddit()` to:
- Pull thread data from `reddit_threads` when available for the ticker
- Include thread titles, top comments, and subreddit context
- Display richer community sentiment in dossiers

---

### Deep Analysis Integration

#### [MODIFY] [deep_analysis_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/deep_analysis_service.py)

- During deep analysis for a watchlist ticker, call `reddit.collect_for_ticker(ticker)` to grab targeted Reddit threads
- This fills the `reddit_threads` table with stock-specific data

## Verification Plan

### Manual Verification
Since this is a live scraping pipeline, automated tests can't hit Reddit's API. Verification:

1. **Start the bot** (`bash run.sh`) and trigger a discovery cycle
2. **Check logs** for `[Reddit]` lines — verify higher thread counts and stock-specific search hits
3. **Check DuckDB** — run `SELECT * FROM reddit_threads LIMIT 5` to verify full thread data is stored
4. **Check the Reddit tab** in the scoreboard ticker detail panel — verify richer context appears
5. **Check embedding stats** — verify Reddit embeddings have more chunks (richer text)
