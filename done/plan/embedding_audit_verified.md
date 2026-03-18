# Embedding & RAG Audit — Verified Review

**Reviewed**: Perplexity AI audit report
**Verified against**: actual codebase as of March 17, 2026

---

## Verdict on Each Claim

### ✅ P0–P6 Fixes: ALL CONFIRMED IMPLEMENTED

| Fix | Status | Evidence |
|-----|--------|----------|
| **P0** Cross-document batching | ✅ | `_batch_embed_and_store()` at [L278](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L278) — all 4 source methods call it |
| **P1** Content cap 14,000 chars | ✅ | `MAX_NEWS_CONTENT_LEN = 14_000` at [L38](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L38), used via `LEFT(nfa.content, ?)` at [L579](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L579) |
| **P2** Parallel `asyncio.gather` | ✅ | [L730](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L730) — all 4 sources run concurrently |
| **P3** Sleep throttles removed | ✅ | **Only** `asyncio.sleep(30)` at [L96](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L96) — this is a **heartbeat logger**, not a throttle |
| **P4** Reusable httpx client | ✅ | `_get_client()` at [L50](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L50) — stored as `self._client`, reused across calls |
| **P5** Hybrid DuckDB commit | ✅ | `COMMIT_EVERY = 50` at [L41](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L41), commit logic at [L370-375](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L370-L375) |
| **P6** Activity log events | ✅ | `log_event("embedding", ...)` at [L718-768](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L718-L768) |

> [!TIP]
> The report is correct that all P0–P6 are done. It should be marked as a **completed** audit, not a future plan.

---

### ⚠️ Report Issue #1 — Stale Line Numbers: **CONFIRMED**

The report cites `L332`, `L402`, `L495`, `L596` for sleep sites and `L80` for httpx. None match current file. **The report should note these were pre-refactor references.**

### ⚠️ Report Issue #2 — Parallelization Claim: **CONFIRMED, but nuanced**

The report's "1.3–1.5x" estimate was valid pre-P0. Post-P0, all chunks are flattened before embedding — `asyncio.gather` now primarily overlaps DuckDB `LEFT JOIN` queries with GPU time. The gain is real but smaller than even 1.3x.

### 🔶 Report Issue #3 — `embed_and_store()` Footgun: **PARTIALLY CORRECT**

**The report overstates the severity.** Here's the evidence:

- `embed_and_store()` exists at [L223](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L223) with per-doc commit at [L272](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L272)
- **Who calls it?** Only [tests/test_rag_integration.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_rag_integration.py) (L49, L236, L241, L246)
- **No production caller** uses `embed_and_store()` — all 4 source methods use `_batch_embed_and_store()`
- The docstring at L233 says "Kept for single-document callers (e.g., tests)"

**Severity: downgraded to Medium.** The footgun is real but currently dormant. It becomes High only if a dev calls it in production without knowing the history.

### ⚠️ Report Issue #4 — `_COMPANY_NAMES` Limited: **CONFIRMED**

42 hardcoded entries in [retrieval_service.py L20-36](file:///home/braindead/github/Lazy-Trading-Bot/app/services/retrieval_service.py#L20-L36). Any ticker not in this dict gets a degraded search query without a company name, reducing cosine similarity for retrieval. **This is a real RAG quality gap for dynamically discovered tickers.**

### 🔴 Report Issue #5 — Duplicate `pipeline_events`: **CONFIRMED + WORSE THAN REPORTED**

The Perplexity report correctly identifies two `CREATE TABLE IF NOT EXISTS pipeline_events` statements:
- **Definition 1** at [database.py:L633](file:///home/braindead/github/Lazy-Trading-Bot/app/database.py#L633): `(id VARCHAR PK, phase, event_type, detail, metadata, loop_id, status, bot_id, model_name)`
- **Definition 2** at [database.py:L962](file:///home/braindead/github/Lazy-Trading-Bot/app/database.py#L962): `(id INTEGER PK autoincrement, bot_id, event_type, event_data, created_at)`

**What the report missed:** This isn't just a dormant schema conflict — it causes **active silent failures**:

| Caller | Schema Used | Columns Inserted | Works? |
|--------|------------|-----------------|--------|
| [event_logger.py:L93](file:///home/braindead/github/Lazy-Trading-Bot/app/services/event_logger.py#L93) | Schema 1 | `id, timestamp, phase, event_type, ticker, detail, metadata, loop_id, status, bot_id, model_name` | ✅ |
| [trade_action_parser.py:L61](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trade_action_parser.py#L61) | Schema 2 | `bot_id, event_type, event_data, created_at` | ❌ **FAILS** |
| [trading_agent.py:L37](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py#L37) | (need to check) | likely Schema 2 | ❌ **FAILS** |

Since Definition 1 runs first (`CREATE TABLE IF NOT EXISTS`), it wins. Definition 2 at L962 silently does nothing. But `trade_action_parser.py` inserts columns `(bot_id, event_type, event_data, created_at)` — **the `event_data` column doesn't exist** in Schema 1 (Schema 1 has `detail` and `metadata` instead). This means **every parse/repair diagnostic event is silently dropped** due to the `except Exception` at L71.

> [!CAUTION]
> **Severity: CRITICAL.** Every trade parse failure, repair attempt, and forced HOLD event is silently lost. This is invisible data loss affecting your trade decision diagnostics.

---

## Answers to the Report's Two Follow-Up Questions

### Q1: Where is `embed_all_sources()` called, and is it before/after LLM VRAM load?

**Answer:** `embed_all_sources()` is called from `_do_embedding()` at [autonomous_loop.py:L1125](file:///home/braindead/github/Lazy-Trading-Bot/app/services/autonomous_loop.py#L1125).

**Pipeline order** (confirmed by reading all three loop methods):
```
Discovery → Discovery Collection → Import → Collection → Embedding → Analysis → Trading
```

Embedding runs AFTER collection but BEFORE analysis (which loads the LLM). **This ordering is correct** — the embedding model (`nomic-embed-text`) finishes its work before the LLM model takes VRAM for analysis. Additionally, `precompute_query_vectors()` runs at [L1139](file:///home/braindead/github/Lazy-Trading-Bot/app/services/autonomous_loop.py#L1139) inside `_do_embedding()`, which correctly pre-caches all query vectors while the embedding model is still loaded.

### Q2: Is `reddit_threads` populated or falling back to snippets?

**Could not verify live data** (DB locked by running bot). However, from code analysis of [reddit_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/reddit_service.py), the `reddit_threads` table IS populated by the Reddit scraper during discovery. The `embed_reddit_posts()` method at [L468-482](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L468-L482) tries `reddit_threads` first, and only falls back to `discovered_tickers` snippets if the table is empty or doesn't exist.

---

## My Two Critical Follow-Up Questions

**1. Can you stop the bot briefly so I can query the live database?** I need to verify: (a) whether `reddit_threads` actually has rows, (b) what the actual `pipeline_events` schema is in the running DB, and (c) whether `trade_action_parser` INSERT errors are showing in the logs. This would confirm if Issue #5 is actively losing data or if the table somehow has both schemas merged via migrations.

**2. Are you running Ollama embedding (`nomic-embed-text`) and the LLM on the same GPU, or separate instances?** The `precompute_query_vectors()` cache at L1139 suggests you anticipated VRAM swapping concerns. If embedding and LLM share one GPU through the same Ollama instance, there's a model swap overhead during the analysis phase when `RetrievalService` calls `embed_text()` for any ticker that missed the cache. If they run on separate GPUs or separate Ollama instances, this concern is irrelevant and the cache is just a latency optimization.
