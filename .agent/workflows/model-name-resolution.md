---
description: How Ollama model name resolution works — NEVER break this pattern
---

# Ollama Model Name Resolution

## The Problem

Bots store model names in **HuggingFace format** (e.g. `ibm/granite-3.2-8b`) but Ollama uses **its own naming** (e.g. `granite3.2:8b`). These MUST be resolved automatically.

## The Solution: `_norm()` Normalization

Both `llm_service.py` and `main.py` use the same normalization function:

```python
def _norm(n: str) -> str:
    if "/" in n:
        n = n.split("/", 1)[1]       # Strip vendor prefix: "ibm/granite-3.2-8b" → "granite-3.2-8b"
    return re.sub(r"[.\-:_]", "", n).lower()  # Remove separators: "granite-3.2-8b" → "granite328b"
```

### Examples

| Input | Normalized |
|-------|-----------|
| `ibm/granite-3.2-8b` | `granite328b` |
| `granite3.2:8b` | `granite328b` |
| `granite3.2:8b-50k` | `granite328b50k` |
| `meta/llama-3.1-8b` | `llama318b` |

## Where It Lives

1. **`llm_service.py` → `verify_and_warm_ollama_model()`** (lines ~1159-1190)
   - Runs during model pre-warm
   - Does normalized fuzzy match against Ollama's `/api/tags`
   - First match wins, model name is swapped to the Ollama name

2. **`main.py` → `_resolve_model_name()`** (lines ~2610-2640)
   - Runs during `run_all_bots`
   - Same normalization logic
   - Has additional substring matching for partial names

## Rules — DO NOT BREAK

1. **NEVER compare raw model names** — always normalize first
2. **NEVER assume the DB name matches the Ollama name** — they come from different sources
3. **The `/` (vendor prefix) MUST be stripped** — Ollama doesn't use vendor prefixes
4. **Separators `.` `-` `:` `_` MUST be removed** — different sources use different separators
5. **If you add a new model resolution path, use `_norm()` or equivalent**
6. **If either function changes, the other MUST be updated to match**
7. **The resolved name MUST be stored back to `settings.LLM_MODEL`** — `autonomous_loop.py` does this after `verify_and_warm_ollama_model()` via `warm_result["base_model"]`. Without this, Prism calls still send the unresolved name and fail with 404.
8. **Use `base_model`, NOT `model`, from the warm result** — `model` may be an ephemeral `_tradingbot` wrapper name, `base_model` is the real Ollama name.
