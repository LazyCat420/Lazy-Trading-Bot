# Parallel Streaming Pipeline — Per-Ticker Pipelining

> **Goal**: Maximize throughput by processing each ticker through the full pipeline
> independently, so the LLM is working on ticker A's analysis while ticker B's
> data is still being collected and ticker C is still being discovered.

---

## The Problem: Sequential Phase Gates

### Current Architecture (`autonomous_loop.py`)

```
     Phase 1            Phase 2           Phase 3            Phase 4
  ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ Discovery│ ──→  │  Import  │ ──→  │ Analysis │ ──→  │ Trading  │
  │ ALL ticks│      │ ALL ticks│      │ ALL ticks│      │ ALL ticks│
  └──────────┘      └──────────┘      └──────────┘      └──────────┘
       12s               2s               90s               10s
```

**Every ticker waits** for all tickers to finish the current phase before any
ticker can start the next phase. If discovery finds 8 tickers, the LLM sits
idle for 14s (discovery + import) before analyzing ticker #1.

### The Bottleneck Breakdown

| Phase | Wall Clock | Bottleneck | Can Overlap? |
|-------|-----------|------------|-------------|
| Discovery (Reddit + YT scraping) | ~12s | HTTP/scraping (I/O-bound) | ✅ Overlap with Collection |
| Data Collection (12 yFinance steps) | ~8s per ticker | HTTP/yfinance (I/O-bound) | ✅ Overlap with Analysis |
| Deep Analysis (4-layer LLM funnel) | ~30-45s per ticker | LLM inference (GPU-bound) | ✅ Start as soon as data is ready |
| Trading (signal routing + execution) | ~1s per ticker | CPU-bound (fast) | ✅ Start as soon as dossier is ready |

**Key insight**: The LLM is the slowest stage (~30-45s per ticker). Every second
the LLM sits idle while data collection or discovery runs is wasted GPU time.

---

## Industry Standard: The Streaming Pipeline Pattern

Based on web research, the recommended pattern for this use case is:

### `asyncio.Queue` Producer-Consumer with Fan-Out/Fan-In

```
   Producer          Queue          Workers            Queue         Workers
  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │Discovery│    │ collect_ │    │ Collector│    │ analysis_│    │ Analyzer │
  │ Scanner │──→ │ queue    │──→ │ Worker 1 │──→ │ queue    │──→ │ Worker 1 │
  │         │    │          │    │ Worker 2 │    │          │    │ (LLM)    │
  │         │    │          │    │ Worker 3 │    │          │    │          │
  └─────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                       │
       Fan-Out: Multiple collectors           Fan-In: Bounded LLM      │
       can fetch data concurrently             concurrency=2           ▼
                                                                  ┌──────────┐
                                                                  │ trade_   │
                                                                  │ queue    │
                                                                  └──────────┘
```

This is the **standard** pattern for multi-stage async pipelines where:

- Each stage has different throughput characteristics
- Items can be processed independently
- The bottleneck stage (LLM) needs backpressure management

### Why This Works for Our Use Case

1. **Tickers are independent** — $NVDA's analysis doesn't depend on $TSLA's data
2. **Stages have different speeds** — scraping (fast), collection (medium), LLM (slow)
3. **LLM is the bottleneck** — we want it 100% utilized at all times
4. **All I/O-bound** — asyncio natively handles concurrent HTTP without threads

---

## Proposed Architecture

### The Streaming Pipeline

```python
# Conceptual flow — each ticker flows through stages independently

async def run_streaming_pipeline():
    collect_q = asyncio.Queue(maxsize=20)   # backpressure
    analyze_q = asyncio.Queue(maxsize=5)    # LLM rate-limit
    trade_q   = asyncio.Queue(maxsize=10)

    # 1) Discovery produces tickers into collect_q
    producer = asyncio.create_task(
        discovery_producer(collect_q)
    )

    # 2) N collector workers pull from collect_q → push to analyze_q
    collectors = [
        asyncio.create_task(
            collection_worker(collect_q, analyze_q)
        )
        for _ in range(4)  # 4 concurrent data collectors
    ]

    # 3) M analysis workers pull from analyze_q → push to trade_q
    analyzers = [
        asyncio.create_task(
            analysis_worker(analyze_q, trade_q)
        )
        for _ in range(2)  # 2 concurrent LLM workers (GPU bound)
    ]

    # 4) Trade worker processes signals as they arrive
    trader = asyncio.create_task(
        trading_worker(trade_q)
    )

    # Wait for pipeline to drain
    await producer
    await collect_q.join()
    await analyze_q.join()
    await trade_q.join()
```

### Stage Details

#### Stage 1: Discovery Producer

```python
async def discovery_producer(out_q: asyncio.Queue):
    """Discover tickers and push each one to the collection queue immediately."""
    result = await discovery_service.run_discovery(
        enable_reddit=True,
        enable_youtube=True,
    )
    # Auto-import decides which tickers qualify
    qualified = watchlist_manager.import_from_discovery(
        min_score=3.0, max_tickers=10,
    )
    for ticker_info in qualified["imported"]:
        await out_q.put(ticker_info["ticker"])
    # Sentinel: signal that discovery is done
    await out_q.put(None)
```

> **Key**: Each ticker is pushed to the queue **immediately** after being
> qualified. The first ticker enters data collection while discovery might
> still be finding more tickers (if they come from YouTube transcripts).

#### Stage 2: Collection Workers (fan-out ×4)

```python
async def collection_worker(in_q: asyncio.Queue, out_q: asyncio.Queue):
    """Pull tickers from queue, collect all 12 data types, push to analysis."""
    while True:
        ticker = await in_q.get()
        if ticker is None:
            await in_q.put(None)  # propagate sentinel
            in_q.task_done()
            break
        try:
            # PipelineService.run() already parallelizes steps 1-9 internally
            pipeline = PipelineService()
            result = await pipeline.run(ticker, mode="data")
            await out_q.put(ticker)  # ticker now has all data in DuckDB
        except Exception as e:
            logger.error("Collection failed for %s: %s", ticker, e)
        in_q.task_done()
```

> **Why 4 workers?** Data collection is I/O-bound (HTTP calls to yFinance,
> Google News, YouTube). 4 concurrent collectors keep the network saturated
> without rate-limiting issues. Each worker runs Steps 1-12 for one ticker
> at a time, with internal parallelism on Steps 1-9.

#### Stage 3: Analysis Workers (fan-out ×2, LLM-bounded)

```python
async def analysis_worker(in_q: asyncio.Queue, out_q: asyncio.Queue):
    """Pull data-ready tickers, run 4-layer deep analysis."""
    deep = DeepAnalysisService()
    while True:
        ticker = await in_q.get()
        if ticker is None:
            await in_q.put(None)
            in_q.task_done()
            break
        try:
            dossier = await deep.analyze_ticker(ticker)
            await out_q.put({
                "ticker": ticker,
                "conviction": dossier.conviction_score,
                "dossier": dossier,
            })
        except Exception as e:
            logger.error("Analysis failed for %s: %s", ticker, e)
        in_q.task_done()
```

> **Why only 2?** LLM inference is GPU-bound. Each ticker needs 7 LLM calls
> (1 question gen + 5 RAG answers + 1 synthesis). Running 2 concurrent
> ensures the LLM is always busy while one waits for I/O, but doesn't
> overwhelm VRAM. This matches the existing `analyze_batch(concurrency=2)`.

#### Stage 4: Trading Worker (fan-in ×1)

```python
async def trading_worker(in_q: asyncio.Queue):
    """Process each analyzed ticker through signal router immediately."""
    paper_trader = PaperTrader()
    signal_router = SignalRouter()
    while True:
        item = await in_q.get()
        if item is None:
            in_q.task_done()
            break
        # Execute trading logic for this ticker
        # (same code from current _do_trading, but per-ticker)
        ...
        in_q.task_done()
```

> **Why 1 worker?** Trading must be sequential to maintain accurate
> portfolio state (cash balance, position limits, daily order counts).
> But it's fast (~1s per ticker) so it's never the bottleneck.

---

## Timeline Comparison

### Before: Sequential Phases (current)

```
Time:  0s    12s  14s                         104s   114s
       ├──────┤──┤──────────────────────────────┤──────┤
       │ Disc │Im│     Analysis (all 8 tickers) │Trade │
       │ ALL  │  │     waiting for ALL to finish│ ALL  │
       └──────────────────────────────────────────────┘
                                          Total: ~114s
       LLM idle: 14s (discovery + import)
```

### After: Streaming Pipeline

```
Time:  0s    3s     8s    11s    16s   41s   46s    54s   84s
       ├─────┼──────┼──────┼──────┼─────┼─────┼──────┼─────┤
  Disc │█████│ disc continues...            │               │
  Coll │     │██ T1 │██ T2 │██ T3 │ ...     │               │
  LLM  │     │      │████████ T1 ██│████████ T2 ██│████ T8 ██│
 Trade │     │      │              │ T1 ✓   │ T2 ✓ │ ... T8 ✓│
       └─────────────────────────────────────────────────────┘
                                          Total: ~84s (26% faster)
       LLM idle: ~3-8s (just until first ticker's data is ready)
```

**Savings breakdown**:

- LLM starts **~3s into the run** (vs. 14s before) → **11s saved**
- Tickers overlap across stages → overall wall clock drops ~26%
- With more tickers, savings compound further (pipeline stays full)

---

## Implementation Plan

### Files to Modify

#### [NEW] `app/services/streaming_pipeline.py`

New orchestrator that replaces the sequential `run_full_loop()`. Contains:

- `StreamingPipeline` class with stage workers
- Queue setup with backpressure (`maxsize`)
- Sentinel propagation for clean shutdown
- Per-ticker progress tracking for the frontend

#### [MODIFY] `app/services/autonomous_loop.py`

- `run_full_loop()` delegates to `StreamingPipeline.run()` instead of
  calling `_do_discovery()` → `_do_import()` → ... sequentially
- Keep `_state` and `_log()` mechanism for frontend polling
- Add a `mode` parameter: `"streaming"` (new) vs `"sequential"` (legacy fallback)

#### [MODIFY] `app/main.py`

- No endpoint changes needed — `POST /api/bot/run-loop` already invokes
  `_loop.run_full_loop()` which will use the new streaming pipeline internally
- Add optional query param `mode=streaming|sequential` for A/B testing

### Concurrency Configuration

```python
# Tunable constants in streaming_pipeline.py
COLLECTION_WORKERS = 4      # I/O-bound: 4 concurrent yFinance fetchers
ANALYSIS_WORKERS = 2        # GPU-bound: 2 concurrent LLM pipelines  
COLLECT_QUEUE_SIZE = 20     # Backpressure: max tickers waiting for collection
ANALYSIS_QUEUE_SIZE = 5     # Backpressure: max tickers waiting for LLM
TRADE_QUEUE_SIZE = 10       # Buffer for trading decisions
```

> [!IMPORTANT]
> `ANALYSIS_WORKERS` should be adjusted based on LLM backend capacity.
> With local Ollama on a single GPU: **2**. With remote API: could be **4-6**.

---

## Safety: Ensuring Data is Ready

The user's core concern: **"we need to make sure the LLM isn't processing
stocks that are still working to collect all the data."**

### How the Queue Pattern Prevents This

```
                                STRICT GATE
                                    │
   Collection Worker                │    Analysis Worker
   ┌─────────────────┐             │    ┌──────────────────┐
   │ 1. Fetch prices  │             │    │ Reads from DuckDB │
   │ 2. Fetch fundas  │             │    │ (data MUST exist) │
   │ 3. Fetch balance │             │    │                    │
   │ ...              │             │    │ Layer 1: Quant     │
   │ 12. YouTube      │             │    │ Layer 2: Questions │
   │                  │   put()     │    │ Layer 3: RAG       │
   │ ALL 12 COMPLETE ─┼──→ queue ──→│──→ │ Layer 4: Dossier   │
   │                  │             │    │                    │
   └─────────────────┘             │    └──────────────────┘
                                    │
                          A ticker ONLY enters
                          the queue after ALL
                          data collection has
                          completed successfully
```

**Guarantees:**

1. **Queue = gate** — a ticker is only put into `analyze_q` AFTER
   `PipelineService.run(ticker, mode="data")` returns successfully
2. **PipelineService already validates** — it tracks status per-step and
   reports errors. Tickers that fail critical steps can be excluded
3. **DeepAnalysis reads from DuckDB** — the quant engine queries
   `price_history`, `technicals`, etc. directly from DuckDB. If the data
   isn't there, it simply gets no results (safe degradation)
4. **No shared mutable state** — each ticker's data is independent in DuckDB.
   Ticker A's collection can't corrupt ticker B's analysis

### Failure Handling

```python
async def collection_worker(in_q, out_q):
    ticker = await in_q.get()
    result = await pipeline.run(ticker, mode="data")

    # Only promote if critical data exists
    critical_steps = ["price_history", "fundamentals"]
    critical_ok = all(
        result.status.get(s, {}).get("status") == "ok"
        for s in critical_steps
    )
    if critical_ok:
        await out_q.put(ticker)          # safe to analyze
    else:
        log_event("collection", "collection_incomplete", ticker=ticker,
                  detail=f"Missing critical data, skipping analysis",
                  status="warning")
```

---

## Frontend Integration

The existing loop progress panel already polls `GET /api/bot/loop-status`.
We extend the state to show per-ticker streaming progress:

```python
# Extended _state structure
{
    "running": True,
    "mode": "streaming",         # NEW
    "phase": "pipeline",         # single phase now
    "phases": {
        "pipeline": "running"
    },
    "ticker_status": {           # NEW: per-ticker progress
        "NVDA": "trading",       # completed analysis, in trading
        "TSLA": "analyzing",     # LLM working on this
        "INTC": "collecting",    # data collection in progress
        "AMD":  "queued",        # waiting for collector slot
    },
    "counters": {                # NEW: aggregate progress
        "discovered": 8,
        "collected": 3,
        "analyzed": 1,
        "traded": 0,
    },
    "log": [...],
}
```

The frontend loop progress panel already renders `loopStatus.log` — we add
a per-ticker progress bar showing each ticker moving through stages.

---

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| LLM overwhelmed (2 concurrent) | Low | High — OOM or slow | `asyncio.Semaphore(2)` caps concurrency. Configurable. |
| yFinance rate-limiting | Medium | Medium — stalled collection | Existing daily guards prevent redundant calls. 4 workers max. |
| DuckDB write contention | Low | Low — DuckDB handles concurrent writers | WAL mode + row-level independence. No ticker shares rows. |
| Discovery takes long, pipeline starves | Low | Low — pipeline just waits | Queue `get()` is blocking — workers naturally wait for input. |
| Ticker promoted before data is ready | Very Low | High — bad analysis | Queue gate pattern (above) prevents this by design. |
| Trading order conflicts (parallel trades) | Medium | High — double-buy | Single trading worker (×1) ensures sequential order execution. |
| Pipeline never drains (stuck workers) | Low | High — loop hangs | Add `asyncio.wait_for()` timeouts per-ticker (e.g., 120s). |

---

## Dependencies

- **No new packages** — this uses only Python's built-in `asyncio.Queue`
- **Compatible with existing code** — `PipelineService.run()` and
  `DeepAnalysisService.analyze_ticker()` are used as-is
- **Backward-compatible** — `mode="sequential"` flag preserves the old behavior

---

## When to Build This

### Build Now If

- Loop runs take >2 minutes with 5+ tickers
- LLM idle time is noticeable in logs (check for gaps between analysis starts)
- You plan to scale to 15-20 tickers per loop

### Can Wait If

- Running <5 tickers per loop
- Total loop time is under 2 minutes
- The sequential approach feels fast enough

### Estimated Effort

- `streaming_pipeline.py`: ~150 lines (new file)
- `autonomous_loop.py` integration: ~30 lines modified
- Frontend progress panel: ~50 lines (extend existing)
- **Total: ~230 lines, ~4 hours**
