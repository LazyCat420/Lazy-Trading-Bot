# Embedding Performance Audit — Final Verified Report (Revised)

> **Scope**: [embedding_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py)
> **Verified against**: Source code + live production logs (2026-03-16 00:29 run) + empirical `chunk_text()` tests.
> **Delta from user's report**: 3 corrections, 1 new bottleneck found, 2 acceptance criteria revised.

---

## Critical Finding: News Articles Are 95%+ of the Problem

| Phase | Duration | Documents | Notes |
|-------|----------|-----------|-------|
| YouTube | 7.5s | 2 videos, 62 chunks | Working correctly |
| Reddit | 0.0s | 0 new posts | Nothing to embed |
| **News** | **28+ min (still running)** | Unknown count | **Dominant bottleneck** |
| Decisions | Not reached | — | Blocked behind news |

Every optimization effort should focus on `embed_news_articles()`.

---

## Claim-by-Claim Verification

### Bottleneck 1: Sequential Source Execution ✅ CONFIRMED

**Code evidence** — [embed_all_sources() L610-683](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L610-L683):
```python
yt        = await self.embed_youtube_transcripts()   # L623 — blocks
reddit    = await self.embed_reddit_posts()           # L632 — blocks
news      = await self.embed_news_articles()          # L641 — blocks 28+ min
decisions = await self.embed_trade_decisions()        # L650 — never starts until news finishes
```

**Live timing confirms** sequential execution — Phase 2 starts exactly when Phase 1 ends (both at 00:29:32).

> [!WARNING]
> **Parallelization speedup is overstated in the original plan.** The original claimed "up to 4x." Ollama serves embedding via a **single GPU instance** — four concurrent `/api/embed` calls queue internally. The real gain is overlapping DB queries + chunking of one source with GPU time of another. **Realistic speedup: ~1.3–1.5x**, not 4x.

**Report claim**: ✅ Accurate. The correction about parallelization is also accurate.

---

### Bottleneck 2: Per-Document Sleep Throttles ✅ CONFIRMED — Low Impact

**Code evidence** — three sleep sites:
| Location | Line | Delay |
|----------|------|-------|
| [embed_youtube_transcripts](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L332) | L332 | `asyncio.sleep(0.1)` |
| [embed_reddit_posts](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L402) | L402 | `asyncio.sleep(0.05)` |
| [embed_news_articles](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L495) | L495 | `asyncio.sleep(0.05)` |

**Report also missed**: [embed_trade_decisions L596](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L596) — `asyncio.sleep(0.05)` — a 4th sleep site not mentioned in the report.

**Impact**: 200 articles × 0.05s = 10s out of 1680s+ = **0.6%**. Negligible. Report's assessment is accurate.

**Report claim**: ✅ Accurate.

---

### Bottleneck 3: Per-Document HTTP Calls ⚠️ PARTIALLY WRONG — Report Correction Is Accurate

**What the report claims the original plan got wrong**: The original plan said `MAX_BATCH_SIZE` "is never hit." The report corrects this — and that correction is verified.

**Evidence from live logs** (YouTube transcript hitting MAX_BATCH_SIZE):
```
[Embedding] Batch 1/2 (32 texts) — sending to Ollama…
[Embedding] ✅ Batch 1/2 done (2.8s, 32 vectors)
[Embedding] Batch 2/2 (6 texts) — sending to Ollama…
```

The sub-batching within `embed_batch()` works correctly when a single document has many chunks. The issue is exclusively with small documents (news articles) that produce 1–3 chunks each and trigger individual HTTP calls.

**Report claim**: ✅ Correction is accurate.

---

### Bottleneck 4: No Cross-Document Batching ✅ CONFIRMED

**Code evidence** — [embed_news_articles() L463-495](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L463-L495):
```python
for row in rows:                           # one article per loop
    stored = await self.embed_and_store(   # → embed_batch() → one HTTP call
        text=content,                       # typically 1-2 chunks
    )
    await asyncio.sleep(0.05)              # then sleeps
```

**Report claim**: ✅ Accurate. This is the highest-value fix.

---

### Bottleneck 5: Per-Document DuckDB Commits ✅ CONFIRMED — Report Assessment Accurate

**Code evidence** — [embed_and_store() L241-242](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L241-L242):
```python
if stored:
    db.commit()  # called once per document
```

**Report claim**: ✅ Assessment is accurate — ~0.2s total. Crash safety concern about reducing to 4 total commits is valid. Hybrid commit (every 50 docs) recommendation is sound.

---

### "Missed Issue": No Content Length Cap ⚠️ PARTIALLY CORRECT — Acceptance Criteria Math Is Wrong

**Code evidence** — [embed_news_articles() L439](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L439): only `WHERE LENGTH(nfa.content) > 50`, no upper bound. ✅ Confirmed.

**However, the suggested acceptance criterion is mathematically wrong.**

The report states:
> "No single news article in the batch should produce more than **8 chunks** (16,384 chars max)"

**Empirical `chunk_text()` test results** (chunk_size=2048, overlap=200):

| Input size | Chunks produced |
|------------|-----------------|
| 500 chars | 1 chunk |
| 1,000 chars | 1 chunk |
| 2,000 chars | 1 chunk |
| 5,000 chars | 3 chunks |
| 10,000 chars | 6 chunks |
| **15,000 chars** | **9 chunks** |
| 50,000 chars | 27 chunks |

The chunking function uses **2048-char chunks with 200-char overlap**, not token-based or 2K-char flat splits. Due to the overlap, 15,000 chars produces **9 chunks, not 8**. And the relationship is `chunks ≈ ceil(content_len / (chunk_size - overlap))` = `ceil(content_len / 1848)`.

> [!IMPORTANT]
> **Corrected criterion**: To cap at 8 chunks, the max content length should be `8 × 1848 = ~14,784 chars` (round to **14,000 chars** for safety). Alternatively, cap at 10 chunks → **18,480 chars** max.

**Report's underlying point about unbounded article size is valid**, but the specific numbers need correction.

---

## 6th Bottleneck the Report Missed: `httpx.AsyncClient` Created Per Batch

**Code evidence** — [embed_batch() L80](file:///home/braindead/github/Lazy-Trading-Bot/app/services/embedding_service.py#L80):
```python
async with httpx.AsyncClient(timeout=120.0) as client:   # NEW connection every call
    resp = await client.post(
        f"{self.base_url}/api/embed",
        json={"model": self.model, "input": batch},
    )
```

Every call to `embed_batch()` creates a **new `httpx.AsyncClient`**, which means a new TCP connection to Ollama. For 200 news articles, this is 200 TCP connection handshakes + teardowns — all to `localhost:11434`.

This adds **~10-30ms per call** of pure connection overhead (TCP handshake + SSL-free local socket setup). For 200 calls: **2-6 seconds** of wasted connection churn.

**Fix**: Create `self._http_client` once in `__init__` (or use a class-level session) and reuse it across all `embed_batch()` calls. This is a trivial fix with zero risk.

> [!NOTE]
> This isn't the dominant bottleneck (2-6s vs 28 minutes), but it becomes meaningful after Ticket 2 (cross-document batching) reduces the total call count. If total calls drop from 200 to 13, connection overhead becomes irrelevant — but during the interim where per-doc calls still exist, a persistent client helps.

---

## Revised Priority Rankings

| Priority | Fix | Verified Impact | Risk |
|----------|-----|-----------------|------|
| **P0** | Cross-document batch collector for news | 200 HTTP calls → ~13; eliminates 10-40s HTTP overhead + GPU batching gains | Medium |
| **P1** | Content length cap `< 14000` chars in news query | Prevents single articles from generating 27+ chunks | Low |
| **P2** | Parallel source execution (`asyncio.gather`) | ~1.3-1.5x wall time — NOT 4x | Low |
| **P3** | Remove `asyncio.sleep()` throttles (all 4 sites, not 3) | Saves ~10s (~0.6%) | None |
| **P4** | Reuse `httpx.AsyncClient` instead of creating per-batch | Saves 2-6s of TCP overhead | None |
| **P5** | Hybrid DuckDB commit every 50 documents | Negligible speed gain, preserves crash safety | None |
| **P6** | Activity log events for embedding phases | Zero speed gain, high observability | None |

---

## Revised Acceptance Criteria

1. **P0**: For 200 news articles (~300 chunks), Ollama HTTP calls ≤ 15. Verified by `Batch X/Y` log lines where Y ≤ 15.
2. **P1**: ~~"8 chunks max at 16,384 chars"~~ → **Correct criterion**: No article should produce more than **8 chunks**. Given `chunk_size=2048` and `overlap=200`, this means capping content at **~14,000 chars** (not 16,384). Verified by adding `AND LENGTH(nfa.content) < 14000` to the news query, or truncating in Python before `embed_and_store()`.
3. **P2**: Total `embed_all_sources()` wall time < sum of sequential source times. NOT necessarily < 25% of sequential sum.
4. **Crash safety**: Hybrid commit every 50 documents. A mid-run kill must skip already-committed articles on re-run.
5. **No regression**: LEFT JOIN dedup queries must still correctly skip already-embedded documents.
6. **Sleep sites**: All **4** sleep sites removed (not 3 — the report missed `embed_trade_decisions` L596).
