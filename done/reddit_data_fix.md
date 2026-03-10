# Fix Reddit Data Quality

## Problem

All 61 rows in `discovered_tickers` with `source='reddit'` are junk — they contain text like "Mentioned in 13 news articles" which comes from the `rss_news_service.py`, not Reddit. The actual Reddit scraper (`reddit_service.py`) has a well-built 5-step pipeline but is either never running successfully or its data is being overwritten/merged incorrectly.

### Root Causes

1. **Junk data in DB** — `source='reddit'` rows have `source_detail='Mentioned in N news articles'` and `context_snippet='Found in N recent financial news articles'`. These are RSS news tickers, not Reddit.
2. **RSS news contamination** — The `rss_news_service.py` uses `source='rss_news'`, but the `_merge_scores` in `discovery_service.py` can combine sources. If a ticker appears in both reddit and rss_news, the merged source becomes `reddit+rss_news` or `multi` — but the `source_detail` from RSS overwrites the reddit detail.
3. **Reddit collector likely fails silently** — Either hit rate-limiting (429), got empty threads, or the 4-hour cooldown guard prevented re-runs after a failed attempt produced 0 results.

## Proposed Changes

### Data Cleanup

#### [MODIFY] [discovery_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/discovery_service.py)

- In `_save_to_db()`, save all `context_snippets` (not just `[0]`) as a JSON array into `context_snippet` — this preserves per-source context when merging.
- In `_merge_scores()`, preserve the **first non-empty** context snippet per source rather than blindly concatenating.

---

### Reddit Collector Hardening

#### [MODIFY] [reddit_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/reddit_service.py)

1. **Expand subreddit coverage** — Add `"investing"`, `"StockMarket"`, `"options"` to both priority and trending lists (matching reference `RedditPurgeScraper` which uses `ShortSqueeze` and `options`).
2. **Increase limits** — Bump `MAX_POSTS_PER_SUB` from 3→10, `MAX_THREADS_TO_SCRAPE` from 3→8 to match the reference implementation's broader coverage.
3. **Add retry on 429** — Currently retries once on 429. Add exponential backoff (2s, 4s, 8s) with up to 3 attempts.
4. **Fix cooldown guard** — The 4-hour cooldown checks `MAX(discovered_at)` from `discovered_tickers`, but if the previous run produced 0 valid tickers (e.g., Reddit was rate-limited), no rows get inserted, so the cooldown never triggers and it tries again next cycle. This is actually fine — the problem is the opposite: if RSS rows get inserted with `source='reddit'`, the cooldown will block real Reddit runs. Fix: check cooldown only against rows where `source_detail` contains actual subreddit names (not "news articles").
5. **Better error logging** — Log `resp.text[:200]` on failed Reddit API calls for debugging.

---

### Data Explorer — Purge API

#### [MODIFY] [main.py](file:///home/braindead/github/Lazy-Trading-Bot/app/main.py)

- Add a dedicated "Purge RSS Junk from Reddit" cleanup in the existing `POST /api/data/{table}/clean` endpoint: for the `reddit` table, also delete rows where `source_detail LIKE '%news articles%'`.

## Verification Plan

### Automated Tests
1. `curl DELETE` to purge the 61 junk reddit rows
2. `curl POST /api/data/reddit/clean` to verify clean operation
3. Manually trigger a discovery run (via the monitor UI or API) to test the Reddit collector produces real data with subreddit context
4. `curl GET /api/data/reddit` to verify new rows have actual reddit content (subreddit names, `[title]`/`[comment]` context snippets)
