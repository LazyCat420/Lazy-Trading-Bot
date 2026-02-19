# Activity Log Upgrade — Full Pipeline Visibility

> **Goal**: Transform the Activity Log from a discovery-only feed into a
> comprehensive audit trail that confirms every piece of data collected
> and every action taken across all pipeline phases.

---

## Current State — What's Broken

### What the Activity Log Shows Now

The Activity Log tab queries **one table**: `discovered_tickers`.

```
Frontend (terminal_app.js)
  └─ useMonitorData() → fetchAll()
       └─ GET /api/discovery/history?limit=200
            └─ DiscoveryService.get_discovery_history()
                 └─ SELECT * FROM discovered_tickers ORDER BY discovered_at DESC
```

Each row shows: **ticker, source (Reddit/YouTube), score, sentiment, snippet, timestamp**.

That's it — raw discovery mentions. Only Phase 1 output.

### What's Invisible (the 90% we're missing)

| Pipeline Phase | Data Collected | Currently Logged? |
|----------------|---------------|-------------------|
| **Discovery** | Reddit mentions, YouTube mentions | ✅ Yes (this is ALL we see) |
| **Discovery** | YouTube transcript downloads | ❌ No |
| **Data Collection** | Price history (1yr OHLCV) | ❌ No |
| **Data Collection** | Fundamentals (24 metrics) | ❌ No |
| **Data Collection** | Financial history (multi-year) | ❌ No |
| **Data Collection** | Balance sheet (multi-year) | ❌ No |
| **Data Collection** | Cash flows (multi-year) | ❌ No |
| **Data Collection** | Analyst data (targets + recs) | ❌ No |
| **Data Collection** | Insider activity (transactions) | ❌ No |
| **Data Collection** | Earnings calendar | ❌ No |
| **Data Collection** | Technical indicators (7 groups) | ❌ No |
| **Data Collection** | Risk metrics (25+ quant) | ❌ No |
| **Data Collection** | News articles (yFinance) | ❌ No |
| **Deep Analysis** | Layer 1: Quant Scorecard | ❌ No |
| **Deep Analysis** | Layer 2: LLM Questions Generated | ❌ No |
| **Deep Analysis** | Layer 3: RAG Answers Found | ❌ No |
| **Deep Analysis** | Layer 4: Dossier Synthesized | ❌ No |
| **Watchlist** | Auto-import events | ❌ No |
| **Watchlist** | Auto-remove events | ❌ No |
| **Trading** | Buy/Sell order execution | ❌ No |
| **Trading** | Trigger fires (stop-loss, take-profit) | ❌ No |
| **Trading** | Portfolio snapshot | ❌ No |

### Why the Loop Progress Panel Doesn't Count

The autonomous loop logs to `_state['log']` (in-memory list in `autonomous_loop.py`).
These messages appear in the green progress panel while the loop is running, but:

1. They **vanish** when the loop finishes and you click "Dismiss"
2. They **don't survive** page refresh or server restart
3. They're **not per-ticker** — just generic status strings like "Discovery found 8 tickers"
4. They **don't confirm** what specific data was collected per ticker

---

## Proposed Solution: Pipeline Event Log

### New DuckDB Table: `pipeline_events`

```sql
CREATE TABLE IF NOT EXISTS pipeline_events (
    id              VARCHAR PRIMARY KEY,       -- UUID
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    phase           VARCHAR NOT NULL,          -- 'discovery' | 'collection' | 'analysis' | 'import' | 'trading'
    event_type      VARCHAR NOT NULL,          -- 'data_collected' | 'analysis_complete' | 'order_placed' | etc.
    ticker          VARCHAR,                   -- NULL for system-level events
    detail          VARCHAR NOT NULL,          -- Human-readable summary
    metadata        VARCHAR DEFAULT '{}',      -- JSON blob with counts/specifics
    loop_id         VARCHAR,                   -- Groups events from same loop run
    status          VARCHAR DEFAULT 'success', -- 'success' | 'error' | 'warning' | 'skipped'
);
```

### Event Types to Emit

#### Phase 1: Discovery

| Event Type | When | Example Detail | Metadata |
|-----------|------|----------------|----------|
| `discovery_scan_start` | Loop begins discovery | "Starting Reddit + YouTube scan" | `{"reddit": true, "youtube": true}` |
| `ticker_discovered` | Each ticker found | "$NVDA found on Reddit (+3.0)" | Already exists in `discovered_tickers` |
| `transcript_collected` | YouTube transcript downloaded | "$NVDA: transcript collected (14.2k chars)" | `{"video_id": "...", "chars": 14200, "channel": "..."}` |
| `discovery_scan_complete` | Discovery phase done | "Discovery complete: 8 tickers, 5 transcripts" | `{"tickers": 8, "transcripts": 5, "duration_s": 12.3}` |

#### Phase 2: Data Collection (per ticker)

| Event Type | When | Example Detail | Metadata |
|-----------|------|----------------|----------|
| `price_history_collected` | Price data pulled | "$NVDA: 252 daily candles collected" | `{"rows": 252, "date_range": "2025-02..2026-02"}` |
| `fundamentals_collected` | .info metrics stored | "$NVDA: 24 fundamental metrics stored" | `{"pe_ratio": 45.2, "market_cap": 2.1e12}` |
| `financials_collected` | Income statement rows | "$NVDA: 4yr financial history stored" | `{"years": 4}` |
| `balance_sheet_collected` | Balance sheet rows | "$NVDA: 4yr balance sheet stored" | `{"years": 4}` |
| `cashflow_collected` | Cash flow rows | "$NVDA: 4yr cash flow stored" | `{"years": 4}` |
| `analyst_data_collected` | Analyst targets saved | "$NVDA: analyst data (32 analysts, target $180)" | `{"num_analysts": 32, "target_mean": 180}` |
| `insider_collected` | Insider transactions | "$NVDA: insider activity (net buying $2.1M)" | `{"net_buying_90d": 2100000}` |
| `earnings_collected` | Earnings calendar | "$NVDA: earnings in 12 days" | `{"days_until": 12}` |
| `technicals_computed` | TA indicators saved | "$NVDA: 7 technical indicator groups computed" | `{"indicators": 7, "latest_rsi": 62}` |
| `risk_computed` | Risk metrics saved | "$NVDA: 25 risk metrics computed" | `{"sharpe": 1.2, "max_dd": -0.15}` |
| `news_collected` | News articles saved | "$NVDA: 12 news articles collected" | `{"count": 12, "sources": ["yfinance"]}` |
| `collection_complete` | All data for one ticker | "$NVDA: all 12 data types collected successfully" | `{"success": 12, "failed": 0, "skipped": 0}` |
| `collection_error` | A collector failed | "$NVDA: fundamentals collection failed" | `{"error": "timeout", "collector": "fundamentals"}` |

#### Phase 3: Deep Analysis (per ticker, per layer)

| Event Type | When | Example Detail | Metadata |
|-----------|------|----------------|----------|
| `quant_scorecard_computed` | Layer 1 done | "$NVDA: quant scorecard — 3 anomaly flags" | `{"flags": ["z_score_high", "volume_spike"], "z_score": 2.1}` |
| `questions_generated` | Layer 2 done | "$NVDA: 5 research questions generated" | `{"count": 5, "high_priority": 2}` |
| `rag_answers_found` | Layer 3 done | "$NVDA: 5/5 questions answered (3 high confidence)" | `{"answered": 5, "high": 3, "medium": 1, "low": 1}` |
| `dossier_synthesized` | Layer 4 done | "$NVDA: dossier generated — conviction 72%" | `{"conviction": 0.72, "signal": "BUY"}` |
| `analysis_error` | Any layer failed | "$NVDA: Layer 2 failed (LLM timeout)" | `{"layer": 2, "error": "timeout"}` |

#### Phase 4: Watchlist

| Event Type | When | Example Detail | Metadata |
|-----------|------|----------------|----------|
| `watchlist_import` | Auto-import from discovery | "$NVDA auto-imported (score: 8.5)" | `{"score": 8.5, "source": "auto_discovery"}` |
| `watchlist_remove` | Auto-removed low conviction | "$AAPL removed (conviction 0.22 for 2 cycles)" | `{"conviction": 0.22, "consecutive_low": 2}` |

#### Phase 5: Trading

| Event Type | When | Example Detail | Metadata |
|-----------|------|----------------|----------|
| `order_buy` | Buy order placed | "$NVDA: BUY 10 shares @ $145.20" | `{"qty": 10, "price": 145.20, "conviction": 0.72}` |
| `order_sell` | Sell order placed | "$NVDA: SELL 10 shares @ $148.50 (+2.3%)" | `{"qty": 10, "price": 148.50, "pnl_pct": 2.3}` |
| `trigger_fired` | Stop-loss/take-profit | "$NVDA: stop-loss triggered @ $138.00" | `{"type": "stop_loss", "price": 138.00}` |
| `signal_hold` | Ticker evaluated, no action | "$NVDA: HOLD (conviction 0.55, no action)" | `{"conviction": 0.55, "reason": "neutral_zone"}` |
| `signal_blocked` | Trade blocked by risk rules | "$NVDA: BUY blocked (daily order limit reached)" | `{"reason": "daily_limit", "orders_today": 5}` |
| `portfolio_snapshot` | EOD snapshot taken | "Portfolio snapshot: $10,245 (+2.4%)" | `{"value": 10245, "daily_pnl_pct": 2.4}` |

---

## Backend Implementation

### 1. Event Logger Service

Create `app/services/event_logger.py`:

```python
"""Pipeline Event Logger — persistent audit trail for all bot activity."""

import json
import uuid
from datetime import datetime

from app.database import get_db

_current_loop_id: str | None = None


def start_loop() -> str:
    """Generate a new loop_id for grouping events."""
    global _current_loop_id
    _current_loop_id = str(uuid.uuid4())[:8]
    return _current_loop_id


def log_event(
    phase: str,
    event_type: str,
    detail: str,
    *,
    ticker: str | None = None,
    metadata: dict | None = None,
    status: str = "success",
) -> None:
    """Write one event row to pipeline_events."""
    db = get_db()
    db.execute(
        """INSERT INTO pipeline_events
           (id, timestamp, phase, event_type, ticker, detail, metadata, loop_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            str(uuid.uuid4()),
            datetime.now().isoformat(),
            phase,
            event_type,
            ticker,
            detail,
            json.dumps(metadata or {}),
            _current_loop_id,
            status,
        ],
    )
```

### 2. Instrument the Pipeline

Add `log_event()` calls to these files (minimal changes — just add 1-line calls after each action):

| File | Where to Instrument |
|------|-------------------|
| `app/services/autonomous_loop.py` | Start/end of each `_do_*` method |
| `app/services/discovery_service.py` | After transcript collection |
| `app/services/pipeline_service.py` | After each of the 12 collector steps |
| `app/services/deep_analysis_service.py` | After each layer (1-4) completes |
| `app/services/watchlist_manager.py` | After auto-import and auto-remove |
| `app/services/paper_trader.py` | After buy/sell orders |
| `app/services/price_monitor.py` | After trigger fires |

### 3. API Endpoint

Add to `app/main.py`:

```python
@app.get("/api/pipeline/events")
async def get_pipeline_events(
    limit: int = Query(default=100, ge=1, le=500),
    phase: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    loop_id: str | None = Query(default=None),
) -> dict:
    """Get pipeline events with optional filtering."""
    # Build query with optional filters
    ...
```

---

## Frontend Implementation

### Replace Activity Log Tab Content

Instead of the current flat list of discovery mentions, render a **grouped, filterable event timeline**:

```
┌─────────────────────────────────────────────────────────┐
│ Activity Log (342)              🔍 Filter: [All Phases ▼]│
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ── Loop Run #a3f2 (2 min ago) ─────────────────────── │
│                                                         │
│  🟢 DISCOVERY                                    12s    │
│    ├─ 8 tickers discovered (Reddit: 6, YouTube: 2)      │
│    ├─ $NVDA: found on r/wallstreetbets (+3.0)          │
│    ├─ $NVDA: transcript collected (14.2k chars)        │
│    └─ ...                                              │
│                                                         │
│  🟢 COLLECTION                               45s       │
│    ├─ $NVDA: ✅ 12/12 data types collected             │
│    │   price_history(252) fundamentals(24) technicals(7)│
│    │   news(12) balance_sheet(4yr) cashflow(4yr)...     │
│    ├─ $TSLA: ✅ 12/12 data types collected             │
│    ├─ $INTC: ⚠️ 11/12 (insider_activity failed)       │
│    └─ ...                                              │
│                                                         │
│  🟢 ANALYSIS                                  38s      │
│    ├─ $NVDA: scorecard → 3 flags → 5 questions →       │
│    │         5 answers → dossier (conviction: 72% BUY) │
│    ├─ $TSLA: scorecard → 2 flags → 5 questions →       │
│    │         5 answers → dossier (conviction: 45% HOLD)│
│    └─ ...                                              │
│                                                         │
│  🟢 TRADING                                    5s      │
│    ├─ $NVDA: BUY 10 shares @ $145.20                   │
│    ├─ $TSLA: HOLD (conviction 0.45, no action)         │
│    └─ Portfolio: $10,245 (+2.4%)                       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Key UI Features

1. **Phase grouping** — events grouped by loop run, then by phase
2. **Per-ticker summary rows** — one expandable row per ticker per phase showing all collected data
3. **Status icons** — ✅ success, ⚠️ partial, ❌ error, ⏭️ skipped
4. **Phase filters** — dropdown to filter by Discovery / Collection / Analysis / Trading
5. **Ticker filter** — type to filter events for a specific ticker
6. **Collapsible groups** — click a phase header to expand/collapse details
7. **Data receipt** — the collection summary for each ticker shows counts for each data type
8. **Persistent** — survives page refresh, server restart (DuckDB-backed, not in-memory)

---

## Implementation Priority

```
1. Create pipeline_events table in database.py
2. Create event_logger.py service
3. Instrument autonomous_loop.py (loop-level events: start, end, phase transitions)
4. Instrument pipeline_service.py (per-ticker, per-collector data collection events)
5. Instrument deep_analysis_service.py (per-layer analysis events)
6. Instrument paper_trader.py + price_monitor.py (trading events)
7. Add /api/pipeline/events endpoint to main.py
8. Replace Activity Log frontend with grouped timeline
9. Add phase/ticker filter controls
```

---

## Migration: Keep Discovery Feed As-Is

The existing `discovered_tickers` table and `/api/discovery/history` endpoint stay unchanged.
The new `pipeline_events` table supplements it. The Activity Log tab switches from
querying `discovered_tickers` to querying `pipeline_events` (which includes discovery
events plus everything else).

> [!TIP]
> The discovery mention entries (Reddit snippets, YouTube sources) stay in the
> Scoreboard's expanded card view. The Activity Log becomes the **audit trail**
> for confirming the entire pipeline worked.
