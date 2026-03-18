# RAG Part 4 — Retrieval Service (Vector Search)

**Priority:** HIGH (required for Part 5 integration)  
**Estimated effort:** Medium  
**Dependencies:** Parts 1-3 (embeddings in DuckDB)

## Goal

Build `RetrievalService` that takes a ticker symbol, generates a query embedding, and retrieves the most relevant text chunks from the `embeddings` table using cosine similarity. This is the "R" in RAG — the retrieval step.

## Architecture

```
ticker ("AAPL")
    ↓
Build search query: "AAPL Apple stock analysis trading outlook"
    ↓
Embed query via EmbeddingService
    ↓
DuckDB: array_cosine_similarity(embedding, query_vec)
    ↓
Filter: ticker = 'AAPL' OR ticker IS NULL
    ↓
Top-K chunks sorted by similarity score
    ↓
Format into text block for LLM prompt
```

## Files to Create

### [NEW] `app/services/retrieval_service.py`

```python
class RetrievalService:
    """Retrieve relevant context from embedded data for trading decisions."""

    def __init__(self):
        self.embedder = EmbeddingService()

    async def retrieve(
        self,
        ticker: str,
        query: str | None = None,
        top_k: int = 5,
        min_score: float = 0.3,
        source_types: list[str] | None = None,
    ) -> list[dict]:
        """Retrieve top-K relevant chunks for a ticker.
        
        Args:
            ticker: Stock symbol to search for
            query: Optional custom search query. If None, auto-generated.
            top_k: Max chunks to return
            min_score: Minimum cosine similarity threshold
            source_types: Filter by source type(s). None = all sources.
        
        Returns:
            List of dicts:
            [
                {
                    "text": "chunk text...",
                    "score": 0.82,
                    "source_type": "youtube",
                    "source_id": "dQw4w9WgXcQ",
                    "metadata": "CNBC | Market Wrap",
                    "ticker": "AAPL"
                },
                ...
            ]
        """
        ...

    async def retrieve_for_trading(
        self,
        ticker: str,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Convenience: retrieve and format chunks for LLM prompt.
        
        Builds a search query from the ticker, retrieves top-K,
        formats with source attribution, and caps total length.
        
        Args:
            ticker: Stock symbol
            top_k: Override default from config
            max_chars: Override default from config
        
        Returns:
            Formatted text block ready for LLM prompt:
            
            [YouTube: CNBC] Apple reported record earnings, beating
            analyst estimates by 12%. Tim Cook highlighted strong
            services revenue growth...
            
            [News: Reuters] AAPL shares rose 3% in after-hours
            trading following the earnings beat...
            
            [Reddit: r/stocks] Everyone's sleeping on AAPL's
            services segment, it's growing 20% YoY...
        """
        ...

    @staticmethod
    def _build_search_query(ticker: str) -> str:
        """Build an effective search query from a ticker symbol.
        
        Uses ticker + common company name mapping for better matches.
        Example: "AAPL" → "AAPL Apple stock market analysis earnings"
        
        For unknown tickers, uses: "{ticker} stock trading analysis outlook"
        """
        # Common ticker → company name mapping for major tickers
        _COMPANY_NAMES = {
            "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Google Alphabet",
            "AMZN": "Amazon", "TSLA": "Tesla", "NVDA": "Nvidia",
            "META": "Meta Facebook", "NFLX": "Netflix", "AMD": "AMD",
            # ... extend as needed
        }
        company = _COMPANY_NAMES.get(ticker, "")
        return f"{ticker} {company} stock trading analysis outlook".strip()

    @staticmethod
    def _format_chunks(chunks: list[dict], max_chars: int = 3000) -> str:
        """Format retrieved chunks into a text block with attribution.
        
        Rules:
        1. Each chunk gets a [Source: Detail] prefix
        2. Total text capped at max_chars
        3. Higher-scored chunks come first
        4. Deduplicate near-identical chunks (overlap from chunking)
        """
        ...
```

## DuckDB Vector Search Query

```sql
SELECT 
    chunk_text,
    source_type,
    source_id,
    ticker,
    metadata,
    list_cosine_similarity(embedding, ?::FLOAT[]) as score
FROM embeddings
WHERE (ticker = ? OR ticker IS NULL)
  AND score >= ?
ORDER BY score DESC
LIMIT ?
```

**DuckDB function note:** Use `list_cosine_similarity()` (DuckDB's built-in). The function name varies by DuckDB version — may need `array_cosine_similarity()`. Test at implementation time and use whichever is available.

**Performance:** For <100K rows, brute-force cosine similarity is fast enough (<100ms). No index needed yet.

## Design Decisions

### Search Query Strategy

- **Don't just search for "AAPL"** — embedding models work better with natural language questions
- Build a query like `"AAPL Apple stock trading analysis outlook"` for broader semantic matching
- This catches transcripts that discuss Apple without literally saying "AAPL"

### Ticker vs General Market Retrieval

- Chunks with `ticker = 'AAPL'` are always retrieved for AAPL queries
- Chunks with `ticker IS NULL` (general market) are also retrieved — these provide macro context
- General market chunks scored lower by the similarity function naturally

### Score Threshold

- `min_score = 0.3` filters out irrelevant noise
- `nomic-embed-text` similarity scores typically range 0.2-0.9
- Relevant chunks score 0.5+ ; tangentially related 0.3-0.5

### Deduplication

- Overlapping chunks from the same source may be very similar
- Keep only the highest-scored chunk per `(source_type, source_id)` pair
- This prevents the LLM from seeing the same YouTube transcript 3 times

## Verification

### Test: `tests/test_retrieval_service.py`

```python
@pytest.mark.asyncio
async def test_retrieve_returns_relevant_chunks(respx_mock, test_db):
    """Top-K retrieval returns highest-scored chunks first."""
    # Insert embeddings with known vectors
    test_db.execute(
        "INSERT INTO embeddings (source_type, source_id, ticker, chunk_index, "
        "chunk_text, embedding) VALUES (?, ?, ?, ?, ?, ?)",
        ["youtube", "v1", "AAPL", 0, "Apple earnings beat estimates", [0.8] * 768]
    )
    test_db.execute(
        "INSERT INTO embeddings (source_type, source_id, ticker, chunk_index, "
        "chunk_text, embedding) VALUES (?, ?, ?, ?, ?, ?)",
        ["reddit", "r1", "MSFT", 0, "Microsoft Azure growing", [0.1] * 768]
    )
    
    # Mock query embedding
    respx_mock.post(...).respond(json={"embeddings": [[0.8] * 768]})
    
    svc = RetrievalService()
    results = await svc.retrieve("AAPL", top_k=5)
    
    assert len(results) >= 1
    assert results[0]["ticker"] == "AAPL"  # AAPL chunk should score highest

@pytest.mark.asyncio
async def test_retrieve_for_trading_formats_output(respx_mock, test_db):
    """retrieve_for_trading returns formatted text with attribution."""
    # Insert test embedding
    ...
    svc = RetrievalService()
    text = await svc.retrieve_for_trading("AAPL")
    assert "[YouTube:" in text or "[News:" in text or "[Reddit:" in text

@pytest.mark.asyncio
async def test_retrieve_includes_general_market(respx_mock, test_db):
    """General market chunks (ticker=NULL) are included in results."""
    test_db.execute(
        "INSERT INTO embeddings (...) VALUES (...)",
        ["youtube", "v1", None, 0, "Market outlook bullish", [0.7] * 768]
    )
    
    respx_mock.post(...).respond(json={"embeddings": [[0.7] * 768]})
    
    svc = RetrievalService()
    results = await svc.retrieve("AAPL")
    assert any(r["ticker"] is None for r in results)
```

### Run

```bash
pytest tests/test_retrieval_service.py -v
```

## Done Criteria

- [ ] `RetrievalService` class created
- [ ] `retrieve()` returns scored, sorted chunks
- [ ] `retrieve_for_trading()` returns formatted text with attribution
- [ ] General market chunks included in results  
- [ ] Deduplication works (no duplicate chunks from same source)
- [ ] Score threshold filters noise
- [ ] Tests pass, ruff clean
