# Olmo-3:32b Failure Investigation — Findings & Plan

## Summary

After analyzing the full bot run log (`trading_bot_2026-03-11_23-42-32.log`, ~21K lines, 4+ models), here is a definitive model performance comparison and root cause analysis for the olmo-3:32b failures.

---

## Model Performance Comparison

Data from the most recent full run (March 11–12, 2026):

| Model | Requests | Success | Empty/Fail | Success Rate | Avg Response Time | Avg Output | Avg Thinking | Avg Empty Wait |
|---|---|---|---|---|---|---|---|---|
| **granite3.2:8b-50k** | 185 | 185 | 0 | **100%** | 14.1s | 331 chars | 0 | N/A |
| **nemotron-3-nano:latest** | 100 | 100 | 0 | **100%** | 60.5s | 266 chars | 3,109 chars | N/A |
| **gpt-oss-safeguard:20b** | 236 | 232 | 2 | **99%** | 41.7s | 411 chars | 1,876 chars | 264s |
| **olmo-3:latest** | 138 | 94 | 40 | **70%** | 111.6s | 540 chars | 6,532 chars | 314s |
| **olmo-3:32b** | 33 | 12 | 19 | **39%** | 167.3s | 173 chars | 3,145 chars | **1,647s** |
| qwen-claude-165k:latest | 1 | 1 | 0 | 100% | 212.0s | 1,568 chars | 10,474 chars | N/A |

> [!CAUTION]
> **olmo-3:32b fails 61% of the time** and wastes an average of **27 minutes per failed request** (max: 37 min). Even when it succeeds, it only outputs 173 chars (the least useful of any model).

---

## Root Cause Analysis

### It's NOT a VRAM problem
- olmo-3:32b needs 20.6 GiB / 64.0 GiB available → only 32% utilization
- All models fit comfortably in VRAM

### It IS a "thinking loop" problem
The Prism/Retina screenshot confirms the pattern: olmo-3:32b produces **thinking tokens but zero output tokens**. From the logs:

```
olmo-3:32b returned empty after 550.9s
olmo-3:32b returned empty after 750.1s
olmo-3:32b returned empty after 1183.6s
olmo-3:32b returned empty after 1502.4s
olmo-3:32b returned empty after 1826.2s
olmo-3:32b returned empty after 2247.0s  ← 37+ minutes wasted!
```

The model enters a **reasoning/thinking loop** where it generates internal chain-of-thought tokens endlessly but never produces a visible output token. This is a known characteristic of certain "thinking" models when given complex structured-output prompts (like our JSON trading format).

### olmo-3:latest also has problems (but less severe)
- 70% success rate (vs 39% for the 32b variant)
- ~40 failed requests averaging 314s each
- The smaller olmo-3 variant has the same fundamental issue but recovers more often

---

## Recommendation

> [!IMPORTANT]
> **Remove olmo-3:32b from the bot roster. Do NOT spend time trying to fix it.**

### Why removal is the right call:

1. **The problem is in the model, not in your code.** The exact same prompts work perfectly on granite3.2, nemotron, and gpt-oss-safeguard. The model simply gets stuck in internal reasoning loops.

2. **Even when it works, it's bad.** Its 12 successful responses averaged only 173 output chars — the lowest quality of any model. Compare to gpt-oss-safeguard at 411 chars or olmo-3:latest at 540 chars.

3. **The cost is prohibitive.** Each failed request wastes 27 minutes of GPU time. Over today's run, olmo-3:32b wasted approximately **8.7 hours** of GPU time on failed requests alone (`19 failures × 1647s avg = 31,293s`).

4. **The fallback system masks the damage.** When olmo-3:32b fails, it falls back to nemotron-3-nano:latest, meaning the 32b model contributes essentially nothing while burning GPU hours.

### What about olmo-3:latest (the smaller one)?

This is a judgment call:
- **Keep it** if you want model diversity — it does produce useful output 70% of the time with high thinking depth (6,532 avg thinking chars)
- **Remove it** if you want reliability — its 30% failure rate and 5-min failure waits are still significant
- **Compromise**: Keep it but add a **stricter timeout** (e.g., 120s instead of 300s) so failures don't burn as much time

### Possible code improvements (optional, for ALL models):

If you want to keep experimenting with thinking-heavy models in the future:
1. **Add a "thinking timeout"** — if the model has been generating thinking tokens for >60s without producing any output tokens, abort early
2. **Add a per-model max timeout** — let smaller fast models have 120s timeout, larger slow ones 180s
3. **Track thinking-to-output ratio** as a performance metric — flag models that think a lot but produce little

---

## Action Items

- [ ] Remove olmo-3:32b from the bot registry (either via UI or API)
- [ ] Decide on olmo-3:latest: keep with stricter timeout, or remove
- [ ] (Optional) Implement per-model timeout configuration
- [ ] (Optional) Add thinking-timeout abort mechanism
