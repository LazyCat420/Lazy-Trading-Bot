# Phase 2 — Automated Watchlist Management

> **Goal**: Replace the static `watchlist.json` with an intelligent, auto-managed
> watchlist that adds high-scoring tickers from Discovery, runs the full analysis
> pipeline, and removes stale/poor performers — all without manual intervention.

---

## 2.1 — How the Watchlist Works Today

Currently the watchlist is a flat JSON file:

```json
// app/user_config/watchlist.json
{ "tickers": ["NVDA", "MSFT", "GOOG", "PLTR", "NBIS", "CRWV", "HOOD", "PH", "MOG-A"] }
```

Tickers are manually added/removed by the user through the dashboard.
The pipeline (`pipeline_service.py`) analyzes whichever tickers are in this list.

---

## 2.2 — New Auto-Managed Watchlist

### Design Principles

1. **Discovery feeds the watchlist** — High scoring tickers from Phase 1 are auto-added
2. **User tickers are sacred** — Manually added tickers are never auto-removed
3. **Conviction-based rotation** — Low-conviction tickers age out, making room for fresh leads
4. **Size cap** — Max 20 tickers to keep pipeline cycle time under control
5. **Cooldown** — A removed ticker can't be re-added for 7 days (prevents thrashing)

### Watchlist Entry Model

```python
class WatchlistEntry(BaseModel):
    ticker: str
    source: Literal["manual", "auto_discovery"]
    added_at: datetime
    discovery_score: float = 0.0          # From Phase 1
    analysis_score: float = 0.0           # From pipeline (BUY=1.0, HOLD=0.5, SELL=0.0)
    last_analyzed: datetime | None = None
    times_analyzed: int = 0
    status: Literal["active", "pending_analysis", "cooldown", "removed"]
    position_held: bool = False           # True if we have an open position
```

### Auto-Add Logic

```
After each Discovery run:
    1. Get top-N scored tickers from ticker_scores table
    2. Filter out:
       - Already on watchlist
       - On cooldown (removed < 7 days ago)
       - Failed validation
    3. Sort by total_score descending
    4. Add up to (MAX_SIZE - current_size) tickers
    5. Mark as status="pending_analysis"
    6. Trigger pipeline run for new additions
```

### Auto-Remove Logic

```
After each analysis cycle:
    1. For each auto-discovery ticker (NOT manual):
       - If signal == "SELL" for 2+ consecutive analyses → remove
       - If no new discovery mentions in 5 days AND signal != "BUY" → remove
       - If discovery_score has decayed below threshold → remove
    2. NEVER remove a ticker with position_held=True
    3. NEVER remove a manual ticker
    4. Removed tickers get cooldown timestamp
```

### Score Decay

Discovery scores decay over time to ensure freshness:

```
effective_score = base_score × decay_factor
decay_factor = max(0.1, 1.0 - (days_since_last_mention × 0.15))
```

| Days | Decay Factor | Example (score=10) |
|------|-------------|---------------------|
| 0 | 1.0 | 10.0 |
| 1 | 0.85 | 8.5 |
| 2 | 0.70 | 7.0 |
| 3 | 0.55 | 5.5 |
| 5 | 0.25 | 2.5 |
| 7 | 0.10 | 1.0 (floor) |

---

## 2.3 — DuckDB Persistence

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    ticker           VARCHAR PRIMARY KEY,
    source           VARCHAR DEFAULT 'manual',     -- 'manual' | 'auto_discovery'
    added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovery_score  DOUBLE DEFAULT 0.0,
    analysis_score   DOUBLE DEFAULT 0.0,
    last_analyzed    TIMESTAMP,
    times_analyzed   INTEGER DEFAULT 0,
    status           VARCHAR DEFAULT 'active',
    position_held    BOOLEAN DEFAULT FALSE,
    last_signal      VARCHAR DEFAULT 'HOLD',       -- BUY/HOLD/SELL
    consecutive_sell INTEGER DEFAULT 0,
    removed_at       TIMESTAMP                     -- For cooldown tracking
);
```

### Migration from `watchlist.json`

On first run, import existing `watchlist.json` entries as `source="manual"`.
After migration, `watchlist.json` becomes a read-cache (written from DuckDB for
backward compatibility with existing dashboard code).

---

## 2.4 — New file: `app/services/watchlist_manager.py`

```python
class WatchlistManager:
    """Manages the automated watchlist lifecycle."""

    MAX_SIZE = 20
    COOLDOWN_DAYS = 7
    MIN_DISCOVERY_SCORE = 3.0   # Minimum to auto-add
    STALE_DAYS = 5              # Days without mention before removal eligible
    CONSECUTIVE_SELL_LIMIT = 2  # Sell signals before auto-remove

    def get_active_tickers(self) -> list[str]:
        """Return current active watchlist tickers."""

    def add_manual(self, ticker: str) -> bool:
        """User manually adds a ticker. Always succeeds (up to MAX_SIZE)."""

    def remove_manual(self, ticker: str) -> bool:
        """User manually removes a ticker."""

    def process_discovery_results(self, scored: list[ScoredTicker]) -> list[str]:
        """
        Auto-add top candidates from discovery.
        Returns list of newly added tickers.
        """

    def process_analysis_result(self, ticker: str, decision: FinalDecision):
        """
        Update watchlist entry after pipeline analysis.
        May trigger auto-removal if sell threshold exceeded.
        """

    def sync_to_json(self):
        """Write current active tickers to watchlist.json for backward compat."""
```

---

## 2.5 — API Endpoints

```
GET    /api/watchlist                → Get full watchlist with metadata
POST   /api/watchlist/{ticker}       → Manually add ticker (source=manual)
DELETE /api/watchlist/{ticker}        → Manually remove ticker
GET    /api/watchlist/auto-manage     → Trigger auto-add/remove cycle
GET    /api/watchlist/cooldown        → List tickers on cooldown
```

---

## 2.6 — Frontend Changes

Enhance existing watchlist table to show:

- **Source badge**: "Manual" (blue) vs "Auto" (green)
- **Discovery score**: Trending indicator
- **Status**: active / pending / cooldown
- **Days on list**: How long the ticker has been tracked
- **Auto-remove countdown**: Visual indicator before auto-removal

---

## Testing Plan

1. **Unit tests** for score decay calculation
2. **Unit tests** for auto-add logic (respects max size, cooldown, validation)
3. **Unit tests** for auto-remove logic (protects manual, protects positions)
4. **Integration test**: Discovery → auto-add → pipeline → auto-remove lifecycle
5. **Migration test**: Import from watchlist.json → DuckDB → export back

## Dependencies

- Phase 1 (Ticker Discovery) must be complete
- Existing: `pipeline_service.py`, `FinalDecision`, DuckDB
