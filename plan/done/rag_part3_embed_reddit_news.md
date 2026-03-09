# RAG Part 3 — Embed Reddit Posts & News Articles

**Priority:** MEDIUM  
**Estimated effort:** Small (same pattern as Part 2)  
**Dependencies:** Part 1 (EmbeddingService), Part 2 pattern

## Goal

Extend the embedding pipeline to cover Reddit posts (from `discovered_tickers.context_snippet`) and full news articles (from `news_full_articles.content`). These provide real-time sentiment and detailed reporting that the current pipeline truncates or ignores.

## Current State

### Reddit Data

- Table: `discovered_tickers` — `context_snippet` column has the raw Reddit post text
- Columns: `ticker, source, source_detail, context_snippet, source_url, discovered_at`
- Filter: `source LIKE '%reddit%'`
- Data is ticker-specific (each row has a ticker)

### News Data

- Table: `news_full_articles` — `content TEXT` column has full article text
- Columns: `article_hash, title, url, publisher, published_at, content, content_length, tickers_found`
- `tickers_found` has comma-separated tickers mentioned in the article
- Content ranges from 200 to 5000+ chars

## Files to Modify

### [MODIFY] `app/services/embedding_service.py`

```python
async def embed_reddit_posts(self) -> dict:
    """Embed Reddit post snippets from discovered_tickers.
    
    Reddit posts are typically short (100-500 chars) so most won't
    need chunking. Each post is ticker-specific.
    
    Query:
        SELECT ticker, source_detail, context_snippet, 
               CAST(rowid AS VARCHAR) as row_id
        FROM discovered_tickers
        WHERE source LIKE '%reddit%'
          AND LENGTH(context_snippet) > 30
          AND CAST(rowid AS VARCHAR) NOT IN (
              SELECT source_id FROM embeddings WHERE source_type = 'reddit'
          )
    """
    ...

async def embed_news_articles(self) -> dict:
    """Embed full news articles from news_full_articles.
    
    News articles are longer (200-5000+ chars) and need chunking.
    tickers_found field maps each article to relevant tickers.
    For multi-ticker articles, create embeddings for each ticker.
    
    Query:
        SELECT article_hash, title, publisher, content, tickers_found
        FROM news_full_articles
        WHERE LENGTH(content) > 50
          AND article_hash NOT IN (
              SELECT DISTINCT source_id FROM embeddings 
              WHERE source_type = 'news'
          )
    """
    ...

async def embed_all_sources(self) -> dict:
    """Run all embedding jobs in sequence. Used by autonomous_loop.
    
    Returns combined stats:
        {
            "youtube": {"embedded": 5, "chunks": 150},
            "reddit": {"embedded": 20, "chunks": 22},
            "news": {"embedded": 8, "chunks": 40},
            "total_chunks": 212,
            "elapsed_s": 32.5
        }
    """
    results = {}
    results["youtube"] = await self.embed_youtube_transcripts()
    results["reddit"] = await self.embed_reddit_posts()
    results["news"] = await self.embed_news_articles()
    results["total_chunks"] = sum(
        r.get("total_chunks", 0) for r in results.values()
    )
    return results
```

### [MODIFY] `app/services/autonomous_loop.py`

Update `_do_embedding()` to call `embed_all_sources()` instead of just `embed_youtube_transcripts()`.

## Design Decisions

### Reddit Posts

- **Short texts:** Most Reddit posts are <500 chars. Don't chunk these — embed the full snippet as one embedding.
- **Ticker mapping:** Each `discovered_tickers` row has a specific ticker → store as `ticker=row.ticker`
- **source_id:** Use DuckDB `rowid` since there's no natural unique ID
- **Metadata:** `source_detail` (subreddit name, e.g. "r/wallstreetbets")

### News Articles

- **Multi-ticker articles:** If `tickers_found = "AAPL,MSFT,GOOGL"`, create embeddings with `ticker=NULL` (general market) so all three tickers can retrieve it. More efficient than duplicating embeddings per ticker.
- **Chunking:** Same strategy as YouTube — 2000 char chunks with 200 char overlap
- **Metadata:** `publisher | title` for attribution

## Verification

### Tests

```python
@pytest.mark.asyncio
async def test_embed_reddit_short_posts(respx_mock, test_db):
    """Short Reddit posts are embedded as single chunks (no splitting)."""
    test_db.execute(
        "INSERT INTO discovered_tickers (ticker, source, context_snippet) "
        "VALUES ('AAPL', 'reddit_wsb', 'AAPL earnings look great, buying calls')"
    )
    respx_mock.post(...).respond(json={"embeddings": [[0.1] * 768]})
    
    svc = EmbeddingService()
    result = await svc.embed_reddit_posts()
    assert result["embedded"] == 1
    assert result["total_chunks"] == 1  # No chunking needed

@pytest.mark.asyncio
async def test_embed_news_multi_ticker(respx_mock, test_db):
    """Multi-ticker articles stored with ticker=NULL."""
    test_db.execute(
        "INSERT INTO news_full_articles (article_hash, title, content, tickers_found) "
        "VALUES ('hash1', 'Tech Earnings', 'Long article...', 'AAPL,MSFT')"
    )
    respx_mock.post(...).respond(json={"embeddings": [[0.1] * 768]})
    
    svc = EmbeddingService()
    result = await svc.embed_news_articles()
    
    rows = test_db.execute(
        "SELECT ticker FROM embeddings WHERE source_id = 'hash1'"
    ).fetchall()
    # Should be stored as general market (ticker=NULL), not duplicated
    assert all(r[0] is None for r in rows)
```

### Run

```bash
pytest tests/test_embedding_reddit_news.py -v
```

## Done Criteria

- [ ] `embed_reddit_posts()` works
- [ ] `embed_news_articles()` works  
- [ ] `embed_all_sources()` orchestrates all three
- [ ] Autonomous loop calls `embed_all_sources()`
- [ ] Tests pass, ruff clean
