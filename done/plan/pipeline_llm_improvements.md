# Pipeline LLM Improvements — Real Data Analysis

> Based on actual conversations and request logs from **Prism MongoDB** (205 requests, 139 conversations), not just source code review.

---

## Database Summary (Prism MongoDB)

| Metric | Value |
|--------|-------|
| Total Requests | 205 |
| Total Conversations | 139 (114 with messages, 5 empty) |
| Projects | `lazy-trading-bot`, `retina`, `test` |
| Conversation Types | 77 peer_discovery, 16 trading_decision, 15 youtube_scan, 11 warmup |

### Model Performance

| Model | Requests | Success Rate | Avg Time | Avg In Tokens | Avg Out Tokens | Tok/s |
|-------|----------|-------------|----------|---------------|----------------|-------|
| `olmo-3:latest` | 77 | **91%** (7 timeouts) | 146s | 548 | 1,823 | 16 |
| `nemotron-3-nano:latest` | 58 | **100%** | 59s | 647 | 985 | 13 |
| `granite3.2:8b` | 13 | 100% | 12s | 1,013 | **9** (warm-up) | <1 |
| `qwen3.5:35b` | 6 | **50%** (3 timeouts) | 262s | 52 | 1,415 | 13 |

> All 10 failures = `fetch failed` at exactly ~300s (hard timeout). 7 from `olmo-3`, 3 from `qwen3.5`.

---

## PROBLEM 1: Truncated & Mislabeled Analysis in Trading Prompts

### What the LLM actually receives (real example: KO)
```
QUANT SIGNALS:
Conviction: 75% | Kelly: 5.2% | Sharpe: 0.54

NEWS DIGEST:
=== PRE-COMPUTED CHART ANALYSIS ===
Current Price: $77.88
  1 week: -0.3%  |  1 month: -0.1%  |  3 months: +10.9%
--- Trend Regime ---
SIDEWAYS/TRANSITIONAL: Mixed SMA alignment
  Distance from SMA200: -1.4%
--- Key Crossovers ---
  ⚡ MACD bearish crossover 9d ago

BULL: === PRE-COMPUTED FUNDAMENTAL ANALYSIS ===
--- Valuation ---
  P/E: 25.6 — Fair/Growth valuation
  Forward P/E: 22.5 — Earnings GROWTH expected
  P/S:                           ← TRUNCATED MID-SENTENCE

BEAR: === PRE-COMPUTED RISK ANALYSIS ===
--- Risk-Adjusted Performance ---
  Sharpe: 0.54 — Moderate
--- Worst-Case Scenarios ---
  VaR(95%)                       ← TRUNCATED MID-LINE
```

### Issues
1. **`NEWS DIGEST:` contains chart analysis** — not news. The label is misleading.
2. **`BULL:`/`BEAR:` are truncated mid-sentence** — `P/S:` cuts off before the value, `VaR(95%)` cuts off before the number. These are truncated at `[:300]` in `_build_context()`.
3. **No actual news headlines** appear in any trading conversation.
4. **No TECHNICAL ANALYSIS section** — the system prompt references a `technical_summary` field but it's empty in all observed conversations.
5. **Fundamentals cut off** — P/S ratio value, revenue growth, FCF yield, Piotroski score all computed by DataDistiller but truncated away.

### Fix
- [ ] Remove `[:300]` truncation from bull/bear case fields — let full analysis through
- [ ] Rename `NEWS DIGEST:` → `CHART ANALYSIS:` in the prompt builder
- [ ] Add separate `FUNDAMENTALS:` and `RISK:` sections instead of cramming into BULL/BEAR
- [ ] Populate `technical_summary` field that the prompt already expects

---

## PROBLEM 2: Near-Identical Duplicate HOLD Decisions (LLM Stagnation)

### Evidence from Prism
The RAG context for HON includes **5 identical HOLD decisions**, all confidence=40%, all with the same rationale:

```
[Decision: HOLD | pending | 2026-03-09] Confidence: 40%
  "The stock has shown a slight gain today but the Altman Z-Score..."
[Decision: HOLD | pending | 2026-03-02] Confidence: 40%
  "The stock has shown a slight gain today, but the Altman Z-Score..."
[Decision: HOLD | pending | 2026-03-05] Confidence: 40%
  "The stock has shown a slight gain today but the Altman Z-Score..."
[Decision: HOLD | pending | 2026-03-05] Confidence: 40%
  "The stock has shown mixed signals..." (slight variation)
[Decision: HOLD | pending | 2026-03-05] Confidence: 40%
  "The stock has shown mixed signals..."
```

**The LLM is repeating itself cycle after cycle with nearly copy-paste rationales.** It sees its own past decisions and mirrors them, creating a self-reinforcing loop.

### Root Cause
- RAG retrieves past decisions and injects them as `MARKET INTELLIGENCE`
- The decision embeddings are boosted 10% in retrieval scoring
- The LLM sees "I said HOLD before → I'll say HOLD again"
- No new information breaks the loop because fundamentals/news are truncated

### Fix
- [ ] Limit past decisions in RAG to max 2 (not 5+)
- [ ] Add a **delta indicator**: "Since your last decision: price moved +2.1%, volume up 15%"
- [ ] Inject **new data** the LLM hasn't seen (news headlines, YouTube trading data, analyst targets)
- [ ] De-duplicate identical rationales before injecting into context

---

## PROBLEM 3: YouTube Extraction Quality Is Wildly Inconsistent

### Evidence from Prism

**Good extraction** (QCOM video — 1,806 chars):
```json
{
  "tickers": ["QCOM", "TM"],
  "trading_data": {
    "sentiment": "mixed",
    "earnings": "Q1 revenue $12.3B, non-GAAP EPS $0.35...",
    "catalysts": ["LOI with Volkswagen Group...", "Partnership with Toyota..."],
    "risks": ["OEMs pausing chip orders...", "Margin compression..."],
    "key_facts": ["Q1 revenue $12.3B, up 5% YoY", ...]
  }
}
```

**Sparse extraction** (technical analysis video — 614 chars):
```json
{
  "tickers": ["NVDA", "TSLA", "AMD", "PLTR", "AAPL", "SOFI"],
  "trading_data": {
    "price_levels": [],        ← EMPTY despite price levels discussed
    "analyst_ratings": [],     ← EMPTY
    "catalysts": [],           ← EMPTY
    "risks": [],               ← EMPTY
    "technicals": "Bearish pivot point; lower highs lower lows..."  ← single string
  }
}
```

**Legacy format** (ETN/PWR video — 14 chars):
```json
["ETN", "PWR"]
```
No trading data at all — old format before the extraction prompt was upgraded.

### Issues
1. **Empty arrays for fields that have data** — technical analysis videos mention price levels but they end up as `[]`
2. **Inconsistent `technicals` format** — sometimes array, sometimes single string
3. **This data is never used downstream anyway** — `youtube_trading_data` table is never queried for trading decisions

### Fix
- [ ] Add few-shot examples for technical analysis videos (the current prompt is earnings-focused)
- [ ] Enforce consistent array format for all list fields
- [ ] Query `youtube_trading_data` in `_build_context()` and add to prompt

---

## PROBLEM 4: Peer Discovery Produces Junk for Non-US Tickers

### Evidence from Prism

```
Input:  Ticker: RV6.DU  |  Company: RV6.DU  |  Sector:  |  Industry:
Output: ["AAPL", "MSFT", "NVDA"]
```

The LLM has no idea what `RV6.DU` is (it's a Düsseldorf exchange ticker), so it returns the 3 most popular US stocks. This wastes an LLM call and pollutes the watchlist.

Also: the EMET conversation asks for competitors of an **ETF** (`VanEck Copper And Green Metals ETF`), which shouldn't use the peer-discovery tool at all since the prompt says "NO ETFs."

### Fix
- [ ] Filter out non-US tickers (`.DU`, `.MX`, `.TO`, `.L`, etc.) BEFORE calling peer_discovery
- [ ] Skip peer discovery for ETFs — check `quoteType` from yfinance
- [ ] Add a quality gate: if sector/industry are both empty and company name matches ticker, skip

---

## PROBLEM 5: `olmo-3` Timeout Pattern — 7 Consecutive Failures

### Evidence from Prism
All 7 `olmo-3` failures happened on 2026-03-11 between 08:24 and 10:29:
```
08:24:18 — olmo-3:latest — fetch failed — 300.4s
09:20:40 — olmo-3:latest — fetch failed — 300.7s
09:43:01 — olmo-3:latest — fetch failed — 301.0s
09:46:11 — olmo-3:latest — fetch failed — 300.8s
09:49:24 — olmo-3:latest — fetch failed — 300.3s
09:52:42 — olmo-3:latest — fetch failed — 300.9s
10:29:40 — olmo-3:latest — fetch failed — 300.9s
```

This is a **sustained 2-hour outage** with no automatic model fallback. The average `olmo-3` request takes 146s on success; 300s timeouts suggest the Jetson was under heavy load or the model was not loaded.

### Fix
- [ ] Add automatic model fallback: if `olmo-3` fails 3x consecutively, switch to `nemotron-3-nano`
- [ ] Log whether the model is actually loaded before sending requests
- [ ] Reduce timeout from 300s to 180s for `olmo-3` (median is 130s, 95th pctl ~250s)
- [ ] Add a circuit breaker — skip LLM calls if last N failed

---

## PROBLEM 6: `granite3.2:8b` Produces Nearly Zero Output

### Evidence from Prism
13 requests averaging **9 output tokens** and only 0.7 tok/s. These are all warm-up/model-load calls, but the low output suggests the model is barely responding.

### Impact
Minimal — these are intentional warm-up calls ("Say OK" → "OK"). But 13 model loads in 10 days is excessive.

### Fix
- [ ] Consolidate warm-up calls — only warm up the model that's about to be used
- [ ] Track warm-up state in-memory to avoid redundant loads

---

## PROBLEM 7: RAG Context Works but Creates Echo Chamber

### Evidence from Prism
The KO trading decision shows **5 past HOLD decisions** in `MARKET INTELLIGENCE`. RAG retrieval IS working (contradicts the earlier rag_audit that said it was failing), but the content is self-referential:

```
MARKET INTELLIGENCE (retrieved context from recent market sources):
[Decision: HOLD | pending | 2026-03-02] Confidence: 30% ...
[Decision: HOLD | pending | 2026-03-09] Confidence: 55% ...
[Decision: HOLD | pending | 2026-03-06] Confidence: 45% ...
[Decision: HOLD | pending | 2026-03-05] Confidence: 45% ...
[Decision: HOLD | pending | 2026-03-05] Confidence: 45% ...
```

**No YouTube context, no news articles, no Reddit posts** — only past decisions. The decision embedding boost (10%) pushes past decisions to the top of retrieval results, crowding out external intelligence.

### Fix
- [ ] Cap decision source to max 2 entries in RAG results
- [ ] Remove the 10% boost for decision embeddings — let relevance decide
- [ ] Ensure YouTube/news/Reddit embeddings exist and score competitively
- [ ] Add source type diversity: at least 1 non-decision source in top-K

---

## PROBLEM 8: Trading Conversations Miss the System Prompt

### Evidence from Prism
Trading decision conversations have exactly 2 messages: `user` (context) and `assistant` (decision). The **system prompt is not stored** in the conversation — Prism only sees the user message and assistant response, not the full multi-turn context with the agent system prompt.

This means auditing through Retina dashboard only shows the data context and the decision, not the system prompt rules that guided the decision. For debugging LLM behavior, this is a blind spot.

### Fix
- [ ] Include the system prompt as the first message in the conversation (role: `system`)
- [ ] Or store it in the conversation's `systemPrompt` field via ConversationService

---

## PRIORITY ORDERING

| # | Problem | Impact | Evidence | Priority |
|---|---------|--------|----------|----------|
| 1 | Truncated/mislabeled analysis | **HIGH** — LLM can't use data that's cut off | P/S, VaR truncated mid-value | **P0** |
| 2 | Duplicate HOLD stagnation | **HIGH** — bot never acts on opportunities | 5 identical rationales for HON | **P0** |
| 7 | RAG echo chamber | **HIGH** — only past decisions, no external intel | KO: 5/5 RAG results are decisions | **P0** |
| 3 | YouTube extraction inconsistency | **MED** — rich data captured unevenly | 14 chars vs 1,806 chars | **P1** |
| 4 | Junk peer discovery | **MED** — wastes LLM calls, pollutes watchlist | RV6.DU → AAPL/MSFT/NVDA | **P1** |
| 5 | olmo-3 timeout cascade | **MED** — 2-hour blind spots | 7 consecutive 300s failures | **P1** |
| 8 | Missing system prompt in audit | **LOW** — audit quality only | No system prompt in Prism convos | **P2** |
| 6 | Excessive warm-up calls | **LOW** — minor resource waste | 13 model loads in 10 days | **P3** |

---

## IMPLEMENTATION PHASES

### Phase 1 — Fix the Prompt Data (P0, ~2-3 hrs)
- Remove `[:300]` truncation from dossier fields in `_build_context()`
- Rename `NEWS DIGEST:` → `CHART ANALYSIS:`
- Add separate `FUNDAMENTALS:` and `RISK PROFILE:` sections
- Populate `technical_summary` field

### Phase 2 — Fix RAG Echo Chamber (P0, ~2 hrs)
- Cap decision entries to 2 in retrieval results
- Remove 10% decision boost
- Enforce source diversity (>=1 non-decision source)
- De-duplicate near-identical rationales

### Phase 3 — Break HOLD Stagnation (P0, ~1-2 hrs)
- Add delta indicators between decisions
- Inject fresh data sources (YouTube trading data, news)
- Limit injected past decisions to 2 most recent

### Phase 4 — Improve YouTube & Peer Quality (P1, ~2-3 hrs)
- Add few-shot examples for technical analysis videos
- Filter non-US tickers before peer_discovery
- Query `youtube_trading_data` for trading context

### Phase 5 — Add Resilience (P1, ~2 hrs)
- Model fallback after 3 consecutive failures
- Circuit breaker for sustained outages
- Store system prompt in Prism conversations
