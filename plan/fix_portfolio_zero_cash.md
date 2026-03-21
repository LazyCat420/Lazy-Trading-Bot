# Fix: Portfolio Shows $0 Cash / $0 Equity

## Problem
Trading agent always sees Cash=$0, Equity=$0, Positions=0 — no trades ever execute.

## Root Causes

### 1. Property Name Mismatch (Critical)
`portfolioService.getSummary()` returns `{cash_balance, total_portfolio_value, positions_count}`
but `tradingAgent.js` reads `{cash, equity, positions}` → all undefined → $0.

**Cascade:** `portfolio.cash < 100` = `undefined < 100 = false`, so the insufficient-cash guardrail never triggers. `portfolio.equity * 0.05` = `NaN`, so `qty = Math.floor(NaN) = NaN`, so no order ever executes.

### 2. No Per-Bot Portfolio Isolation
All bots share the same global `config.risk_params.starting_balance`. Changing LLM settings still reuses the same bot_id/portfolio — no A/B testing possible.

### 3. Bot Identity Too Broad
`registerBotIfNotExists` matched only by `model_name`, not settings. Same model with different temperature/context would share a portfolio.

## Fix Applied
- `portfolioService.getSummary()` → returns canonical `{cash, equity, positions}` + verbose names for backward compat
- Per-bot `starting_balance` stored on bot document, falls back to global config
- `botRegistry` uses MD5 fingerprint of `model_name|temperature|context_length|top_p|max_tokens`
- `autonomousLoop.js` passes full LLM config to registration

## Files Changed
- `tradingbackend/src/services/portfolioService.js`
- `tradingbackend/src/services/botRegistry.js`
- `tradingbackend/src/services/autonomousLoop.js`

## Status: DONE
