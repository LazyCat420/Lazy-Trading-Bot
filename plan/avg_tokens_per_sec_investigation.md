# Investigation: Avg Tokens/Sec Discrepancy — Lazy Bot (29.4) vs Prism (1000s)

## Status: DONE ✅ — Corrected with dev feedback

## Summary

The two systems calculate `tokens per second` using **different time windows**, measuring different (but both valid) metrics.

---

## How Lazy-Trading-Bot calculates tok/s (~29.4 tok/s)

**Location:** `app/main.py` lines 3742-3748

```sql
SUM(tokens_used) * 1000.0 / NULLIF(SUM(execution_time_ms), 0) AS avg_tok_per_sec
```

- `tokens_used` = `len(output_text) // 4` (rough estimate)
- `execution_time_ms` = full wall-clock time (includes network, prefill, everything)
- Result: **end-to-end throughput** — realistic ~29.4 tok/s

---

## How Prism calculates tok/s (showing 1000s)

**Location:** `src/routes/chat.js` lines 561-565

```javascript
const tokensPerSec = usage.tokensPerSec
    ? usage.tokensPerSec.toFixed(1)
    : generationSec && generationSec > 0
        ? (usage.outputTokens / generationSec).toFixed(1)
        : "N/A";
```

- `outputTokens` = vLLM's `completion_tokens` (actual server-reported output tokens ← **correct, NOT inflated by prefill**)
- `generationSec` = `generationEnd - firstTokenTime` = time between first text chunk and last text chunk
- Result: **decode throughput** — how fast the model generates once it starts

### Why it shows 1000+ for short outputs

For short responses (e.g. 200-token JSON), vLLM generates tokens in a burst. The `generationSec` window (first-to-last text chunk) might be only 0.1-0.3s, so `200 / 0.15 = 1333 tok/s`. This is an **accurate measure of decode speed** for that burst — it's just not what users expect to see as "throughput."

---

## Dev Corrections on Original Investigation

### ✅ Correct in original report
- vLLM provider doesn't set `tokensPerSec` — confirmed at `vllm.js:144-147`
- Fallback formula is `usage.outputTokens / generationSec` — confirmed at `chat.js:561-565`
- `generationEnd` only updated for text chunks (line 520) — usage/thinking/image chunks all `continue` before reaching it

### ❌ Wrong in original report
- **"completion_tokens includes prefill tokens"** — WRONG. `completion_tokens` is strictly output tokens. Prefill = `prompt_tokens`. The numerator is correct.
- **"Bug" characterization** — MISLEADING. `generationSec` (first-text-chunk to last-text-chunk) is the standard Time Between Tokens (TBT) window. It intentionally excludes TTFB/prefill because those aren't generation. Both metrics are valid:

| Metric | Formula | What it measures |
|--------|---------|-----------------|
| Decode throughput | `outputTokens / generationSec` | How fast the model generates once it starts |
| End-to-end throughput | `outputTokens / totalSec` | Full request throughput including network + prefill |

### Why `totalSec` would be worse
Using `totalSec` would penalize tok/s with network latency, prefill time, and post-processing — none of which reflect generation speed. For large-prompt / short-output requests, this makes tok/s look artificially slow.

---

## vLLM server observations

- vLLM SSE stream does NOT include per-request timing metadata — only `usage` with `prompt_tokens`, `completion_tokens`, `total_tokens`
- vLLM exposes Prometheus metrics (`/metrics`) with aggregate histograms for TTFT, TPOT, e2e latency — but not per-request in the API response
- Server-reported generation speed per-request is NOT available from vLLM's OpenAI-compatible API

---

## Applied Fix: Clamp to sane maximum

Since vLLM doesn't report per-request speed, and `generationSec` is legitimately tiny for burst responses, the best pragmatic fix is to clamp the displayed tok/s to a sane maximum to avoid the "1000+" display artifact.

**Approach:** Keep `outputTokens / generationSec` as the calculation (it IS the actual decode speed), but clamp the displayed value to a configurable maximum (e.g. 200 tok/s for local models) to prevent confusing dashboard numbers.

**Alternative considered:** Use `totalSec` — rejected because it undershoots by penalizing with network + prefill overhead.
