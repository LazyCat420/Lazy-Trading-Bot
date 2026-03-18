# RAG Part 1 — Embedding Service Foundation

**Priority:** CRITICAL (blocks all other RAG parts)  
**Estimated effort:** Medium  
**Dependencies:** None — standalone service

## Goal

Build the core `EmbeddingService` that calls Ollama's `/api/embed` endpoint, chunks long text, and stores embedding vectors in DuckDB. This service is used by all subsequent parts.

## Architecture

```
Text → chunk_text() → list[str]
                         ↓
               embed_batch() → Ollama /api/embed
                         ↓
               list[list[float]] → DuckDB embeddings table
```

## Files to Create / Modify

### [NEW] `app/services/embedding_service.py`

```python
class EmbeddingService:
    """Embeds text chunks via Ollama and stores them in DuckDB."""

    DEFAULT_MODEL = "nomic-embed-text:latest"
    CHUNK_SIZE = 512       # tokens (~2048 chars)
    CHUNK_OVERLAP = 50     # tokens overlap between chunks
    MAX_BATCH_SIZE = 32    # max texts per /api/embed call

    def __init__(self, model: str | None = None):
        """Init with Ollama URL from existing config."""
        from app.config import settings
        self.base_url = settings.OLLAMA_URL.rstrip("/")
        self.model = model or self._get_configured_model()

    @staticmethod
    def _get_configured_model() -> str:
        """Read embedding_model from llm_config.json, fallback to default."""
        ...

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string. Returns vector."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call.
        
        Ollama's /api/embed accepts {"model": ..., "input": [str, ...]}
        Returns list of vectors, one per input text.
        """
        ...

    @staticmethod
    def chunk_text(
        text: str,
        chunk_size: int = 2048,  # chars, not tokens
        overlap: int = 200,     # char overlap
    ) -> list[str]:
        """Split long text into overlapping chunks.

        Strategy:
        1. Split on paragraph breaks (double newline) first
        2. If a paragraph exceeds chunk_size, split on sentences
        3. Prepend overlap from previous chunk for continuity
        
        Returns list of chunk strings. Minimum chunk length: 50 chars.
        """
        ...

    async def embed_and_store(
        self,
        source_type: str,
        source_id: str,
        text: str,
        ticker: str | None = None,
        metadata: str = "",
    ) -> int:
        """Chunk text, embed, and store in DuckDB. Returns chunk count."""
        chunks = self.chunk_text(text)
        if not chunks:
            return 0
        
        vectors = await self.embed_batch(chunks)
        
        db = get_db()
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            db.execute(
                "INSERT INTO embeddings (...) VALUES (...)",
                [source_type, source_id, ticker, i, chunk, vec, metadata],
            )
        db.commit()
        return len(chunks)
```

### [MODIFY] `app/database.py`

Add to `_init_tables()`:

```sql
CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY DEFAULT (nextval('embeddings_seq')),
    source_type VARCHAR NOT NULL,      -- 'youtube', 'news', 'reddit', 'trade_decision'
    source_id   VARCHAR NOT NULL,      -- video_id, article_hash, post_id, decision_id
    ticker      VARCHAR,               -- associated ticker (NULL = general market)
    chunk_index INTEGER NOT NULL,      -- position within the source doc
    chunk_text  TEXT NOT NULL,          -- raw text of this chunk
    embedding   FLOAT[] NOT NULL,      -- vector from Ollama embed API
    metadata    VARCHAR DEFAULT '',     -- optional: channel name, subreddit, etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_type, source_id, chunk_index)
);
```

**Notes on DuckDB FLOAT[]:**

- DuckDB natively supports `FLOAT[]` arrays
- `array_cosine_similarity(a, b)` is built-in — no extensions needed
- Index not required for <100K rows; add later if needed

### [MODIFY] `app/user_config/llm_config.json`

Add new config keys:

```json
{
    "embedding_model": "nomic-embed-text:latest",
    "rag_enabled": true,
    "rag_top_k": 5,
    "rag_max_chars": 3000
}
```

### [MODIFY] `app/config.py`

Add settings fields:

```python
RAG_EMBEDDING_MODEL: str = "nomic-embed-text:latest"
RAG_ENABLED: bool = True
RAG_TOP_K: int = 5
RAG_MAX_CHARS: int = 3000
```

Load from `llm_config.json` in the config loader.

## Ollama Embed API Reference

```
POST /api/embed
{
    "model": "nomic-embed-text:latest",
    "input": ["text1", "text2", ...]
}

Response:
{
    "model": "nomic-embed-text:latest",
    "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]]
}
```

- `nomic-embed-text` output: 768 dimensions
- `mxbai-embed-large` output: 1024 dimensions
- Max input per text: ~8192 tokens (chunk_text handles this)

## Verification

### Test: `tests/test_embedding_service.py`

```python
# 1. Test chunk_text splits correctly
def test_chunk_text_basic():
    text = "A" * 5000
    chunks = EmbeddingService.chunk_text(text, chunk_size=2048, overlap=200)
    assert len(chunks) >= 3
    assert all(len(c) <= 2048 + 200 for c in chunks)

# 2. Test chunk_text with paragraph breaks
def test_chunk_text_paragraphs():
    text = "Para one.\n\nPara two.\n\nPara three."
    chunks = EmbeddingService.chunk_text(text, chunk_size=50)
    assert len(chunks) >= 2

# 3. Test chunk_text minimum length filter
def test_chunk_text_filters_short():
    text = "Hi\n\nBye"
    chunks = EmbeddingService.chunk_text(text, chunk_size=2048)
    # Very short text should return as single chunk
    assert len(chunks) <= 1

# 4. Test embed_batch with mocked Ollama
@pytest.mark.asyncio
async def test_embed_batch_mocked(respx_mock):
    respx_mock.post("http://10.0.0.30:11434/api/embed").respond(
        json={"embeddings": [[0.1] * 768, [0.2] * 768]}
    )
    svc = EmbeddingService()
    results = await svc.embed_batch(["hello", "world"])
    assert len(results) == 2
    assert len(results[0]) == 768

# 5. Test embed_and_store writes to DuckDB
@pytest.mark.asyncio
async def test_embed_and_store(respx_mock, tmp_duckdb):
    respx_mock.post(...).respond(json={"embeddings": [[0.1] * 768]})
    svc = EmbeddingService()
    count = await svc.embed_and_store("youtube", "vid123", "Short text", ticker="AAPL")
    assert count == 1
    # Verify DuckDB has the row
    row = tmp_duckdb.execute("SELECT * FROM embeddings WHERE source_id = 'vid123'").fetchone()
    assert row is not None
```

### Run

```bash
pytest tests/test_embedding_service.py -v
ruff check app/services/embedding_service.py --select E,W,F
```

## Done Criteria

- [ ] `EmbeddingService` class created with all methods
- [ ] `embeddings` table in DuckDB
- [ ] Config keys added to `llm_config.json` and `config.py`
- [ ] All 5 tests pass
- [ ] ruff clean
