# Dashboard Model Display Plan

## Goal
Show the active model name in the dashboard header and loop progress panel so the user can easily see which LLM model is running.

## Changes

### [MODIFY] terminal_app.js

1. **Header badge** (line ~4508-4510): When the loop is running, append model name to the `LOOP RUNNING` badge (e.g. `LOOP RUNNING · Qwen3-8B`).

2. **Loop progress panel** (line ~4543): Change `"Autonomous Loop Running"` to include the model name (e.g. `"Autonomous Loop Running — Qwen3-8B"`). Both `activeBotModelName` and `activeBotId` are already available in the component.

## Verification
- User manually refreshes the dashboard and confirms the model name is visible in the header and loop panel.
