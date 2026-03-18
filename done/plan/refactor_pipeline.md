Now I have the full picture. Your instincts are all correct. Here's the complete design:

***

# Cross-Examination System Design

## You're Right About the Ordering Problem

The current pipeline does things backwards. Right now `peer_fetcher.py` gets called during a trading run — meaning you're asking "who are RBRK's competitors?" **while simultaneously trying to trade RBRK**, before that data even exists.  The correct order is:

```
WRONG (current):
Trading run starts → LLM asks for peers → peers never get data collected → analysis runs blind

CORRECT (new):
Stock added to watchlist → peers found + cached immediately → 
data collected for primary + all peers → 
trading run uses fully loaded comparison set
```

***

## The Core Architecture: `peer_graph` Table

Everything hinges on one new DB table that acts as the "association map" between a primary stock and its competitors. This is the missing piece that ties your whole pipeline together.

```sql
CREATE TABLE peer_graph (
    primary_ticker   VARCHAR NOT NULL,
    peer_ticker      VARCHAR NOT NULL,
    rank             INTEGER,          -- 1-5, order of relevance
    cached_at        TIMESTAMP,        -- when this association was created
    last_validated   TIMESTAMP,        -- last time yfinance confirmed peer still valid
    PRIMARY KEY (primary_ticker, peer_ticker)
);
```

This table is the only thing that needs to be built. Everything else in your codebase already exists and can plug into it.

***

## The 5 Rules of Your Logic Flow (Exactly As You Described)

**Rule 1 — Hard cap at 5 peers per stock.**
When `peer_fetcher` runs, it stores up to 5 validated competitors in `peer_graph`. It never fetches more. The `rank` column (1–5) preserves LLM confidence order.

**Rule 2 — Cache forever, never re-fetch unless stale.**
Once `peer_graph` has a row for `(RBRK, CRWD)`, you never ask the LLM again. You only re-fetch if `cached_at` is older than 90 days (competitors don't change month-to-month, but they do change year-to-year as you said). This saves LLM tokens on every run.

**Rule 3 — Deleting a primary stock removes the association, not the peer.**
If you delete RBRK from the watchlist: `DELETE FROM peer_graph WHERE primary_ticker = 'RBRK'`. CRWD still exists in the DB independently. If another primary stock was also pointing to CRWD (e.g. `S → CRWD`), that row is untouched.

**Rule 4 — If peer ticker gets deleted from watchlist, disassociate only.**
If you delete CRWD from the watchlist: `DELETE FROM peer_graph WHERE peer_ticker = 'CRWD'`. The primary stocks that pointed to it lose that association and their peer count drops to 4. Next time they run, if they have < 5 peers, they top up.

**Rule 5 — No infinite chain. Peers of peers never become primaries.**
This is the critical guard. When you collect peers for RBRK and find CRWD, you **never** then run peer discovery on CRWD. CRWD is only ever a data-collection target, never a peer-discovery source. This hard stop prevents the exponential explosion you described.

***

## The Correct Pipeline Order (New Flow)

```
1. STOCK ADDED TO WATCHLIST
        ↓
2. PeerFetcher runs ONCE → finds up to 5 competitors → saves to peer_graph
   (only if peer_graph has < 5 rows for this stock AND cached_at < 90 days)
        ↓
3. DATA COLLECTION PHASE (runs before every trading analysis):
   ├── Collect price/technicals for PRIMARY ticker (yfinance_service)
   ├── Collect price/technicals for each PEER in peer_graph (same service)
   ├── Collect news for primary + peers (news_service / rss_news_service)
   └── All data lands in existing DB tables, tagged with ticker
        ↓
4. CROSS-EXAMINATION PHASE (new — runs after data collection):
   ├── Load primary data bucket (price, technicals, news sentiment)
   ├── Load each peer's data bucket (same fields)
   ├── Build comparison context block for LLM prompt
   └── Pass to trading_agent / portfolio_strategist
        ↓
5. TRADING DECISION with full peer context
```

***

## What the Comparison Context Block Looks Like

This is what actually gets injected into the LLM analysis prompt in Step 4:

```
PRIMARY: RBRK (Rubrik)
  Price: $68.42  |  Daily: +2.1%  |  5d: +4.8%  |  RSI: 58  |  Vol: 1.2x avg
  News sentiment: BULLISH (3 positive, 1 neutral)

PEER COMPARISON (Cybersecurity / Data Security):
  CRWD  $378.10  Daily: +4.5% ▲  5d: +7.2%  RSI: 67  → Leading sector
  PANW  $192.30  Daily: +1.8%    5d: +2.1%  RSI: 52  → In-line
  S     $18.40   Daily: -0.3% ▼  5d: -1.1%  RSI: 44  → Lagging

RELATIVE STRENGTH: RBRK is MIDDLE of peer group
SECTOR MOMENTUM: 3/4 stocks bullish → sector tailwind confirmed
```

The LLM can now answer: *"Is RBRK's move stock-specific or just a rising sector tide?"* — which is the most important short-term trading signal peer data provides.

***

## What You Actually Need to Build

You already have everything except the glue:

| What | Where | Status |
|---|---|---|
| `peer_graph` DB table | New migration in `database.py` | 🔴 Build |
| `PeerCacheService` — read/write/check stale | New `peer_cache_service.py` | 🔴 Build |
| Data collection loop for peers | `yfinance_service.py` already works per-ticker | 🟡 Wire up |
| Comparison context builder | New method in `pipeline_service.py` or `trading_agent.py` | 🔴 Build |
| Trigger peer fetch on watchlist add | `watchlist_manager.py` | 🟡 Wire up |
| Delete cascade on watchlist remove | `watchlist_manager.py` | 🟡 Wire up |
| Inject comparison block into LLM prompt | `trading_agent.py` or `portfolio_strategist.py` | 🔴 Build |

The two biggest files that need to be touched are [`watchlist_manager.py`](https://github.com/LazyCat420/Lazy-Trading-Bot/blob/2e6cd6871a06969614719dc17bf32de86b5110d7/app/services/watchlist_manager.py) (trigger peer fetch on add/delete) and [`trading_agent.py`](https://github.com/LazyCat420/Lazy-Trading-Bot/blob/2e6cd6871a06969614719dc17bf32de86b5110d7/app/services/trading_agent.py) (inject comparison block into prompt).  The new files you need to create are `peer_cache_service.py` and the DB migration for `peer_graph`.

***

## One Improvement to Your Logic You Should Add

**Bidirectional awareness, not bidirectional collection.** If RBRK lists CRWD as a peer, and CRWD is *also* independently in your watchlist as a primary stock — when you analyze CRWD, RBRK should automatically appear in its peer comparison block by doing a reverse lookup: `SELECT primary_ticker FROM peer_graph WHERE peer_ticker = 'CRWD'`. This gives you free cross-referencing without any extra LLM calls, just a DB query. You'd know RBRK is moving on the same day CRWD is moving, automatically.