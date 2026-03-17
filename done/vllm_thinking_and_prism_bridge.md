# Plan: Fix vLLM Thinking Display + Build vLLM → Prism Bridge

## Problem 1: Thinking Content Cutoff in Diagnostics
- The `raw_response` saved by `LLMAuditLogger` is the FINAL content returned by `_send_vllm_request()`
- When vLLM's `content` field is empty and thinking/reasoning was used as fallback, the raw reasoning text becomes the response
- The response gets truncated at multiple levels:
  - DB insert: `raw_response[:50_000]` (50K chars — fine)
  - Frontend system_prompt: `.substring(0, 2000)` (2K chars)
  - Frontend user_context: `.substring(0, 3000)` (3K chars)
  - Frontend response: `.substring(0, 3000)` (3K chars — this is the bottleneck)
  - Frontend response container: `max-h-40` (very short) — MAIN ISSUE
- The thinking/reasoning content is lost — it's never stored separately in the audit log

### Fix:
1. Store `reasoning_content` separately in the audit log
2. Frontend: Show thinking in a collapsible section, increase response display limit
3. Frontend: Remove the hard `max-h-40` cap, make it scrollable

## Problem 2: vLLM → Prism Bridge
- vLLM calls bypass Prism entirely — no conversation tracking, no admin visibility
- Solution: After each vLLM call, POST the prompt/response to Prism as a conversation
- Use Prism's existing POST /chat endpoint with conversationMeta to auto-create conversations
- This happens as a non-blocking fire-and-forget after the vLLM call returns

### Checklist:
- [x] Create `PrismBridge` service to format vLLM calls for Prism ingestion
- [x] Hook it into `_send_vllm_request()` (non-blocking, fire-and-forget)
- [x] Fix frontend truncation: increase limits, add thinking section
- [x] Store thinking/reasoning separately in audit log DB schema
