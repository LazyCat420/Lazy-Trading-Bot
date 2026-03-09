# Fix: Hardware Limit Stuck at 2,048 in Frontend UI

## Problem

The Settings UI showed "Hardware Limit: 2,048 tokens" for `olmo-3:latest`, even though the backend loads the model at 8,192 ctx. The slider max was locked to 2,048.

## Root Cause

A stale `proven_max_ctx: 2048` was cached in `llm_config.json` from an initial VRAM audit where all context sizes above 2k failed (likely due to temporary memory pressure). The backend has a `max(load_ctx, 8192)` floor so it loads fine, but the frontend reads the unclamped cached value.

## Fixes Applied

### 1. `app/services/llm_service.py` — Audit floor

- `proven_max_ctx = max(last_successful_ctx, 8192)` — the audit path now floors the saved value at 8192, matching the load floor.

### 2. `app/main.py` — vram-estimate endpoint floor

- `/api/llm/vram-estimate` now floors `proven_max_ctx` at 8192 before sending to frontend, so even stale cache entries produce correct slider max and badge values.

### 3. `app/user_config/llm_config.json` — Stale cache cleared

- Removed the stale `olmo-3:latest` entry from `vram_measurements`. Next Save Config will trigger a fresh VRAM audit.

## Verification

- Restart server, open Settings → the "Hardware Limit" badge should disappear (no cached audit)
- Click Save Config → a new VRAM audit runs → proven_max_ctx will be ≥ 8192
