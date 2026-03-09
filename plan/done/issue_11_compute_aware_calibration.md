# Issue 11 — Compute-Aware Calibration (Context vs Throughput)

**Severity:** HIGH  
**Root Cause:** Calibration finds the maximum ctx that doesn't OOM, but ignores inference throughput. On a 64GB Jetson with a 32B model, it loads at 65K ctx — consuming nearly all VRAM for KV cache and leaving nothing for compute. Every inference takes 50+s and times out.

## The Problem

Current calibration asks: *"What's the biggest ctx that won't crash?"*  
It should ask: *"What's the biggest ctx that keeps inference fast?"*

### Math (olmo-3:32b on 64GB Jetson as example)

| Component | Size |
|-----------|------|
| Total VRAM | 64 GiB |
| OS reserve | 5 GiB |
| Safe ceiling | 59 GiB |
| Model weights (Q4) | ~18 GiB |
| Graph overhead | 0.5 GiB |
| **Available for KV + compute** | **40.5 GiB** |

**Current behavior:** Uses all 40.5 GiB for KV cache → max ctx ~65K → inference crawls  
**Desired behavior:** Reserve 30% of safe ceiling for compute → KV gets ~22.8 GiB → optimal ctx ~36K → fast inference

## Formula

```
compute_reserve = safe_ceiling * 0.30      # 30% of usable VRAM for compute
kv_budget = safe_ceiling - weights - graph - compute_reserve
optimal_ctx = kv_budget / kv_bytes_per_token
optimal_ctx = clamp(optimal_ctx, 2048, desired_ctx)
```

The 30% reserve gives the GPU room for:

- Attention score computation buffers
- Intermediate activation tensors  
- CUDA workspace allocations
- Memory fragmentation headroom

## Files to Modify

### 1. `app/services/llm_service.py`

#### FAST PATH (line ~912): Add compute-aware capping

- After `load_ctx = min(desired_ctx, proven_max_ctx)`, also cap to `compute_optimal_ctx`
- The proven_max_ctx stays as a hard ceiling, but we load at the *lower* of (proven_max, compute_optimal, desired)

#### AUDIT PATH (line ~1035): Replace "step until OOM" with formula

- Calculate `compute_optimal_ctx` using the formula above
- Use it as the `proven_max_ctx` — no need to probe higher
- Still do ONE load test at the calculated ctx to verify it works
- If it fails, step down to the next lower audit step (graceful fallback)

#### New static method: `calculate_compute_optimal_ctx()`

- Takes: model_file_size, kv_bytes_per_token, safe_ceiling
- Returns: optimal ctx that leaves 30% headroom for compute
- Used by both fast path and audit path

### 2. Cache format update

- Save `compute_optimal_ctx` alongside `proven_max_ctx` in the VRAM measurements cache
- The `compute_optimal_ctx` is what actually gets loaded; `proven_max_ctx` is informational only

## Verification

### Automated Tests

- `ruff check app/services/llm_service.py --select E,W,F`
- `pytest tests/test_trading_agent.py tests/test_trade_action.py -v`

### Manual Verification

- After the fix, trigger recalibration from the Settings UI
- Verify the log shows: `compute_optimal_ctx` < `proven_max_ctx`
- Verify inference doesn't timeout (trading phase completes in reasonable time)
