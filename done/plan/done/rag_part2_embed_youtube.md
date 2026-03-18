# RAG Part 2 — Embed YouTube Transcripts

**Priority:** HIGH (biggest data source, most value for trading decisions)  
**Estimated effort:** Small  
**Dependencies:** Part 1 (EmbeddingService)

## Goal

Create a background job that embeds all YouTube transcripts from the `youtube_transcripts` table. This is the highest-value data source — financial YouTube channels discuss specific tickers with context, sentiment, and reasoning that the LLM currently never sees.

## Current State

- `youtube_transcripts` table has columns: `video_id`, `channel_name`, `title`, `raw_transcript`, `collected_at`, `scanned_for_tickers`
- Transcripts range from 500 to 10,000+ words
- YouTube service already has daily dedup guards (`raw_transcript IS NOT NULL AND LENGTH(raw_transcript) > 50`)
- ~42 transcripts collected per day across financial channels

## Files to Modify

### [MODIFY] `app/services/embedding_service.py`

Add method:

```python
async def embed_youtube_transcripts(self) -> dict:
    """Embed all YouTube transcripts not yet in the embeddings table.
    
    Flow:
    1. Query youtube_transcripts WHERE raw_transcript IS NOT NULL
       AND LENGTH(raw_transcript) > 50
    2. LEFT JOIN embeddings to skip already-embedded videos
    3. For each unembedded transcript:
       a. chunk_text(raw_transcript)
       b. embed_batch(chunks)
       c. Store with source_type='youtube', source_id=video_id
       d. Set ticker from scanned_for_tickers or NULL (general market)
       e. Set metadata = channel_name + title
    4. Log progress: "Embedded 15/42 transcripts (360 chunks total)"
    
    Returns:
        {"embedded": 15, "skipped": 27, "total_chunks": 360, "elapsed_s": 45.2}
    """
```

**Key design decisions:**

1. **Ticker association:** Most YouTube transcripts are general market commentary, not ticker-specific. Store with `ticker=NULL` so they're retrievable for ANY ticker query as "general market intelligence." If `scanned_for_tickers` found specific tickers, create ticker-specific entries for those.

2. **Metadata:** Store `channel_name | title` as metadata so retrieved chunks can show attribution: `[YouTube: CNBC] "Apple's earnings beat..."`.

3. **Chunking strategy for transcripts:**
   - Transcripts are conversational, not structured
   - Use paragraph-based chunking (split on `\n\n`)
   - Target ~2000 chars per chunk (roughly 500 tokens for nomic-embed)
   - 200 char overlap to maintain context across chunks

4. **Rate limiting:** Embed in batches of 32 texts at a time to avoid overwhelming Ollama. Sleep 100ms between batches.

### [MODIFY] `app/services/autonomous_loop.py`

Add embedding phase to the main loop:

```python
# In _run_loop(), after data collection, before analysis:
async def _do_embedding(self) -> dict:
    """Run embedding on newly collected data (once per cycle)."""
    if not settings.RAG_ENABLED:
        return {"skipped": True, "reason": "RAG disabled"}
    
    from app.services.embedding_service import EmbeddingService
    svc = EmbeddingService()
    
    result = await svc.embed_youtube_transcripts()
    self._log(
        f"Embedding: {result.get('embedded', 0)} transcripts → "
        f"{result.get('total_chunks', 0)} chunks"
    )
    return result
```

**Placement in loop:** After `_do_data_collection()`, before `_do_analysis()`. This ensures newly collected transcripts are embedded before the trading phase tries to retrieve them.

**Error handling:** If embedding fails (Ollama down, model not loaded), log warning and continue. Embedding is enhancement, not critical path.

## SQL Queries

### Find unembedded transcripts

```sql
SELECT yt.video_id, yt.channel_name, yt.title, yt.raw_transcript
FROM youtube_transcripts yt
LEFT JOIN (
    SELECT DISTINCT source_id 
    FROM embeddings 
    WHERE source_type = 'youtube'
) e ON yt.video_id = e.source_id
WHERE yt.raw_transcript IS NOT NULL
  AND LENGTH(yt.raw_transcript) > 50
  AND e.source_id IS NULL
ORDER BY yt.collected_at DESC
```

## Verification

### Test: `tests/test_embedding_youtube.py`

```python
@pytest.mark.asyncio
async def test_embed_youtube_skips_existing(respx_mock, test_db):
    """Already-embedded transcripts are not re-embedded."""
    # Insert transcript + existing embedding
    test_db.execute("INSERT INTO youtube_transcripts ...")
    test_db.execute("INSERT INTO embeddings (source_type, source_id, ...) ...")
    
    svc = EmbeddingService()
    result = await svc.embed_youtube_transcripts()
    assert result["embedded"] == 0
    assert result["skipped"] == 1

@pytest.mark.asyncio
async def test_embed_youtube_chunks_long_transcript(respx_mock, test_db):
    """Long transcripts get chunked into multiple embeddings."""
    test_db.execute(
        "INSERT INTO youtube_transcripts (video_id, raw_transcript, ...) VALUES (?, ?, ...)",
        ["vid1", "A" * 10000, ...]
    )
    # Mock embed API
    respx_mock.post(...).respond(json={"embeddings": [[0.1] * 768] * 5})
    
    svc = EmbeddingService()
    result = await svc.embed_youtube_transcripts()
    assert result["embedded"] == 1
    assert result["total_chunks"] >= 3  # 10000 chars / 2048 per chunk
```

### Run

```bash
pytest tests/test_embedding_youtube.py -v
```

### Manual Verification

- Run a full loop cycle
- Check logs: `Embedding: X transcripts → Y chunks`
- Query DuckDB: `SELECT COUNT(*) FROM embeddings WHERE source_type = 'youtube'`

## Done Criteria

- [ ] `embed_youtube_transcripts()` method works
- [ ] Dedup: already-embedded videos are skipped
- [ ] Chunking produces reasonable sizes (no 1-char chunks, no 50K char chunks)
- [ ] Integrated into autonomous loop (runs after collection)
- [ ] Tests pass, ruff clean
