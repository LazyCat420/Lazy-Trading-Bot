# Phase 4 — Autonomous Scheduler

> **Goal**: Run the entire Discovery → Watchlist → Analysis → Trading loop
> automatically on a schedule, without human intervention.
> Pre-market discovery, market-hours monitoring, end-of-day reporting.

---

## 4.1 — Daily Schedule

The bot runs on a **three-phase daily loop** aligned with US market hours (ET):

```
┌─────────────────────────────────────────────────────────────────┐
│  6:00 AM ET — PRE-MARKET PHASE                                  │
│                                                                  │
│  1. Run Reddit Scraper (trending overnight threads)              │
│  2. Run YouTube Scanner (new transcripts from last 12h)          │
│  3. Merge scores → Auto-add top tickers to watchlist             │
│  4. Run pipeline analysis for all NEW watchlist tickers           │
│  5. Generate pre-market briefing (summary of signals)             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  9:30 AM - 4:00 PM ET — MARKET HOURS PHASE                      │
│                                                                  │
│  Every 60 seconds:                                               │
│    • Price Trigger Monitor (stop-loss, take-profit, trailing)    │
│    • Update position P&L                                         │
│                                                                  │
│  Every 2 hours (10:30, 12:30, 2:30):                             │
│    • Re-run pipeline for BUY-signal tickers (update conviction)  │
│    • Check for new Reddit/YouTube mentions                       │
│    • Process any new FinalDecisions through Signal Router         │
│                                                                  │
│  After fills:                                                    │
│    • Update watchlist (mark position_held)                       │
│    • Persist order to DuckDB                                     │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  4:30 PM ET — END-OF-DAY PHASE                                   │
│                                                                  │
│  1. Final portfolio snapshot → DuckDB                            │
│  2. Run watchlist auto-remove (stale tickers)                    │
│  3. Generate EOD report:                                         │
│     • Today's trades (fills, P&L)                                │
│     • Portfolio summary                                          │
│     • Tomorrow's watchlist and key levels                        │
│  4. Apply score decay to all discovery scores                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4.2 — Scheduler Implementation

### Technology: APScheduler

Using `APScheduler` (Advanced Python Scheduler) with async support.
Integrates cleanly with FastAPI's event loop.

```python
# app/services/scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

class TradingScheduler:
    """Manages the automated trading schedule."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False

    def start(self):
        """Register all jobs and start the scheduler."""

        # Pre-market discovery (6:00 AM ET weekdays)
        self.scheduler.add_job(
            self._pre_market_run,
            CronTrigger(hour=6, minute=0, day_of_week="mon-fri",
                       timezone="America/New_York"),
            id="pre_market",
            name="Pre-Market Discovery & Analysis",
        )

        # Price monitoring (every 60s during market hours)
        self.scheduler.add_job(
            self._price_monitor_tick,
            IntervalTrigger(seconds=60),
            id="price_monitor",
            name="Price Trigger Monitor",
        )

        # Mid-day re-analysis (every 2 hours during market, 10:30/12:30/2:30)
        for hour in [10, 12, 14]:
            self.scheduler.add_job(
                self._midday_reanalysis,
                CronTrigger(hour=hour, minute=30, day_of_week="mon-fri",
                           timezone="America/New_York"),
                id=f"midday_{hour}",
                name=f"Mid-Day Reanalysis ({hour}:30)",
            )

        # End-of-day wrap-up (4:30 PM ET weekdays)
        self.scheduler.add_job(
            self._end_of_day_run,
            CronTrigger(hour=16, minute=30, day_of_week="mon-fri",
                       timezone="America/New_York"),
            id="end_of_day",
            name="End-of-Day Report & Cleanup",
        )

        self.scheduler.start()
        self.is_running = True

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown()
        self.is_running = False
```

### Job Implementations

```python
    async def _pre_market_run(self):
        """
        1. RedditCollector.collect() → scored tickers
        2. TickerScanner.scan_recent_transcripts() → scored tickers
        3. Merge and deduplicate scores
        4. WatchlistManager.process_discovery_results()
        5. For each new watchlist ticker:
           - PipelineService.run(ticker, mode="full")
           - SignalRouter.process_decision(decision)
        6. Generate briefing_report
        """

    async def _price_monitor_tick(self):
        """
        1. Skip if market is closed
        2. PriceMonitor.check_triggers()
        3. Update position P&L
        4. Take portfolio snapshot (every 15 min)
        """

    async def _midday_reanalysis(self):
        """
        1. Skip if market is closed
        2. Get watchlist tickers with last_signal == "BUY"
        3. Re-run pipeline in "quick" mode
        4. Process updated decisions
        5. Check for new Reddit/YouTube mentions
        """

    async def _end_of_day_run(self):
        """
        1. Take final portfolio snapshot
        2. WatchlistManager auto-remove stale tickers
        3. Apply score decay to all discovery scores
        4. Generate EOD report
        5. Persist report to DuckDB
        """
```

---

## 4.3 — Market Hours Detection

```python
import pytz
from datetime import datetime, time

def is_market_open() -> bool:
    """Check if US stock market is currently open."""
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)

    # Weekday check (Mon=0, Fri=4)
    if now.weekday() > 4:
        return False

    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = time(9, 30)
    market_close = time(16, 0)

    return market_open <= now.time() <= market_close

    # NOTE: Does not account for holidays.
    # Future enhancement: use `exchange_calendars` package for NYSE calendar
```

---

## 4.4 — Reports

### Pre-Market Briefing

```
╔══════════════════════════════════════════╗
║    PRE-MARKET BRIEFING — Feb 17, 2026   ║
╠══════════════════════════════════════════╣
║                                          ║
║  📡 DISCOVERY                            ║
║  Reddit: 12 tickers found (r/wsb,       ║
║          r/stocks, r/pennystocks)         ║
║  YouTube: 5 tickers from 3 channels      ║
║                                          ║
║  🆕 NEW WATCHLIST ADDITIONS              ║
║  SMCI (score: 15.2) — earnings catalyst  ║
║  RKLB (score: 12.8) — rocket launch      ║
║                                          ║
║  📊 ANALYSIS RESULTS                     ║
║  NVDA — BUY (0.85) — strong momentum     ║
║  SMCI — BUY (0.72) — oversold bounce     ║
║  PLTR — HOLD (0.65) — await breakout     ║
║  HOOD — SELL (0.40) — weakening trend    ║
║                                          ║
║  🎯 PENDING ORDERS                       ║
║  NVDA — Buy 5 shares @ $120.50           ║
║  SMCI — Buy 3 shares @ $45.00 (limit)    ║
╚══════════════════════════════════════════╝
```

### End-of-Day Report

```
╔══════════════════════════════════════════╗
║     END-OF-DAY REPORT — Feb 17, 2026    ║
╠══════════════════════════════════════════╣
║                                          ║
║  💰 PORTFOLIO                            ║
║  Value: $10,245.50 (+$245.50 / +2.46%)   ║
║  Cash: $7,100.00                         ║
║  Positions: 3 open                       ║
║                                          ║
║  📈 TODAY'S TRADES                       ║
║  BUY  NVDA ×5 @ $120.50 (filled)        ║
║  SELL HOOD ×10 @ $22.30 (stop-loss hit)  ║
║                                          ║
║  📊 P&L                                 ║
║  Realized: -$12.00 (HOOD stop-loss)      ║
║  Unrealized: +$24.00 (NVDA, PLTR)        ║
║                                          ║
║  🔄 WATCHLIST CHANGES                    ║
║  Removed: CRWV (stale, no mentions 5d)   ║
║  Cooldown: HOOD (7 day cooldown)         ║
╚══════════════════════════════════════════╝
```

---

## 4.5 — DuckDB Persistence

```sql
-- Scheduler run history
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id          VARCHAR PRIMARY KEY,
    job_name    VARCHAR NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status      VARCHAR DEFAULT 'running',  -- running/success/error
    summary     VARCHAR DEFAULT '',
    error       VARCHAR DEFAULT ''
);

-- Reports
CREATE TABLE IF NOT EXISTS reports (
    id          VARCHAR PRIMARY KEY,
    report_type VARCHAR NOT NULL,            -- 'pre_market' | 'end_of_day'
    date        DATE NOT NULL,
    content     VARCHAR NOT NULL,            -- JSON blob
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4.6 — API Endpoints

```
# Scheduler Control
GET  /api/scheduler/status           → Is scheduler running? Next job times?
POST /api/scheduler/start            → Start the scheduler
POST /api/scheduler/stop             → Stop the scheduler
POST /api/scheduler/run/{job_name}   → Manually trigger a job

# Reports
GET  /api/reports/latest             → Most recent pre-market & EOD reports
GET  /api/reports/history            → All reports with date filtering
```

---

## 4.7 — Frontend: Scheduler Panel

Add a **"Bot Control" panel** on the dashboard:

- **Start/Stop toggle** — big red/green button
- **Next scheduled job** — countdown timer
- **Job history** — table of recent runs with status
- **Pre-market briefing** — rendered report card
- **EOD report** — rendered report card
- **Manual triggers** — buttons to run individual jobs on demand

---

## 4.8 — Safety Guardrails

### Critical Safety Features

1. **Kill Switch**: One-click stop button that:
   - Stops the scheduler
   - Cancels all pending orders
   - Deactivates all price triggers
   - **Does NOT close existing positions** (user must decide)

2. **Daily Loss Limit**: If realized + unrealized loss exceeds X% of portfolio
   in a single day → auto-pause trading, notify user

3. **Max Orders Per Day**: Cap at 10 orders/day to prevent runaway trading

4. **Confirmation Mode**: Optional setting that requires manual approval
   for each trade above a dollar threshold

5. **Audit Log**: Every trading action is logged with:
   - The FinalDecision that triggered it
   - The exact price at time of execution
   - Which scheduler job initiated it

---

## Testing Plan

1. **Unit tests** for `is_market_open()` with mocked timezone
2. **Unit tests** for job scheduling (verify correct cron triggers)
3. **Integration test**: Full pre-market cycle with mocked collectors
4. **Integration test**: Price monitor tick → trigger firing
5. **Safety test**: Daily loss limit → auto-pause verification
6. **End-to-end test**: Discovery → Watchlist → Analysis → Trade → Report

## Dependencies

- Phases 1-3 must be complete
- `APScheduler>=3.10` — async scheduler
- `pytz` — timezone handling
- Optional: `exchange_calendars` — NYSE holiday calendar
