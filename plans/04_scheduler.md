# Phase 4 — Autonomous Scheduler

> **Goal**: Run the entire Discovery → Watchlist → Analysis → Trading loop
> automatically on a real-time schedule, without human intervention.
> Pre-market discovery, market-hours monitoring, end-of-day reporting.

---

## What Already Exists

| Component | File | Status |
|-----------|------|--------|
| `AutonomousLoop.run_full_loop()` | `app/services/autonomous_loop.py` | ✅ Built |
| "Run Full Loop" button | `terminal_app.js` | ✅ Built |
| Loop status polling | `GET /api/bot/loop-status` | ✅ Built |
| Discovery service | `app/services/discovery_service.py` | ✅ Built |
| Auto-import to watchlist | `WatchlistManager.import_from_discovery()` | ✅ Built |
| Deep analysis batch | `DeepAnalysisService.analyze_batch()` | ✅ Built |
| Market hours check | Not yet built | ❌ Needed |
| Trading engine | Not yet built (Phase 3) | ❌ Needed |

> [!IMPORTANT]
> Phase 3 (Trading Engine) should be built first. The scheduler wraps the
> existing `AutonomousLoop` in a time-aware schedule — it's the "auto-pilot"
> on top of the "one-click" loop.

---

## 4.1 — Daily Schedule (US Market Hours, ET)

```
┌─────────────────────────────────────────────────────────────────────┐
│  6:00 AM ET — PRE-MARKET PHASE                                      │
│                                                                      │
│  Calls AutonomousLoop.run_full_loop() which runs:                    │
│    1. Discovery (Reddit + YouTube, 12h lookback)                     │
│    2. Auto-Import (top tickers → watchlist)                          │
│    3. Deep Analysis (4-layer funnel on all active tickers)           │
│    4. Trade Execution (process signals through PaperTrader)          │
│  Then generates a pre-market briefing report.                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  9:30 AM - 4:00 PM ET — MARKET HOURS PHASE                          │
│                                                                      │
│  Every 60 seconds:                                                   │
│    • PriceMonitor.check_triggers()  (stop-loss, take-profit, trail) │
│    • Update position P&L                                             │
│                                                                      │
│  Every 2 hours (10:30, 12:30, 2:30):                                 │
│    • Re-run Deep Analysis for tickers flagged as BUY                 │
│    • Check for new Reddit/YouTube mentions                           │
│    • Process updated dossiers through SignalRouter                   │
│                                                                      │
│  After each fill:                                                    │
│    • Update watchlist (mark position_held)                           │
│    • Persist order to DuckDB                                         │
│    • Take portfolio snapshot                                         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  4:30 PM ET — END-OF-DAY PHASE                                       │
│                                                                      │
│  1. Final portfolio snapshot → DuckDB                                │
│  2. Run watchlist auto-remove (stale tickers with no mentions >5d)   │
│  3. Apply score decay to all discovery scores (×0.8 daily)           │
│  4. Generate EOD report:                                             │
│     • Today's trades (fills, P&L per trade)                          │
│     • Portfolio summary (value, realized, unrealized)                │
│     • Tomorrow's watchlist and key price levels                      │
│  5. Persist report to DuckDB                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4.2 — New Files to Create

### `app/services/scheduler.py` — APScheduler Integration

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

class TradingScheduler:
    """Manages the full daily automation schedule."""

    def __init__(self, loop: AutonomousLoop, price_monitor: PriceMonitor):
        self.scheduler = AsyncIOScheduler()
        self.loop = loop
        self.monitor = price_monitor
        self.is_running = False

    def start(self):
        # Pre-market: 6:00 AM ET weekdays
        self.scheduler.add_job(
            self._pre_market_run,
            CronTrigger(hour=6, minute=0, day_of_week="mon-fri",
                        timezone="America/New_York"),
            id="pre_market",
        )

        # Price monitoring: every 60s (PriceMonitor skips if market closed)
        self.scheduler.add_job(
            self.monitor.check_triggers,
            IntervalTrigger(seconds=60),
            id="price_monitor",
        )

        # Mid-day re-analysis: 10:30, 12:30, 2:30 ET
        for hour in [10, 12, 14]:
            self.scheduler.add_job(
                self._midday_reanalysis,
                CronTrigger(hour=hour, minute=30, day_of_week="mon-fri",
                            timezone="America/New_York"),
                id=f"midday_{hour}",
            )

        # End-of-day: 4:30 PM ET weekdays
        self.scheduler.add_job(
            self._end_of_day_run,
            CronTrigger(hour=16, minute=30, day_of_week="mon-fri",
                        timezone="America/New_York"),
            id="end_of_day",
        )

        self.scheduler.start()
        self.is_running = True

    def stop(self):
        self.scheduler.shutdown()
        self.is_running = False
```

### `app/utils/market_hours.py` — Market Time Helpers

```python
import pytz
from datetime import datetime, time

ET = pytz.timezone("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() > 4:          # Weekend
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE

def next_market_open() -> datetime:
    """Return the next market open datetime (for countdown display)."""
    ...

# Future: use `exchange_calendars` package for NYSE holidays
```

### `app/services/report_generator.py` — Pre-Market & EOD Reports

Generates structured JSON reports stored in `reports` DuckDB table.
Consumed by frontend for display.

---

## 4.3 — DuckDB Tables

Add to `app/database.py`:

```sql
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id          VARCHAR PRIMARY KEY,
    job_name    VARCHAR NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status      VARCHAR DEFAULT 'running',   -- running/success/error
    summary     VARCHAR DEFAULT '',
    error       VARCHAR DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id          VARCHAR PRIMARY KEY,
    report_type VARCHAR NOT NULL,             -- 'pre_market' | 'end_of_day'
    report_date DATE NOT NULL,
    content     VARCHAR NOT NULL,             -- JSON blob
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4.4 — API Endpoints

Add to `main.py`:

```
GET  /api/scheduler/status           → Running? Next job times? Job history
POST /api/scheduler/start            → Start the automated schedule
POST /api/scheduler/stop             → Stop (kill switch)
POST /api/scheduler/run/{job}        → Manually trigger pre_market, midday, or eod
GET  /api/reports/latest             → Most recent pre-market & EOD reports
GET  /api/reports/history            → All reports with date filtering
```

---

## 4.5 — Frontend: Scheduler Panel

Add a **"Bot Control"** section to Autobot Monitor or as a new sidebar page:

| Element | Description |
|---------|-------------|
| **Start / Kill Switch** | Big green/red toggle — starts or stops the scheduler |
| **Next Job Countdown** | Shows "Pre-Market in 3h 22m" or "Price Check in 45s" |
| **Job History Table** | Recent runs with name, start, end, status, error |
| **Pre-Market Briefing** | Rendered report card (signals, new tickers, orders) |
| **EOD Report** | Rendered report card (P&L, trades, portfolio summary) |
| **Manual Triggers** | Buttons: "Run Pre-Market Now", "Run EOD Now" |

---

## 4.6 — Safety Guardrails

| Guard | Trigger | Action |
|-------|---------|--------|
| **Kill Switch** | User clicks stop | Stops scheduler, cancels pending orders, deactivates triggers. Does **not** close positions. |
| **Daily Loss Limit** | Realized + unrealized loss > 5% of portfolio in a day | Auto-pause trading, log warning |
| **Max Orders/Day** | More than 10 orders in a calendar day | Reject new orders until next day |
| **Confirmation Mode** | Trade exceeds $1,000 (configurable) | Queue for manual approval instead of auto-executing |
| **Audit Trail** | Every action | Full log: dossier → signal → order → fill, with timestamps |

---

## Testing Plan

1. **Unit**: `is_market_open()` with mocked timezone (weekday/weekend/holiday)
2. **Unit**: Job scheduling (verify correct cron triggers fire)
3. **Integration**: Full pre-market cycle with mocked collectors → verify report generated
4. **Integration**: Price monitor tick → trigger firing → auto-sell
5. **Safety**: Daily loss limit → auto-pause verification
6. **End-to-end**: Run full automated day: pre-market → monitor → EOD report

## Dependencies (New Packages)

| Package | Purpose |
|---------|---------|
| `APScheduler>=3.10` | Async cron scheduler |
| `pytz` | Timezone handling |
| `exchange_calendars` (optional) | NYSE holiday calendar |
