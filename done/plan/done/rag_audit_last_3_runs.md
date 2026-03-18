# RAG Pipeline Audit — Last 3 Trading Runs

## Verdict: ❌ RAG Retrieval is NOT Working

**Embedding phase works.** Retrieval phase **silently fails every run.** The LLM never sees MARKET INTELLIGENCE context.

---

## Evidence Summary

| Run | Health Report | Model | Embed Chunks | RAG Retrieval | Trading Decisions |
|-----|---------------|-------|-------------|---------------|-------------------|
| 1 (22:38) | `health_2026-03-05_000252.md` | olmo-3:latest ctx=8192 | ✅ 8,257 | ❌ Silent fail | 8 decisions (many forced HOLD) |
| 2 (00:38) | `health_2026-03-05_012730.md` | olmo-3:32b ctx=8192 | ✅ 49 | ❌ Silent fail | 4 timeouts, 0 orders |
| 3 (01:45) | `health_2026-03-05_015618.md` | granite3.2:8b-50k ctx=8192 | ✅ 37 | ❌ Silent fail | 8 decisions, 1 order |

### Key Log Evidence

**Test run log** (`trading_bot_2026-03-04_21-40-43.log` line 15-16):

```
[Embedding] Ollama /api/embed failed (HTTP 500):
[Retrieval] Failed to embed query for AAPL
```

**Main run log** (`trading_bot_2026-03-04_22-18-24.log`): Zero lines matching "Retrieval", "RAG", "MARKET INTELLIGENCE", "rag_context", or "TradingPipeline" during the trading phase. All 8 trading decisions proceed directly: `[TradingAgent] Analyzing X...` → `Ollama request` → `Decision`. No retrieval step between context building and LLM call.

---

## Root Cause

### The VRAM Collision Problem

```
Embedding Phase (works):     LLM model UNLOADED → nomic-embed-text loads → embeds 8257 chunks ✅
Trading Phase (fails):       LLM model LOADED (occupies VRAM) → nomic-embed-text tries to load → HTTP 500 ❌
```

**Timeline of failure:**

1. `_do_embedding()` runs after data collection. The LLM model (olmo-3/granite) was pre-warmed but Ollama may unload it. `nomic-embed-text` loads successfully and creates thousands of chunks.
2. `_do_trading()` runs next. The LLM model reloads into VRAM for trading decisions.
3. `TradingPipelineService._build_context()` calls `RetrievalService.retrieve_for_trading(ticker)`.
4. `RetrievalService.retrieve()` → `EmbeddingService.embed_text()` → hits `/api/embed` for the **query** vector.
5. **This fails** because Ollama can't load `nomic-embed-text` while the larger LLM model occupies VRAM.
6. Exception is caught at `trading_pipeline_service.py:438` → `rag_context = ""` → silently proceeds.

### Code Path

```
autonomous_loop._do_trading()
  └─ TradingPipelineService.run_once()           # settings.USE_NEW_PIPELINE = True
       └─ _process_ticker() → _build_context()
            └─ Line 425-443: RAG retrieval block
                 └─ RetrievalService().retrieve_for_trading(ticker)
                      └─ EmbeddingService.embed_text(search_query)   ← FAILS HERE (HTTP 500)
                 └─ Except catches silently → rag_context = ""
```

The `TradingAgent._build_prompt()` has the MARKET INTELLIGENCE section at line 162-167, but `rag_context` is always empty, so it's never rendered.

---

## Fix Options

### Option A: Pre-compute query vectors (Recommended)

Before the trading phase starts, compute and cache query embeddings for all active tickers while `nomic-embed-text` is still loaded. Store them in the DuckDB or in-memory dict. The retrieval phase then skips the embed step and just does the SQL cosine similarity search.

### Option B: Unload/reload model dance

Before each `retrieve_for_trading()` call, temporarily unload the LLM model, load nomic-embed-text, do the embed, then reload the LLM. This would be slow (~20s per ticker for model swap).

### Option C: Use Ollama concurrent model loading

If the Jetson has enough VRAM, configure Ollama to keep both models loaded simultaneously. Current LLM (granite 8B = ~5.8GB) + nomic-embed-text (~274MB) would need ~6GB total, well within the 64GB available. Set `OLLAMA_MAX_LOADED_MODELS=2` in the Ollama server environment.
