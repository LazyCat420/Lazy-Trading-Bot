# RAG Implementation Plan — Overview & Index

## Architecture

```
Collection → Embedding → Storage → Retrieval → Trading Prompt
(existing)    (Part 1)   (DuckDB)  (Part 4)    (Part 5)
```

## Parts

| Part | File | Description | Dependencies |
| ---- | ---- | ----------- | ------------ |
| 1 | `rag_part1_embedding_service.md` | EmbeddingService + DuckDB table + config | None |
| 2 | `rag_part2_embed_youtube.md` | Embed YouTube transcripts | Part 1 |
| 3 | `rag_part3_embed_reddit_news.md` | Embed Reddit posts + news articles | Part 1 |
| 4 | `rag_part4_retrieval_service.md` | Vector search retrieval service | Parts 1-3 |
| 5 | `rag_part5_pipeline_integration.md` | Wire RAG into trading pipeline | Part 4 |
| 6 | `rag_part6_decision_memory.md` | Embed trade decisions (learning loop) | Parts 1-5 |

## Order

Build each part, test it, verify it works, then move to the next.
Move completed part MDs to `plan/done/` when finished.
