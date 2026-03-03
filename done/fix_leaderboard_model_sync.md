# Fix: Leaderboard Model Selection Not Syncing to Run Loop

## Root Cause

`_set_active_bot()` in `main.py` only set the `_active_bot_id` variable but **never** synced
`settings.LLM_MODEL` or any LLM config. So clicking a bot on the leaderboard marked it "ACTIVE"
visually, but `run_full_loop` still used whichever model was in global settings.

## Fix Applied

Patched `_set_active_bot()` to look up the bot's stored config from `BotRegistry.get_bot()` and
hot-patch `settings.LLM_MODEL`, `LLM_CONTEXT_SIZE`, `LLM_TEMPERATURE`, `LLM_TOP_P`, and
`OLLAMA_URL`. Also updated the `PUT /api/active-bot` response to include `synced_model`.

## Verification

- 26/30 `test_bot_registry.py` tests pass
- 4 failures are pre-existing `TestBotHardDelete` issues (soft-delete not cleaning up)
- No regressions from this change
