# Phase 3 — Trading Engine

> **Goal**: Execute buy/sell decisions produced by the Deep Analysis pipeline.
> Starts with paper trading (simulated), upgrades to live trading via Alpaca API.
> Includes position tracking, price trigger monitoring, and portfolio management.

---

## What Already Exists

| Component | File | Status |
|-----------|------|--------|
| `FinalDecision` model | `app/models/decision.py` | ✅ Built |
| Deep Analysis → `TickerDossier` | `app/services/deep_analysis_service.py` | ✅ Built |
| Autonomous Loop orchestrator | `app/services/autonomous_loop.py` | ✅ Built |
| `AutonomousLoop.run_full_loop()` | Calls Discovery → Import → Deep Analysis | ✅ Built |
| Live price quotes | `GET /api/quotes` (batch yfinance) | ✅ Built |
| Risk params config | `app/user_config/risk_params.json` | ✅ Built |
| Frontend Autobot Monitor | `app/static/terminal_app.js` | ✅ Built |

---

## 3.1 — Architecture

The trading engine plugs into the **existing autonomous loop** as Step 4:

```
┌─────────────────────────────────────────────────────────────┐
│  AutonomousLoop.run_full_loop()                              │
│                                                              │
│  Step 1: Discovery         ← already built                  │
│  Step 2: Auto-Import       ← already built                  │
│  Step 3: Deep Analysis     ← already built                  │
│  Step 4: Trade Execution   ← THIS PHASE                     │
│     │                                                        │
│     ├─ Read each ticker's TickerDossier                      │
│     ├─ SignalRouter converts conviction → Order              │
│     ├─ PaperTrader/LiveTrader executes order                 │
│     └─ PriceMonitor sets stop-loss / take-profit triggers    │
└─────────────────────────────────────────────────────────────┘
```

### Signal Flow

```
TickerDossier (conviction_score, bull/bear case)
        │
        ▼
┌──────────────────────────┐
│  SignalRouter             │  conviction ≥ 0.7 → BUY order
│  (app/engine/             │  conviction ≤ 0.3 → SELL order
│   signal_router.py)       │  0.3 < conv < 0.7 → HOLD (update triggers)
└──────────┬───────────────┘
           │
    ┌──────▼──────────────────────────────────────────┐
    │              ORDER MANAGER                       │
    │  ┌─────────────┐  ┌─────────────┐               │
    │  │ PaperTrader  │  │ LiveTrader  │               │
    │  │ (Phase 3a)   │  │ (Phase 3b)  │               │
    │  └─────────────┘  └─────────────┘               │
    │       Both implement OrderExecutor protocol      │
    └──────────────┬──────────────────────────────────┘
                   │
    ┌──────────────▼──────────────────────────────────┐
    │            POSITION TRACKER                      │
    │  • Open positions (ticker, qty, entry price)     │
    │  • P&L calculation (unrealized + realized)       │
    │  • Portfolio allocation tracking                 │
    └──────────────┬──────────────────────────────────┘
                   │
    ┌──────────────▼──────────────────────────────────┐
    │         PRICE TRIGGER MONITOR                    │
    │  • Stop-loss / take-profit triggers              │
    │  • Trailing stop triggers                        │
    │  • Checks every 60s via existing /api/quotes     │
    └─────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> The SignalRouter reads **TickerDossier** (from deep analysis), NOT the old `FinalDecision`.
> This is a key change from the original plan — the dossier's `conviction_score` + `bull_case`/`bear_case` are richer signals.

---

## 3.2 — New Files to Create

### `app/models/trading.py` — Pydantic models

```python
class Position(BaseModel):
    ticker: str
    qty: int
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    stop_loss: float = 0.0           # auto-set from dossier
    take_profit: float = 0.0
    trailing_stop_pct: float = 0.0   # e.g. 5.0 = sell if drops 5% from peak
    opened_at: datetime
    last_updated: datetime

class Order(BaseModel):
    id: str                            # UUID
    ticker: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    status: Literal["pending", "filled", "cancelled", "failed"]
    filled_at: datetime | None = None
    created_at: datetime
    # Link to the dossier that triggered this order
    conviction_score: float = 0.0
    signal: str = ""                   # BUY/SELL/HOLD

class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    cash_balance: float
    total_positions_value: float
    total_portfolio_value: float
    realized_pnl: float               # cumulative
    unrealized_pnl: float             # current

class PriceTrigger(BaseModel):
    id: str
    ticker: str
    trigger_type: Literal["stop_loss", "take_profit", "trailing_stop"]
    trigger_price: float
    high_water_mark: float = 0.0
    trailing_pct: float = 0.0
    action: Literal["sell"]            # triggers always sell for safety
    qty: int
    status: Literal["active", "triggered", "cancelled"]
    created_at: datetime
```

### `app/engine/signal_router.py` — Converts dossiers to orders

Key logic:

1. Read `conviction_score` from latest dossier
2. Read `risk_params.json` for position sizing limits
3. Calculate position size: `portfolio_value × suggested_pct / current_price`
4. Cap at `max_position_size_pct` and `max_portfolio_allocation_pct`
5. If BUY: execute order, set stop-loss (entry × (1 - stop_loss_pct)), set take-profit
6. If SELL: close entire position if currently held
7. If HOLD: update/maintain existing triggers

### `app/services/paper_trader.py` — Simulated execution

```python
class PaperTrader:
    def __init__(self, starting_balance: float = 10_000.0): ...
    async def buy(self, ticker, qty, price) -> Order: ...
    async def sell(self, ticker, qty, price) -> Order: ...
    def get_portfolio(self) -> PortfolioSnapshot: ...
    def get_positions(self) -> list[Position]: ...
```

All state persisted to DuckDB — survives server restarts.

### `app/services/price_monitor.py` — Trigger checker

Polls `GET /api/quotes` every 60s during market hours.
Checks all active triggers, fires auto-sells when conditions met.

---

## 3.3 — DuckDB Tables

Add to `app/database.py`:

```sql
CREATE TABLE IF NOT EXISTS positions (
    ticker          VARCHAR PRIMARY KEY,
    qty             INTEGER NOT NULL,
    avg_entry_price DOUBLE NOT NULL,
    stop_loss       DOUBLE DEFAULT 0.0,
    take_profit     DOUBLE DEFAULT 0.0,
    trailing_stop_pct DOUBLE DEFAULT 0.0,
    opened_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id              VARCHAR PRIMARY KEY,
    ticker          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,
    qty             INTEGER NOT NULL,
    price           DOUBLE NOT NULL,
    order_type      VARCHAR DEFAULT 'market',
    status          VARCHAR DEFAULT 'filled',
    conviction_score DOUBLE DEFAULT 0.0,
    signal          VARCHAR DEFAULT '',
    filled_at       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_triggers (
    id              VARCHAR PRIMARY KEY,
    ticker          VARCHAR NOT NULL,
    trigger_type    VARCHAR NOT NULL,
    trigger_price   DOUBLE NOT NULL,
    high_water_mark DOUBLE DEFAULT 0.0,
    trailing_pct    DOUBLE DEFAULT 0.0,
    action          VARCHAR NOT NULL,
    qty             INTEGER NOT NULL,
    status          VARCHAR DEFAULT 'active',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    timestamp            TIMESTAMP PRIMARY KEY,
    cash_balance         DOUBLE NOT NULL,
    total_positions_value DOUBLE NOT NULL,
    total_portfolio_value DOUBLE NOT NULL,
    realized_pnl         DOUBLE DEFAULT 0.0,
    unrealized_pnl       DOUBLE DEFAULT 0.0
);
```

---

## 3.4 — Integration with Autonomous Loop

Modify `app/services/autonomous_loop.py`:

```python
# Step 4 — currently a placeholder, becomes:
async def _do_trading(self) -> dict:
    """Process dossiers through SignalRouter → PaperTrader."""
    tickers = self.watchlist.get_active_tickers()
    orders_placed = []
    for ticker in tickers:
        dossier = DeepAnalysisService.get_latest_dossier(ticker)
        if not dossier:
            continue
        order = await self.signal_router.process_dossier(ticker, dossier)
        if order:
            orders_placed.append(order)
    return {"orders": len(orders_placed), "details": orders_placed}
```

---

## 3.5 — API Endpoints

Add to `main.py`:

```
GET  /api/portfolio              → Current cash + positions + total value
GET  /api/portfolio/history      → Snapshots over time (for equity curve chart)
GET  /api/positions              → All open positions with live P&L
POST /api/positions/{ticker}/close → Manual close
GET  /api/orders                 → Order history
GET  /api/triggers               → Active price triggers
PUT  /api/trading/mode           → Switch paper/live (requires confirmation)
```

---

## 3.6 — Frontend: Portfolio Tab

Add a **"Portfolio"** tab to the Autobot Monitor page (alongside Scoreboard, Watchlist, Activity Log):

| Section | Content |
|---------|---------|
| **Overview Card** | Total value, today's P&L ($, %), cash balance |
| **Positions Table** | Ticker, Qty, Entry, Current, P&L, Stop, Target, Close button |
| **Order History** | Sortable table with side, price, conviction, timestamp |
| **Equity Curve** | Line chart of portfolio value over time |

---

## 3.7 — Safety Guardrails

| Guard | Description | Default |
|-------|-------------|---------|
| **Max position size** | Single position can't exceed X% of portfolio | 10% |
| **Max portfolio allocation** | Total invested can't exceed X% of portfolio | 60% |
| **Max orders/day** | Hard cap on daily order count | 10 |
| **Daily loss limit** | Pause trading if losses exceed X% | 5% |
| **Min conviction** | Only trade if conviction ≥ threshold | 0.70 |
| **Cooldown** | Don't re-buy a ticker within X days of selling | 7 days |

All configurable via `risk_params.json`.

---

## Testing Plan

1. **Unit**: Position sizing with various portfolio sizes and risk params
2. **Unit**: PaperTrader buy/sell/balance math
3. **Unit**: Each trigger type (stop-loss, take-profit, trailing stop)
4. **Integration**: Dossier → SignalRouter → PaperTrader → DuckDB
5. **Integration**: PriceMonitor triggers → auto-sell
6. **Edge cases**: Insufficient balance, position not found, duplicate buy
7. **End-to-end**: Run full autonomous loop with paper trading enabled

## Dependencies

- Existing: `TickerDossier`, `/api/quotes`, DuckDB, `risk_params.json`
- New: None for paper trading
- Future: `alpaca-py` for live trading (Phase 3b)
