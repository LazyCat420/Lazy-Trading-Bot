# Pipeline LLM Fix Checklist

> All fixes derived from real Prism MongoDB conversation data (205 requests, 139 conversations).
> See `pipeline_llm_improvements.md` for full evidence and data citations.

---

## Phase 1 — Fix the Prompt Data (P0)

### 1.1 Remove Truncation from Dossier Fields
- **File**: `app/services/trading_pipeline_service.py` → `_build_context()`
- **Problem**: `[:300]` truncation cuts off fundamental analysis mid-value (e.g., `P/S:` with no number)
- **Evidence**: KO trading conversation shows `P/S:` and `VaR(95%)` literally truncated mid-line
- [ ] Remove `[:300]` from `executive_summary` (chart analysis) field
- [ ] Remove `[:300]` from `bull_case` (fundamental analysis) field
- [ ] Remove `[:300]` from `bear_case` (risk analysis) field
- [ ] Verify: run a trading cycle, check Prism conversation content for full analysis text

### 1.2 Rename Mislabeled Prompt Sections
- **File**: `app/services/trading_pipeline_service.py` → `_build_context()`
- **Problem**: `NEWS DIGEST:` contains chart analysis, `BULL:`/`BEAR:` contain fundamentals/risk
- [ ] Rename `NEWS DIGEST:` → `CHART ANALYSIS:` in the context builder
- [ ] Rename `BULL:` → `FUNDAMENTALS:` in the context builder
- [ ] Rename `BEAR:` → `RISK PROFILE:` in the context builder
- [ ] Alternative: rename dossier storage fields in `deep_analysis_service.py`

### 1.3 Populate Technical Summary
- **File**: `app/services/trading_pipeline_service.py` → `_build_context()`
- **Problem**: System prompt references `technical_summary` but it's always empty
- [ ] Wire `data_distiller.distill_price_action()` output to `technical_summary` field
- [ ] Or include it directly in the prompt context as a `TECHNICALS:` section

---

## Phase 2 — Fix RAG Echo Chamber (P0)

### 2.1 Cap Past Decisions in RAG Results
- **File**: `app/services/retrieval_service.py`
- **Problem**: KO shows 5/5 RAG results are past HOLD decisions, crowding out external intel
- [ ] Add source-type tracking to retrieval results
- [ ] Cap `trade_decisions` source to max 2 entries in top-K results
- [ ] Enforce diversity: at least 1 non-decision source in results (YouTube/news/Reddit)

### 2.2 Remove Decision Embedding Boost
- **File**: `app/services/retrieval_service.py`
- **Problem**: 10% score boost for past decisions pushes them above more relevant content
- [ ] Remove or reduce the `1.10` multiplier for decision embeddings
- [ ] Let cosine similarity alone determine relevance

### 2.3 De-duplicate Near-Identical Rationales
- **File**: `app/services/retrieval_service.py` or `trading_pipeline_service.py`
- **Problem**: HON has 5 near-identical HOLD rationales all injected into context
- [ ] Before injection, deduplicate RAG results with >90% text similarity
- [ ] Keep only the most recent version of duplicate rationales

---

## Phase 3 — Break HOLD Stagnation (P0)

### 3.1 Add Delta Indicators Between Decisions
- **File**: `app/services/trading_pipeline_service.py` → `_build_context()`
- **Problem**: LLM sees past decisions but no "what changed since then"
- [ ] Query last decision for the ticker from `trade_decisions` table
- [ ] Compute delta: price change %, volume change %, conviction change
- [ ] Add `SINCE LAST DECISION:` section to prompt with deltas

### 3.2 Inject Fresh Data Sources
- **File**: `app/services/trading_pipeline_service.py` → `_build_context()`
- **Problem**: No news, YouTube data, or analyst targets in trading prompt
- [ ] Query `youtube_trading_data` for the ticker and add `CATALYST INTELLIGENCE:` section
- [ ] Add most recent news headlines to prompt (not just RAG)
- [ ] Add analyst price targets from fundamentals data

---

## Phase 4 — Improve YouTube & Peer Quality (P1)

### 4.1 Fix YouTube Extraction Inconsistency
- **File**: `app/services/ticker_scanner.py`
- **Problem**: Extraction varies from 14 chars to 1,806 chars; tech analysis videos produce empty arrays
- [ ] Add few-shot examples for technical analysis videos
- [ ] Enforce consistent array format for all list fields (no single-string technicals)
- [ ] Add validation: if `trading_data` has >50% empty fields, retry with enhanced prompt

### 4.2 Filter Junk Peer Discovery Inputs
- **File**: `app/services/peer_fetcher.py` or caller
- **Problem**: `RV6.DU` → returns `["AAPL", "MSFT", "NVDA"]` (nonsense); ETFs hit peer discovery
- [ ] Filter out non-US exchange tickers (`.DU`, `.MX`, `.TO`, `.L`, etc.) before LLM call
- [ ] Skip peer discovery for ETFs (check `quote_type` from yfinance)
- [ ] Quality gate: if sector AND industry are empty and company name matches ticker, skip

### 4.3 Increase Transcript Character Limit
- **File**: `app/services/ticker_scanner.py`
- **Problem**: `_MAX_TRANSCRIPT_CHARS = 8000` cuts videos at ~50% losing conclusions
- [ ] Increase to 16000 or implement "head + tail" strategy (first 8K + last 4K)
- [ ] Profile LLM response quality vs transcript length

---

## Phase 5 — Add Resilience (P1)

### 5.1 Automatic Model Fallback
- **File**: `app/services/llm_service.py`
- **Problem**: `olmo-3` had 7 consecutive 300s timeouts (2-hour outage) with no fallback
- [ ] Track consecutive failure count per model
- [ ] After 3 consecutive failures, switch to fallback model (`nemotron-3-nano`)
- [ ] Log the fallback event; auto-recover after N minutes

### 5.2 Reduce Timeout for Large Models
- **File**: `app/services/llm_service.py` or `config.py`
- **Problem**: 300s timeout is too generous; `olmo-3` median is 130s, 95th percentile ~250s
- [ ] Set `olmo-3` timeout to 200s (covers 99% of successful requests)
- [ ] Set `nemotron-3-nano` timeout to 120s (median 59s)

### 5.3 Store System Prompt in Prism Conversations
- **File**: `app/services/llm_service.py` → conversation creation
- **Problem**: Trading conversation audit in Retina only shows user context + assistant response, not the system prompt rules
- [ ] Include system prompt as first message (role: `system`) when creating conversation
- [ ] Or set `systemPrompt` field via `startConversation()` API

---

## Verification Plan

For each phase, verify by:
1. Run a full trading cycle with the changes
2. Check Prism conversations in Retina (`/admin/conversations`) to confirm:
   - Full analysis text (not truncated)
   - Correct section labels
   - Diverse RAG results (not all decisions)
   - Delta indicators present
3. Check request logs in Retina (`/admin/requests`) for:
   - Success rate improvements
   - Token count changes (more input context = more input tokens)
   - No timeout regressions
