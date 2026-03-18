# RAG Part 6 — Embed Trade Decisions (Decision Memory)

**Priority:** LOW (enhancement, builds on working RAG)  
**Estimated effort:** Small  
**Dependencies:** Parts 1-5 (full RAG pipeline working)

## Goal

Embed past trading decisions so the LLM can reference its own history. When analyzing AAPL, the bot can see: "Last week I sold AAPL at $180 because RSI was overbought — it dropped to $175. That was a good call." This creates a feedback loop for learning from past decisions.

## Current State

- `trade_decisions` table stores: `symbol, action, confidence, rationale TEXT, risk_level, time_horizon, raw_llm_response, status`
- `orders` table stores: `ticker, side, qty, price, filled_at`
- Past decisions are NOT currently referenced in any trading prompt

## Data to Embed

For each trade decision, build a composite text:

```
AAPL — BUY at $175.50 (confidence: 82%, risk: LOW)
Rationale: Strong earnings beat, services revenue growing 20% YoY.
RSI was at 45 (neutral), MACD bullish crossover 3 days prior.
Kelly fraction suggested 8% position size.
Status: executed, filled at $175.60
```

This captures:

- What the bot decided and why
- The market conditions at decision time
- Whether the decision was executed or rejected
- Outcome data (if available from subsequent price moves)

## Files to Modify

### [MODIFY] `app/services/embedding_service.py`

```python
async def embed_trade_decisions(self) -> dict:
    """Embed past trade decisions for decision memory.
    
    Query trade_decisions + orders to build decision summaries.
    Only embed decisions from the last 30 days (older ones are
    less relevant for current market conditions).
    
    Flow:
    1. Query trade_decisions WHERE ts > (NOW - 30 days)
    2. LEFT JOIN orders ON decision_id for execution details
    3. Skip already-embedded decisions
    4. Build summary text for each decision
    5. Embed and store with source_type='trade_decision',
       ticker=symbol, source_id=decision_id
    
    Returns:
        {"embedded": 12, "skipped": 45, "total_chunks": 12}
    """
    db = get_db()
    
    rows = db.execute("""
        SELECT 
            td.id, td.symbol, td.action, td.confidence,
            td.rationale, td.risk_level, td.time_horizon,
            td.status, td.ts,
            o.price as fill_price, o.qty as fill_qty
        FROM trade_decisions td
        LEFT JOIN orders o ON td.id = o.decision_id
        WHERE td.ts > CURRENT_TIMESTAMP - INTERVAL 30 DAY
          AND td.id NOT IN (
              SELECT source_id FROM embeddings 
              WHERE source_type = 'trade_decision'
          )
        ORDER BY td.ts DESC
    """).fetchall()
    
    for row in rows:
        text = self._format_decision_text(row)
        await self.embed_and_store(
            source_type="trade_decision",
            source_id=row.id,
            text=text,
            ticker=row.symbol,
            metadata=f"{row.action} {row.ts}",
        )

def _format_decision_text(self, row) -> str:
    """Format a trade decision row into embeddable text."""
    parts = [
        f"{row.symbol} — {row.action} (confidence: {row.confidence:.0%}, "
        f"risk: {row.risk_level})",
    ]
    if row.rationale:
        parts.append(f"Rationale: {row.rationale[:500]}")
    if row.fill_price:
        parts.append(f"Executed at ${row.fill_price:.2f}")
    parts.append(f"Status: {row.status}")
    return "\n".join(parts)
```

### [MODIFY] `app/services/embedding_service.py` — `embed_all_sources()`

Add `embed_trade_decisions()` as the last step:

```python
async def embed_all_sources(self) -> dict:
    results = {}
    results["youtube"] = await self.embed_youtube_transcripts()
    results["reddit"] = await self.embed_reddit_posts()
    results["news"] = await self.embed_news_articles()
    results["trade_decisions"] = await self.embed_trade_decisions()
    # ...
```

### [MODIFY] `app/services/retrieval_service.py`

Add decision memory weighting:

```python
async def retrieve(self, ticker, ...):
    # After retrieving all chunks, boost trade_decision scores
    # by 10% to surface the bot's own experience
    for chunk in results:
        if chunk["source_type"] == "trade_decision":
            chunk["score"] *= 1.10  # 10% boost
    
    # Re-sort after boosting
    results.sort(key=lambda x: x["score"], reverse=True)
```

### [MODIFY] `app/services/retrieval_service.py` — Attribution Format

```python
# In _format_chunks():
_SOURCE_LABELS = {
    "youtube": "YouTube",
    "reddit": "Reddit",
    "news": "News",
    "trade_decision": "My Past Decision",  # Makes it clear it's the bot's own history
}
```

## Design Decisions

### 30-Day Window

- Only embed decisions from the last 30 days
- Older decisions reflect different market conditions and are less useful
- Reduces embedding computation and storage
- Old embeddings can be cleaned up periodically

### No Chunking

- Trade decisions are short (~200-500 chars)
- Each decision is one embedding (no chunking needed)

### Score Boost

- The bot's own decisions are given a 10% score boost
- This ensures the LLM sees its own history even if other sources score slightly higher
- Not too aggressive — external intelligence still dominates

### Privacy Note

- Trade decisions may contain the LLM's raw reasoning
- Since this is a local system (Ollama on Jetson), there are no privacy concerns
- All data stays on-device

## Verification

### Tests

```python
@pytest.mark.asyncio
async def test_embed_trade_decisions(respx_mock, test_db):
    """Trade decisions from last 30 days are embedded."""
    test_db.execute(
        "INSERT INTO trade_decisions (id, symbol, action, confidence, rationale, ts) "
        "VALUES ('d1', 'AAPL', 'BUY', 0.82, 'Strong earnings', CURRENT_TIMESTAMP)"
    )
    respx_mock.post(...).respond(json={"embeddings": [[0.1] * 768]})
    
    svc = EmbeddingService()
    result = await svc.embed_trade_decisions()
    assert result["embedded"] == 1

@pytest.mark.asyncio
async def test_old_decisions_excluded(respx_mock, test_db):
    """Decisions older than 30 days are NOT embedded."""
    test_db.execute(
        "INSERT INTO trade_decisions (id, symbol, action, confidence, ts) "
        "VALUES ('old1', 'AAPL', 'BUY', 0.5, '2025-01-01')"
    )
    
    svc = EmbeddingService()
    result = await svc.embed_trade_decisions()
    assert result["embedded"] == 0

@pytest.mark.asyncio
async def test_decision_memory_boosted_in_retrieval(respx_mock, test_db):
    """Trade decision chunks get 10% score boost."""
    # Insert trade decision embedding and youtube embedding with same vector
    ...
    svc = RetrievalService()
    results = await svc.retrieve("AAPL")
    
    # Trade decision should rank higher due to boost
    decision_chunks = [r for r in results if r["source_type"] == "trade_decision"]
    other_chunks = [r for r in results if r["source_type"] != "trade_decision"]
    if decision_chunks and other_chunks:
        assert decision_chunks[0]["score"] >= other_chunks[0]["score"]
```

### Run

```bash
pytest tests/test_embedding_decisions.py -v
```

### Manual Verification

- Run several trading cycles to generate decisions
- Run embedding to embed those decisions
- On next trading cycle, check if `MARKET INTELLIGENCE` section includes `[My Past Decision]` entries
- Verify the LLM references past decisions in its rationale

## Done Criteria

- [ ] `embed_trade_decisions()` embeds recent decisions
- [ ] 30-day window filter works
- [ ] Score boost applied to decision chunks in retrieval
- [ ] `[My Past Decision]` attribution shows in formatted output
- [ ] Integrated into `embed_all_sources()` pipeline
- [ ] Tests pass, ruff clean

## Future Enhancements (out of scope for Part 6)

- **Outcome tracking:** After a BUY decision, track the actual P&L over 3/7/14 days and append outcome to the embedding: "Result: +5.2% over 7 days"
- **Decay scoring:** Gradually reduce the score boost for older decisions
- **Win/loss filtering:** Only boost decisions that led to profitable outcomes
