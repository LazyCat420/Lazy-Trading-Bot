# Plan: Build Multi-Layered Brain Architecture (V3)

## Problem
The old system dumped ALL data into EVERY analyst, causing bloated context windows, timeouts, and zero correlation between signals.

## Solution (Hybrid Approach 1+3)
1. **SignalRanker** (pure Python) scans data for anomalies → produces ranked seeds
2. **InvestigationAgent** (ReAct loop) investigates each seed with 2-3 tools per iteration
3. Investigation memos → existing ThesisConstructor → DecisionAgent

## Files Created
- `app/services/signal_ranker.py`
- `app/services/investigation_agent.py`
- `app/services/investigation_prompts.py`

## Files Modified
- `app/services/trading_agent.py` — V2 → V3 wiring
- `app/services/brain_loop.py` — isinstance safety checks

## Status: DONE ✅
Verified with full pipeline audit — 3 seeds investigated correctly with proper tool calls.
