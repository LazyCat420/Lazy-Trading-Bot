# Phase 3 — Trading Engine

> **Goal**: Build the buy/sell execution engine that acts on pipeline decisions.
> Starts with paper trading (simulated), upgrades to live trading via broker API.
> Includes position tracking, price trigger monitoring, and portfolio management.

---

## 3.1 — Architecture Overview

```
FinalDecision (from Pipeline)
        │
        ▼
┌──────────────────────┐
│  Signal Router       │  BUY → Order Manager (open position)
│                      │  SELL → Order Manager (close position)
│                      │  HOLD → Price Monitor (set/update triggers)
└──────────┬───────────┘
           │
    ┌──────▼──────────────────────────────────────────┐
    │              ORDER MANAGER                       │
    │  ┌─────────────┐  ┌─────────────┐               │
    │  │ Paper Mode   │  │ Live Mode   │               │
    │  │ (Simulated)  │  │ (Broker API)│               │
    │  └─────────────┘  └─────────────┘               │
    │       Implements same OrderExecutor interface     │
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
    │  • Stop-loss triggers                            │
    │  • Take-profit triggers                          │
    │  • Trailing stop triggers                        │
    │  • Entry price triggers (buy limit orders)       │
    └─────────────────────────────────────────────────┘
```

---

## 3.2 — Order Execution

### OrderExecutor Interface

```python
class OrderExecutor(Protocol):
    """Common interface for paper and live trading."""

    async def buy(self, ticker: str, qty: int, price: float,
                  order_type: str = "market") -> Order: ...

    async def sell(self, ticker: str, qty: int, price: float,
                   order_type: str = "market") -> Order: ...

    async def get_account_balance(self) -> float: ...

    async def get_positions(self) -> list[Position]: ...
```

### Paper Trading (Phase 3a — Build First)

```python
class PaperTrader(OrderExecutor):
    """Simulated trading against live market prices."""

    def __init__(self, starting_balance: float = 10000.0):
        self.balance = starting_balance
        self.positions: dict[str, Position] = {}
        self.order_history: list[Order] = []

    async def buy(self, ticker, qty, price, order_type="market"):
        """
        1. Check we have enough balance
        2. Deduct cost from balance
        3. Add to positions
        4. Record in order_history
        5. Persist to DuckDB
        """

    async def sell(self, ticker, qty, price, order_type="market"):
        """
        1. Check we have the position
        2. Calculate P&L
        3. Add proceeds to balance
        4. Remove/reduce position
        5. Record in order_history
        6. Persist to DuckDB
        """
```

### Live Trading (Phase 3b — Future)

```python
class LiveTrader(OrderExecutor):
    """Real trading via broker API (Alpaca, IBKR, etc.)"""
    # Implementation deferred — paper trading validates the logic first
    # Will use Alpaca Trade API (most common for algo trading):
    #   pip install alpaca-trade-api
    #   Supports: market, limit, stop, stop-limit orders
    #   Paper trading mode built into Alpaca (free, no real money)
```

> **Broker recommendation**: Start with **Alpaca** — they have a free paper trading
> API that works identically to their live API. This lets us validate the
> `LiveTrader` implementation without risking real money.

---

## 3.3 — Position Tracking

### Data Models

```python
class Position(BaseModel):
    ticker: str
    qty: int
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    opened_at: datetime
    last_updated: datetime

    # From FinalDecision
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop_pct: float = 0.0   # e.g., 5.0 = sell if drops 5% from peak

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

    # Link to the decision that triggered this order
    decision_signal: str = ""          # BUY/SELL
    decision_confidence: float = 0.0

class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    cash_balance: float
    total_positions_value: float
    total_portfolio_value: float
    realized_pnl: float               # Cumulative
    unrealized_pnl: float             # Current
    positions: list[Position]
```

---

## 3.4 — Signal Router

The **Signal Router** converts `FinalDecision` output into actionable orders.

```python
class SignalRouter:
    """Routes pipeline decisions to order execution."""

    def __init__(self, executor: OrderExecutor, risk_params: dict):
        self.executor = executor
        self.risk_params = risk_params

    async def process_decision(self, decision: FinalDecision) -> Order | None:
        """
        BUY Decision:
            1. Check if we already hold this ticker
            2. Calculate position size from decision.suggested_position_size_pct
            3. Apply risk limits (max_risk_per_trade, max_position_size)
            4. Check portfolio allocation limit
            5. Calculate qty = (portfolio * size_pct) / price
            6. Execute buy order
            7. Set stop-loss and take-profit price triggers

        SELL Decision:
            1. Check if we hold this ticker
            2. If yes → execute sell (full exit)
            3. If no → do nothing (can't short in paper mode)

        HOLD Decision:
            1. Update price triggers if decision suggests new levels
            2. Log the hold reasoning
        """
```

### Position Sizing Calculation

```python
def calculate_position_size(
    decision: FinalDecision,
    portfolio_value: float,
    risk_params: dict,
) -> int:
    """
    Steps:
        1. Get suggested_position_size_pct from decision (LLM suggested)
        2. Cap at risk_params["max_position_size_pct"]
        3. Calculate dollar amount = portfolio_value × capped_pct
        4. Calculate qty = dollar_amount / current_price
        5. Verify total allocation doesn't exceed max_portfolio_allocation_pct
        6. Return integer qty (floor)
    """
```

---

## 3.5 — Price Trigger Monitor

### What it does

Polls live prices periodically and checks against active triggers.
When a trigger fires, it automatically executes the corresponding order.

### Trigger Types

| Trigger | Description | Action |
|---------|-------------|--------|
| **Stop-Loss** | Price drops below `stop_loss` | Auto-SELL entire position |
| **Take-Profit** | Price rises above `take_profit` | Auto-SELL entire position |
| **Trailing Stop** | Price drops X% from its high-water mark | Auto-SELL |
| **Entry Limit** | Price drops to `suggested_entry_price` | Auto-BUY |

```python
class PriceTrigger(BaseModel):
    id: str                             # UUID
    ticker: str
    trigger_type: Literal["stop_loss", "take_profit", "trailing_stop", "entry_limit"]
    trigger_price: float
    high_water_mark: float = 0.0       # For trailing stops
    trailing_pct: float = 0.0          # For trailing stops
    action: Literal["buy", "sell"]
    qty: int
    status: Literal["active", "triggered", "cancelled"]
    created_at: datetime

class PriceMonitor:
    """Polls prices and fires triggers."""

    POLL_INTERVAL_SECONDS = 60  # Check every minute during market hours

    async def check_triggers(self):
        """
        1. Get all active triggers from DuckDB
        2. Fetch current prices via /api/quotes (our existing batch endpoint)
        3. For each trigger:
           - Update trailing stop high-water marks
           - Check if trigger condition is met
           - If triggered → execute order via SignalRouter
           - Update trigger status
        """

    def is_market_open(self) -> bool:
        """Check if US stock market is currently open (9:30 AM - 4:00 PM ET)."""
```

---

## 3.6 — DuckDB Persistence

```sql
-- Track all positions
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

-- Full order history
CREATE TABLE IF NOT EXISTS orders (
    id              VARCHAR PRIMARY KEY,
    ticker          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,          -- 'buy' | 'sell'
    qty             INTEGER NOT NULL,
    price           DOUBLE NOT NULL,
    order_type      VARCHAR DEFAULT 'market',
    status          VARCHAR DEFAULT 'filled',
    decision_signal VARCHAR DEFAULT '',
    decision_confidence DOUBLE DEFAULT 0.0,
    filled_at       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Active price triggers
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

-- Portfolio snapshots for P&L tracking over time
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

## 3.7 — API Endpoints

```
# Portfolio
GET  /api/portfolio                  → Current portfolio snapshot
GET  /api/portfolio/history          → Portfolio value over time (for charts)
GET  /api/portfolio/pnl              → Realized + unrealized P&L breakdown

# Positions
GET  /api/positions                  → All open positions with live P&L
POST /api/positions/{ticker}/close   → Manually close a position

# Orders
GET  /api/orders                     → Order history
GET  /api/orders/{id}                → Single order details

# Triggers
GET  /api/triggers                   → Active price triggers
POST /api/triggers                    → Manually create a trigger
DELETE /api/triggers/{id}             → Cancel a trigger

# Trading Mode
GET  /api/trading/mode               → Current mode (paper/live)
PUT  /api/trading/mode               → Switch mode (requires confirmation)
```

---

## 3.8 — Frontend: Portfolio Dashboard

New dashboard sections:

### Portfolio Overview Card

- Total portfolio value (cash + positions)
- Today's P&L (dollar + percentage)
- Portfolio allocation pie chart

### Open Positions Table

| Ticker | Qty | Entry | Current | P&L | P&L % | Stop | Target | Action |
|--------|-----|-------|---------|-----|-------|------|--------|--------|
| NVDA | 5 | $120.50 | $125.30 | +$24.00 | +3.98% | $115.00 | $140.00 | Close |

### Order History

- Filterable table of all past orders
- Each order links to the FinalDecision that triggered it

### Active Triggers

- Visual display of stop-loss, take-profit, trailing stop levels
- Ability to modify or cancel

### Equity Curve Chart

- Line chart of portfolio value over time
- Benchmark comparison (S&P 500)

---

## Testing Plan

1. **Unit tests** for position sizing calculation
2. **Unit tests** for PaperTrader buy/sell/balance logic
3. **Unit tests** for each trigger type (stop-loss, take-profit, trailing)
4. **Integration test**: FinalDecision → SignalRouter → PaperTrader → DuckDB
5. **Integration test**: PriceMonitor trigger firing → auto sell
6. **Edge cases**: Insufficient balance, position not found, market closed
7. **Paper trading backtest**: Feed historical decisions → verify P&L calculation

## Dependencies

- Phase 1 (Discovery) + Phase 2 (Watchlist) must be complete
- Existing: `FinalDecision`, `/api/quotes`, DuckDB, `risk_params.json`
- Future: `alpaca-trade-api` (for live trading phase)
