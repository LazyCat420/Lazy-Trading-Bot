# Phase 3 — Single Trading Pipeline

## Goal

Replace the multi-step "deep analysis → portfolio strategist loop" with a **single, deterministic pipeline** that ends in a trade (or a logged HOLD), using **one LLM call per ticker**.

---

## Current State (What Exists)

| Component | Status | Notes |
|---|---|---|
| `symbol_filter.py` + `FilterPipeline` | ✅ Exists | Composable filters, `run_batch()`, `rejected_symbols` table |
| `deep_analysis_service.py` | ✅ Exists | Quant + Distiller, zero LLM calls — produces `TickerDossier` |
| `portfolio_strategist.py` | ✅ Exists | 1023-line tool-calling LLM agent. Multi-turn loop with `get_dossier`, `place_buy`, `place_sell`. **This is Phase 3's predecessor — needs replacing.** |
| `autonomous_loop.py` | ✅ Exists | Orchestrates Discovery → Collection → Analysis → Trading. Calls `PortfolioStrategist` in `_do_trading()`. |
| `paper_trader.py` | ✅ Exists | Paper trading engine with positions, orders, triggers. |
| `TradingAgent` (single-call) | ❌ Missing | Needs to be created |
| `TradingPipelineService` | ❌ Missing | Needs to be created |
| `ExecutionService` | ❌ Missing | Needs to be created |
| `risk_rules.py` | ❌ Missing | Needs to be created |
| `dry_run` support | ❌ Missing | No dry-run flag anywhere |
| `USE_DEEP_ANALYSIS` feature flag | ❌ Missing | No feature flag for old vs new path |

---

## PR-by-PR Implementation

### PR 3.1: `TradingAgent` — Single-Call Decision Maker

**File:** `app/services/trading_agent.py` [NEW]

Replaces the multi-turn `PortfolioStrategist` loop with a single LLM call per ticker.

```python
class TradingAgent:
    """One LLM call per ticker → TradeAction JSON."""
    
    async def decide(self, context: dict) -> TradeAction:
        """
        context contains:
          - ticker, last_price, today_change_pct, volume, avg_volume
          - technical_summary (from DataDistiller — precomputed, no charts)
          - quant_scorecard summary (trend score, RS rating, Sharpe, etc.)
          - news_summary (2-3 sentence digest)
          - portfolio: available_cash, max_position_pct, existing_position
          - house_rules: excluded symbols, penny stock threshold, liquidity floor
        
        Returns: TradeAction (BUY/SELL/HOLD + confidence + rationale)
        """
```

**Key design:**

- Build one compact prompt with all context inline (no tool-calling needed)
- Call `LLMService.chat(..., response_format="json")` with `TradeAction` schema
- LLM's job is NARROW: BUY/SELL/HOLD + short rationale + risk level
- All indicators are precomputed locally — LLM only interprets

**Dependencies:** `TradeAction` schema from Phase 4 PR 4.1 (build that first)

---

### PR 3.2: `ExecutionService` + Safety Gating

**File:** `app/services/execution_service.py` [NEW]

Accepts a validated `TradeAction` and enforces deterministic safety rules before executing.

```python
class ExecutionService:
    """Deterministic execution with safety gates."""
    
    async def execute(self, action: TradeAction, dry_run: bool = True) -> dict:
        """
        Gates (all checked before execution):
          1. Market hours check (via market_hours.py)
          2. Max notional per trade (configurable, e.g. $5000)
          3. Max daily trades (configurable, e.g. 10)
          4. No duplicate orders (same symbol + side within 5 min)
          5. Position concentration limit (max % of portfolio in one stock)
          6. dry_run mode: log everything but don't touch PaperTrader
        
        If all gates pass → paper_trader.place_order()
        """
```

**Dependencies:** `paper_trader.py` (existing), `market_hours.py` (existing)

---

### PR 3.3: `risk_rules.py` — Deterministic Sizing

**File:** `app/services/risk_rules.py` [NEW]

Compute quantity, stop-loss, take-profit using deterministic rules so execution is consistent and testable.

```python
class RiskRules:
    """Deterministic position sizing and risk management."""
    
    def compute_qty(self, price: float, cash: float, risk_level: str,
                    portfolio_value: float) -> int:
        """ATR/volatility-based position sizing."""
    
    def compute_stop_loss(self, price: float, atr: float, risk_level: str) -> float:
        """2x ATR below entry by default."""
    
    def compute_take_profit(self, price: float, atr: float, risk_level: str) -> float:
        """3x ATR above entry by default (1.5:1 R:R minimum)."""
```

---

### PR 3.4: `TradingPipelineService` — The Orchestrator

**File:** `app/services/trading_pipeline_service.py` [NEW]

One entrypoint: `run_once(bot_id, dry_run=True)`.

```python
class TradingPipelineService:
    """Single deterministic trading pipeline."""
    
    async def run_once(self, bot_id: str = "default", dry_run: bool = True) -> dict:
        """
        1. Load candidate symbols from watchlist
        2. Run symbol_filter.get_filter_pipeline().run_batch(symbols)
        3. For each passed symbol:
           a. Fetch context: price snapshot, technicals, scorecard, positions, cash
           b. Call TradingAgent.decide(context) → TradeAction
           c. Validate TradeAction (Phase 4 parser)
           d. If BUY/SELL: send to ExecutionService
           e. If HOLD: log to trade_decisions table
        4. Return summary with all decisions and outcomes
        """
```

---

### PR 3.5: Wire Into App + Feature Flag

**Files Modified:**

- `app/config.py` — Add `USE_NEW_PIPELINE: bool = True` setting
- `app/services/autonomous_loop.py` — Update `_do_trading()` to use `TradingPipelineService` when flag is on, keep `PortfolioStrategist` when flag is off
- `app/main.py` — Update "Run cycle" endpoint to use new pipeline

**Transitional approach:**

- Old `PortfolioStrategist` path stays available behind `USE_NEW_PIPELINE=false`
- "Generate dossier" remains a separate optional feature (not required for trades)
- Default: new pipeline is ON

---

## Files Summary

| File | Action |
|---|---|
| `app/services/trading_agent.py` | **NEW** |
| `app/services/execution_service.py` | **NEW** |
| `app/services/risk_rules.py` | **NEW** |
| `app/services/trading_pipeline_service.py` | **NEW** |
| `app/config.py` | **MODIFY** (add feature flag) |
| `app/services/autonomous_loop.py` | **MODIFY** (swap `_do_trading()`) |
| `app/main.py` | **MODIFY** (wire new pipeline endpoint) |

## Verification

- Unit test: `TradingAgent.decide()` returns valid `TradeAction` JSON for sample contexts
- Unit test: `ExecutionService` enforces all safety gates (market hours, max notional, duplicates)
- Unit test: `RiskRules` computes consistent qty/stop/TP for given inputs
- Integration test: `TradingPipelineService.run_once(dry_run=True)` produces `trade_decisions` rows
- Manual test: Run a full cycle with `USE_NEW_PIPELINE=true` and verify terminal logs show one LLM call per ticker → valid decisions
