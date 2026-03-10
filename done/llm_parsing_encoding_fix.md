# Fix LLM Parsing Failures and UTF-8 Mojibake

The LLM backend has two interrelated bugs: (1) reasoning models output `<think>...</think>` blocks that break JSON parsing in the thinking-model fallback path, and (2) UTF-8 characters (em-dashes, arrows) are rendered as mojibake (`ÃƒÆ'Ã‚Â¢...`) in logs and source files due to encoding mishandles.

## Proposed Changes

### LLM Service — Parsing Fixes

#### [MODIFY] [llm_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/llm_service.py)

**1. Strip `<think>` blocks in thinking-model fallback (lines 388–418)**

The `clean_json_response()` method already strips `<think>` tags (line 488), but the fallback path at line 392–418 receives the raw `thinking` field and tries to parse JSON from it without stripping first. When parsing fails, it falls back to the raw thinking text (7,500 chars of noise).

```diff
-        if not content.strip() and thinking.strip():
-            # Try to find a JSON object or array in the thinking
-            import json as _json
-
-            # Reuse our robust brace-depth-counting extractor
-            # instead of a regex that breaks on nested JSON
-            candidate = LLMService.clean_json_response(thinking)
+        if not content.strip() and thinking.strip():
+            import json as _json
+
+            # Strip <think>...</think> tags from thinking text first
+            clean_thinking = re.sub(
+                r"<think>.*?</think>", "", thinking, flags=re.DOTALL,
+            ).strip()
+            # Use original thinking if stripping removed everything
+            text_to_parse = clean_thinking or thinking
+
+            candidate = LLMService.clean_json_response(text_to_parse)
```

**2. Log extracted JSON keys on success (line 403–406)**

Replace raw char-count log with extracted key names for cleaner output.

```diff
-                    content = candidate
-                    logger.info(
-                        "[LLM] Extracted JSON from thinking field (%d chars)",
-                        len(content),
-                    )
+                    content = candidate
+                    _keys = list(_json.loads(candidate).keys())[:5]
+                    logger.info(
+                        "[LLM] Extracted JSON from thinking field: keys=%s (%d chars)",
+                        _keys,
+                        len(content),
+                    )
```

**3. Force UTF-8 on httpx response (line 382)**

```diff
         data = resp.json()
```
httpx already decodes response bodies as UTF-8 by default (unlike `requests`). The `resp.json()` call uses the charset from headers. Since Ollama responses are always JSON/UTF-8, no explicit encoding override is needed — httpx handles this correctly. **No change required here.**

**4. Fix mojibake in comments/docstrings**

Replace all `ÃƒÆ'Ã‚Â¢...` sequences with their intended Unicode characters (`→`, `—`, `×`, etc.) throughout the file. These are source-level encoding corruption from a previous editor session and don't affect runtime behavior, but they pollute log output when used in log format strings.

---

### Scanner Prompts — JSON Enforcement

#### [MODIFY] [ticker_scanner.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/ticker_scanner.py)

Add strict JSON-only instruction to the system prompt (line 226):

```diff
-                system=("You are a stock ticker extraction tool. Return ONLY valid JSON."),
+                system=(
+                    "You are a stock ticker extraction tool. "
+                    "Return ONLY raw, valid JSON. Do not include markdown "
+                    "formatting, code blocks like ```json, or conversational text."
+                ),
```

#### [MODIFY] [reddit_service.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/reddit_service.py)

Add strict JSON-only instruction to the system prompt (line 134):

```diff
-                system="You are a financial thread filter. Return ONLY valid JSON.",
+                system=(
+                    "You are a financial thread filter. "
+                    "Return ONLY raw, valid JSON. Do not include markdown "
+                    "formatting, code blocks like ```json, or conversational text."
+                ),
```

---

### Logger — UTF-8 Console Fix

#### [MODIFY] [logger.py](file:///home/braindead/github/Lazy-Trading-Bot/app/utils/logger.py)

File handlers already use `encoding='utf-8'`. The console `StreamHandler(sys.stdout)` inherits the terminal encoding which on WSL can be non-UTF-8. Fix by wrapping stdout:

```diff
+    # Ensure console output is UTF-8 (critical on WSL)
+    import io
+    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
-    console = logging.StreamHandler(sys.stdout)
+    console = logging.StreamHandler(utf8_stdout)
```

## Verification Plan

### Automated Tests

1. **Existing tests** — Run full test suite to verify no regressions:
   ```bash
   cd /home/braindead/github/Lazy-Trading-Bot && venv/bin/python -m pytest tests/ -x -q 2>&1 | head -80
   ```

2. **Existing `<think>` tests** — Already covered by `TestThinkBlockStripping` in `test_portfolio_strategist.py` (lines 435–476). These test `clean_json_response()` which was already correct.

3. **New test** — Add a test for the thinking-model fallback path in `_send_ollama_request()` to verify `<think>` tags inside the `thinking` field are stripped before the raw-text fallback. This will be added to `tests/test_portfolio_strategist.py` as a new test class.
