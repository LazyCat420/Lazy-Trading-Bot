# Embedding & RAG Audit Report — Revised & Verified (March 17, 2026)

**Audited against Codebase HEAD:** `53973319` and subsequent commits from today.
**Audit scope:** Embedding service, RAG pipeline, pipeline event logging, autonomous loop orchestration, vLLM Provider architecture

---

## Verdict on Prior Audit Claims

### ✅ P0–P6 Fixes: CONFIRMED
All six previously audited fixes (cross-doc batching, 14k content cap, parallel gather, sleep removal, reusable httpx client, hybrid DuckDB commits, and activity log events) remain intact in HEAD across the embedding service or retrieval service.

### ✅ Issue #8: `reddit_threads` Populated (Q2 Answered)
Confirmed via code analysis. Commit `9c670c8` added `reddit_threads` population during discovery. The embedding path (`embed_reddit_posts`) correctly tries this rich table first before falling back to snippets.

### 🔴 Issue #5: Duplicate `pipeline_events` Schema: CRITICAL (Still Present)
Both `CREATE TABLE IF NOT EXISTS pipeline_events` definitions remain in `database.py` at L633 and L962. Schema 1 (L633) creates the table and wins. 
Therefore, `trade_action_parser.py` (which uses Schema 2 columns like `event_data`) silently fails its INSERT requests. Every parse failure, repair, and forced HOLD event is silently dropped, meaning PromptEvolver operates blindly.

---

## 🚨 NEW HIGH-RISK FINDINGS IN HEAD

The recent additions of vLLM support (`53973319`) and Model Pre-warming (`cc3e0000`) have introduced severe structural regressions to the RAG pipeline.

### 🔴 Issue #6: Architectural Split — vLLM vs Ollama Conflict
**Severity: HIGH (VRAM contention and latency under vLLM mode)**

When `LLM_PROVIDER = "vllm"`:
1. `_send_vllm_request()` handles text generation via the external vLLM server.
2. But **`EmbeddingService` still calls Ollama** directly for `nomic-embed-text`.

**The Bug:** In `run_full_loop()`, the pre-warm step calls `LLMService.unload_all_ollama_models()`. This maliciously unloads `nomic-embed-text` from VRAM right before the embedding phase needs it, causing massive latency as Ollama has to cold-load the embedding model from disk on every single loop loop. 
There are currently no checks to skip `unload_all_ollama_models` for the embedding model when in vLLM mode.

### 🔴 Issue #7: Pipeline Ordering Regression — LLM Pre-Warm Causes Contention
**Severity: HIGH (OOM risk on single-GPU setups)**

The pipeline order is now inverted:
1. `unload_all_ollama_models()`
2. `verify_and_warm_ollama_model()` (Loads LLM into VRAM)
3. Discovery → Collection → **Embedding** (Needs VRAM for `nomic-embed-text`)
4. Analysis

**The Bug:** Because the large LLM is pre-warmed *before* the embedding phase, `nomic-embed-text` now has to compete for VRAM with the fully loaded LLM. On a single GPU, this will cause Out-Of-Memory (OOM) crashes or violent VRAM swapping during the embedding step.

**Required Fix:** The pre-warm step MUST occur *after* the `_do_embedding` phase concludes, immediately before `_do_deep_analysis`.

### 🔶 Issue #9: `ContextDisambiguator` Bottleneck in Discovery
**Severity: MEDIUM (Latency)**

Commit `9c670c8` added `ContextDisambiguator` to validate tickers during Reddit scraping. This adds a synchronous LLM call during Discovery. Because `_do_discovery` is subject to `asyncio.Semaphore(1)` limits or strict serialized execution in some paths, resolving ambiguous tickers creates a hard bottleneck, radically slowing down the Discovery phase for high-volume sweeps.

---

## Three Critical Follow-Up Actions for Devs

1. **Move Pre-Warm Execution:** You must physically move `unload_all_ollama_models()` and `verify_and_warm_ollama_model()` in `autonomous_loop.py` so they execute *after* `_do_embedding()` and *before* `_do_deep_analysis()`. This restores the free VRAM for the embedding model.

2. **Fix `pipeline_events` Schema Now:** Delete the duplicate CREATE TABLE at `database.py:L962`. Unify `trade_action_parser.py` and `event_logger.py` to use a single schema so you stop losing PromptEvolver diagnostic data immediately.

3. **Separate Ollama URLs Strategy:** When using vLLM, does `nomic-embed-text` run on the *same physical GPU* via Ollama, or a different server? If the same server, you must ensure `unload_all_ollama_models` explicitly ignores/spares `nomic-embed-text`. If a different server, `EmbeddingService` needs a new env var (e.g., `EMBEDDING_OLLAMA_URL`) instead of inheriting the master `OLLAMA_URL`.
