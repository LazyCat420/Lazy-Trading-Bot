# Fix #1: Cookie-Cutter Analysis — Enriched Trading Rationales

## Problem

Every BUY decision from `granite3.2:8b-50k` produces nearly identical rationales:
> *"Quant signals indicate strong conviction (75%) with positive momentum…"*

The bot has access to rich data (technicals, dossier, RAG, YouTube) but ignores most of it. The LLM lazy-defaults to parroting the quant conviction number.

## Root Cause

Two problems in [trading_agent.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py):

1. **System prompt (L191):** `"rationale" → MUST be a string, 1-3 sentences referencing data` — too vague, doesn't require differentiation or specific data points
2. **User prompt:** The dossier data is dumped as free text (chart analysis, fundamentals, risk) but there's no structured prompt asking the LLM to *compare and contrast* signals

## Proposed Changes

### [MODIFY] [trading_agent.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/trading_agent.py)

#### Change 1: Restructure rationale field in system prompt (L158-226)

Replace the generic `"rationale" → MUST be a string, 1-3 sentences` with a **structured rationale format** that forces the LLM to cite specific numbers:

```diff
- "rationale" → MUST be a string, 1-3 sentences referencing data from the context
+ "rationale" → MUST be a structured string with THREE parts separated by pipes:
+   1. THESIS: One sentence stating your core reason (cite a specific number)
+   2. KEY_DATA: The 2-3 most important data points that drive this decision
+   3. DIFFERENTIATOR: What makes THIS ticker different from a generic BUY/HOLD
+   Example: "THESIS: GEV's 85% conviction with +320% 12m momentum is exceptional | KEY_DATA: Sharpe 2.4, RSI 62, MACD bullish cross | DIFFERENTIATOR: Only position with >2.0 Sharpe in current portfolio"
```

#### Change 2: Add anti-boilerplate instruction (in system prompt)

```diff
+ RATIONALE QUALITY RULES:
+ - Do NOT start rationale with "Quant signals indicate" or "Quant conviction is"
+ - Your rationale must mention at least ONE specific number from technical analysis (RSI, MACD, SMA, etc.)
+ - Your rationale must explain WHY the confidence is the exact number you chose (not just "high" or "strong")  
+ - Each ticker's rationale must be UNIQUE — copying the same wording across tickers is prohibited
```

#### Change 3: Add ticker-comparison section to user prompt `_build_prompt()` (L542-618)

Currently the prompt only shows one ticker at a time. We can't show all tickers (context budget), but we can add a **comparison anchor**:

```diff
+ # After quant verdict section (~L600)
+ # Add position diversity context
+ if positions held:
+   parts.append(f"\nCURRENT HOLDINGS: {list of held tickers with conviction}")
+   parts.append("Your rationale must explain why this ticker adds VALUE beyond what you already hold.")
```

## Verification Plan

### Automated Tests
- Run existing smoke tests to confirm no regressions
- Validate that the new prompt format produces parseable rationales

### Manual Verification
- Run 1 pipeline cycle with the updated prompt
- Compare rationale diversity across BUY decisions  
- Check that rationales cite specific RSI/MACD/Sharpe numbers instead of generic conviction
