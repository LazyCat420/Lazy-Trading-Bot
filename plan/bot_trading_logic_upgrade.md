# Bot Trading Logic Upgrade Plan

## 1. Overview
The rigid enforcement of `max_orders_per_cycle` will be removed or strictly relegated to an extreme safety fallback, rather than a primary business-logic throttler. The bot will instead be provided with comprehensive context regarding its available capital, existing portfolio risk, and sector concentration. The goal is for the bot to make "human-like" decisions on capital allocation, understanding that concentrating risk is its own choice, not a hard system boundary.

## 2. Capital Context & Risk Awareness
- **Inject Account Context:** Expose total account size, current cash available, and current portfolio value directly to the LLM agent during the strategic decision-making phase (`trading_pipeline_service.py` / `portfolio_strategist.py`).
- **Inject Sector/Position Risk Context:** Provide the LLM with the % weight of each stock and sector in the current portfolio.
- **Instruction Update:** Modify the LLM prompt to inform the bot that it controls its own risk—if it chooses to allocate 80% to one sector, it must justify the high risk, but it is permitted to do so.

## 3. Advanced 'Pass' and Trigger Price Logic
- **"Pass" Strategy with Triggers:** If the bot analyzes a stock and decides not to buy it at the current price, it should output "pass". However, it MUST also provide a `trigger_price` (a lower entry point where it *would* be interested in buying).
- **Graceful Passing:** If no reasonable `trigger_price` exists (e.g., the stock is fundamentally broken), it can pass without setting a trigger.
- **System Integration:** Any `trigger_price` returned during a "pass" should be routed to the `PriceMonitor` or trigger database to automatically alert or execute if the stock dips to that level.

## 4. Autonomous Selling & Capital Freeing
- **Holding Awareness:** The bot should only consider a "sell" action if it currently holds the stock in its portfolio.
- **Proactive Exits:** The bot should be instructed that it can sell for multiple strategic reasons besides just hitting technical stop-losses/take-profits. This includes:
  - Freeing up capital for better identified opportunities.
  - Hedging against newly identified macroeconomic or sector risks.
  - Taking profits proactively based on fundamental shifts.
- **System Integration:** Ensure the execution service processes LLM-initiated "sell" signals correctly and calculates the freed capital for the next cycle.

## 5. Continuous Mutation Testing
- Apply the 5-step Continuous Mutation Testing Loop to `trading_pipeline_service.py` and the LLM prompts.
- Ensure tests verify that the bot receives the correct capital/risk context and that "pass" decisions correctly spawn price triggers in the database.
- Use `mutmut` or equivalent to ensure the conditional logic (e.g., "only sell if holding") is strictly enforced and cannot be mutated without failing a test.
