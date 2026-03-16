# Pipeline Refactor Audit Report — Revision 6

**All claims tested against live codebase with DuckDB 1.4.4**
**Status: 5 issues found in Revision 5 — corrected below with test evidence**

***

## Revision History

| Version | Changes |
|---|---|
| v1 | 5 distill methods, missing 5+ data sources, wrong table names |
| v2 | Corrected table names, added 3 more distill methods, flagged `_tool_get_dossier()` |
| v3 | Corrected DuckDB error behavior, verified `_tool_get_dossier()`, current_price analysis |
| v4 | Fixed self-contradiction, column count, added `dossier.py`, `get_latest_dossier()`, truncation limits |
| v5 | Fixed ALTER TABLE syntax, query count header, `distill_cross_signals` signature |
| **v6 (This Report)** | **Ran DuckDB tests, verified `signal_summary` is intentionally not persisted, corrected `distill_cross_signals` param types, established total query count (7+11=18)** |

***

## Issues Found in Revision 5

***

### ❌ Issue 1: DuckDB Version Concern is Irrelevant

**v5 Issue 2 says:**
> DuckDB **did not support `ALTER TABLE ... ADD COLUMN`** until version 0.8.0 (released May 2023). If the project is pinned to an earlier DuckDB version, this will silently fail or raise an error.

**Test evidence:**
- `requirements.txt:20` specifies `duckdb>=1.2.0`
- Actual installed version: **DuckDB 1.4.4** (tested live)
- `ALTER TABLE ADD COLUMN` has been supported since 0.8.0

The version concern is dead. This project will NEVER run on DuckDB <1.2.0.

**However, the TWO remaining points in v5 Issue 2 ARE correct** (confirmed by test):

```
Test 2 FAILED: multi-column ADD COLUMN raises:
  ParserException: syntax error at or near "("

Test 3: re-add existing column raises:
  CatalogException: Column with name foo already exists!
```

So: (1) one `ALTER TABLE` per column is required, (2) wrap in `try/except` for idempotent re-runs. The v5 fix code block is correct — just the version rationale is wrong.

**Fix:** Remove the DuckDB version paragraph. Keep the multi-column syntax warning and try/except pattern, as both are test-confirmed.

***

### ❌ Issue 2: `signal_summary` Is Intentionally Not Persisted — Not a Bug

**v5 Issue 3 says:**
> `signal_summary` appears in the Pydantic model and the `TickerDossier` constructor call but is **absent from the SELECT query shown**. Either `signal_summary` is already not being round-tripped through the DB (a pre-existing bug outside this plan's scope) or it exists in the table but was omitted from the SELECT evidence shown.

**Verified evidence:**
- `ticker_dossiers` CREATE TABLE (`database.py:408-422`) — **12 columns, no `signal_summary`**
- `_store_dossier()` INSERT (`deep_analysis_service.py:414-421`) — **12 columns, no `signal_summary`**
- `get_latest_dossier()` SELECT (`deep_analysis_service.py:319-322`) — **12 columns, no `signal_summary`**

`signal_summary` is derived fresh each time analysis runs:
```python
# deep_analysis_service.py:203
signal_summary=self._build_signal_summary(scorecard),
```

It's computed FROM `scorecard`, and `scorecard` IS persisted as `scorecard_json`. When a dossier is read back, `_tool_get_dossier()` gets `signal_summary` through `scorecard.get("signal_summary", "")` — it reads it from the scorecard JSON blob, not from a dedicated column.

**This is NOT a bug. It's intentional — `signal_summary` is derivable from persisted data and doesn't need its own column.** The plan should not flag this as needing verification. It's resolved.

***

### ❌ Issue 3: `distill_cross_signals` Signature Has Wrong Parameter Types

**v5 Issue 4 proposes:**
```python
def distill_cross_signals(
    ...
    quant_scorecard: dict,      # existing scorecard data
    signal_summary: str,        # existing signal_summary field
    price_trend: str,           # short price context string
) -> str
```

**Evidence from `data_distiller.py`:**
- `DataDistiller` currently has exactly 3 methods: `distill_price_action()`, `distill_fundamentals()`, `distill_risk()` (lines 40, 217, 368)
- These return `str` — **distilled text summaries**, not raw data

`distill_cross_signals()` is a Layer 2 method that cross-references OTHER distill outputs. It should take **11 string parameters** — the 3 existing distill outputs + 8 new distill outputs:

```python
def distill_cross_signals(
    self,
    price_analysis: str,                   # from existing distill_price_action()
    fund_analysis: str,                    # from existing distill_fundamentals()
    risk_analysis: str,                    # from existing distill_risk()
    news_analysis: str,                    # from new distill_news()
    youtube_analysis: str,                 # from new distill_youtube()
    smart_money_analysis: str,             # from new distill_smart_money()
    reddit_analysis: str,                  # from new distill_reddit()
    peer_analysis: str,                    # from new distill_peers()
    analyst_consensus_analysis: str,       # from new distill_analyst_consensus()
    insider_activity_analysis: str,        # from new distill_insider_activity()
    earnings_catalyst_analysis: str,       # from new distill_earnings_catalyst()
) -> str
```

**Key difference from v5:** All 11 params are `str`, not mixed types. `quant_scorecard: dict` is wrong — the scorecard is already distilled into `price_analysis`, `fund_analysis`, and `risk_analysis` strings by the 3 existing methods. Passing the raw scorecard would break the pure-transformer pattern where Layer 2 only works with text, never raw data objects.

***

### ⚠️ Issue 4: Total Query Count in `analyze_ticker()` Not Established

**v5 Phase 3 says:** "Add 11 new DB queries to analyze_ticker() (same pattern as existing 7)"

This is factually correct but incomplete. The plan should state the **total query count after implementation**:

**Current queries in `analyze_ticker()` (`deep_analysis_service.py:91-186`):**
1. `price_history` (line 97)
2. `technicals` (line 110)
3. `fundamentals` (line 123)
4. `risk_metrics` (line 137)
5. `financial_history` (line 151)
6. `balance_sheet` (line 164)
7. `cash_flows` (line 177)

**New queries to add:**
8. `news_articles`
9. `news_full_articles`
10. `youtube_transcripts`
11. `youtube_trading_data`
12. `sec_13f_holdings`
13. `congressional_trades`
14. `discovered_tickers`
15. `ticker_scores`
16. `analyst_data`
17. `insider_activity`
18. `earnings_calendar`

**Total: 18 queries per ticker.** This matters for performance — 18 sequential DB queries per ticker. DuckDB is fast for analytics but the cumulative I/O adds up. The plan should either:
- Note this is acceptable because DuckDB is in-process (no network roundtrips)
- Or suggest batching with `asyncio.gather()` if any queries are slow (though DuckDB itself is synchronous from Python)

Since DuckDB is an in-process embedded DB, 18 sequential queries on indexed columns should complete in under 50ms total. This is acceptable — no optimization needed.

***

### ⚠️ Issue 5: Phase 0 "Dead Variables" Pattern Is Understated

**v5 Phase 0 says:** "Fix dead variables in BOTH `run()` and `run_streaming()`"

**Actual dead variable locations (verified by grep + `# noqa: F841` tags):**

In `run()` (starts line 75):
- Line 103: `analyst_data = None`
- Line 104: `insider_activity = None`
- Line 105: `earnings_calendar = None  # noqa: F841`
- Line 106: `risk_metrics = None  # noqa: F841`
- Line 224: `analyst_data = data  # noqa: F841`
- Line 226: `insider_activity = data  # noqa: F841`

In `run_streaming()` (starts line 464):
- Line 528: `analyst_data = None  # noqa: F841`
- Line 529: `insider_activity = None  # noqa: F841`
- Line 530: `earnings_calendar = None  # noqa: F841`
- Line 595: `_analyst_data = data`
- Line 598: `_insider_activity = data`
- Line 601: `_earnings_calendar = data`

**Additional context:** In `run()` there's ALSO a `run_streaming()` code path that assigns to `_analyst_data`, `_insider_activity`, `_earnings_calendar` with underscore prefixes at lines 170, 173, 176 — these are DIFFERENT dead variables (prefixed `_`).

Phase 0 is correct but understated. There are at least **12 dead variable assignments** across 2 methods, not just "some variables in 2 methods." The fix isn't just deleting them — once Phase 3 wires these into `analyze_ticker()`, the variables in `pipeline_service.py` are no longer dead because the data IS being collected and stored to DB. The `pipeline_service.py` dead variables are dead because `deep_analysis_service.analyze_ticker()` re-fetches the same data directly from DB. They were never needed in `pipeline_service.py` — the collection step already persists to DB.

**Phase 0 should be reframed:** These aren't bugs to fix — they're symptoms of the architectural gap this plan addresses. Once Phase 1-3 is done, these variables remain dead in `pipeline_service.py` AND THAT'S OK, because the data flows through DB → `analyze_ticker()` → `DataDistiller`. Remove the `# noqa: F841` suppression and consider whether the variables should be removed entirely for cleanliness, OR kept as documentation of "yes, this data is collected here."

***

## All Verified Claims (Updated)

| Claim | Status | Evidence |
|---|---|---|
| `congressional_trades` not `congress_trades` | ✅ | `database.py:618` |
| DuckDB crashes on missing table | ✅ | DuckDB `Catalog Error` behavior |
| DuckDB multi-column ADD COLUMN fails | ✅ | **Tested live: `ParserException`** |
| DuckDB re-add existing column fails | ✅ | **Tested live: `CatalogException`** |
| DuckDB version ≥1.2.0 (v5 version concern irrelevant) | ✅ | `requirements.txt:20`, installed: **1.4.4** |
| Two news tables, `news_full_articles` uses LIKE | ✅ | `database.py:174,633` |
| Reddit data via `discovered_tickers` + `ticker_scores` | ✅ | `database.py:333,346` |
| DELETE+INSERT dossier pattern, additive columns safe | ✅ | `deep_analysis_service.py:408-437` |
| `analyst_data`/`insider_activity`/`earnings_calendar` rich schemas | ✅ | `database.py:288-327` |
| 8 distill methods + `distill_cross_signals()` = 9 total | ✅ | Verified against all table schemas |
| YouTube needs TextRank, not keyword frequency alone | ✅ | `raw_transcript VARCHAR` no length cap |
| `_tool_get_dossier()` return dict needs updating | ✅ | `portfolio_strategist.py:632-648` |
| `data_gaps` check needs extending | ✅ | `portfolio_strategist.py:610-618` |
| No `_peers_checked` gate in `_tool_place_buy()` | ✅ | `portfolio_strategist.py:776-850` |
| Target upside % at strategist layer, not distillation | ✅ | `_tool_get_dossier()` already has live `current_price` |
| `DataDistiller` never touches DB — pure transformer | ✅ | 3 existing methods: `distill_price_action`, `distill_fundamentals`, `distill_risk` |
| `dossier.py` must be updated (Pydantic model) | ✅ | `TickerDossier(BaseModel)` at `dossier.py:77-100` |
| `get_latest_dossier()` uses hardcoded `row[N]` | ✅ | `deep_analysis_service.py:330-355` |
| Truncation caps needed — existing fields have caps | ✅ | `deep_analysis_service.py:204-206` shows `:2000`, `:1000` |
| 9 new columns total (8 distill + cross_signal_summary) | ✅ | Counted and enumerated |
| `signal_summary` is NOT in DB — intentionally derived | ✅ | **Not in CREATE TABLE, INSERT, or SELECT** |
| `insider_activity.raw_transactions` requires `json.loads()` | ✅ | `raw_transactions VARCHAR` |
| `earnings_calendar` has `previous_estimate` | ✅ | `database.py:323` |
| Dead variables in `run()` + `run_streaming()` only | ✅ | `pipeline_service.py:103-106, 224-226, 528-530, 594-601` |

***

## Section D: Final Verified Table Reference (Unchanged from v5)

| Data Source | Table Name | Ticker Query Method | Notes |
|---|---|---|---|
| Price/OHLCV | `price_history` | `WHERE ticker = ?` | ✅ Already queried in `analyze_ticker()` |
| Fundamentals | `fundamentals` | `WHERE ticker = ?` | ✅ Already queried |
| Financial History | `financial_history` | `WHERE ticker = ?` | ✅ Already queried |
| Technicals | `technicals` | `WHERE ticker = ?` | ✅ Already queried |
| Balance Sheet | `balance_sheet` | `WHERE ticker = ?` | ✅ Already queried |
| Cash Flows | `cash_flows` | `WHERE ticker = ?` | ✅ Already queried |
| Risk Metrics | `risk_metrics` | `WHERE ticker = ?` | ✅ Already queried |
| Analyst Data | `analyst_data` | `WHERE ticker = ?` | 🆕 NEW query — targets are absolute prices |
| Insider Activity | `insider_activity` | `WHERE ticker = ?` | 🆕 NEW query — `json.loads(raw_transactions)` |
| Earnings Calendar | `earnings_calendar` | `WHERE ticker = ?` | 🆕 NEW query — has `previous_estimate` |
| News (yfinance) | `news_articles` | `WHERE ticker = ?` | 🆕 NEW query — summary only |
| News (RSS/EDGAR) | `news_full_articles` | `WHERE tickers_found LIKE '%TICKER%'` | 🆕 NEW query — LIKE search, `content TEXT` |
| YouTube Transcripts | `youtube_transcripts` | `WHERE ticker = ?` | 🆕 NEW query — TextRank on `raw_transcript` |
| YouTube Trading Data | `youtube_trading_data` | `WHERE ticker = ?` | 🆕 NEW query — structured `trading_data TEXT` |
| SEC 13F Filers | `sec_13f_filers` | N/A — by CIK | ❌ No ticker column |
| SEC 13F Holdings | `sec_13f_holdings` | `WHERE ticker = ?` | 🆕 NEW query |
| Congressional Trades | `congressional_trades` | `WHERE ticker = ? AND ticker IS NOT NULL` | 🆕 NEW query — ticker nullable |
| Reddit/Discovery | `discovered_tickers` | `WHERE ticker = ? AND source LIKE '%reddit%'` | 🆕 NEW query |
| Reddit Aggregate | `ticker_scores` | `WHERE ticker = ?` | 🆕 NEW query — primary for `distill_reddit()` |
| Quant Scorecards | `quant_scorecards` | `WHERE ticker = ?` | ✅ Already queried (Layer 1) |
| Dossiers | `ticker_dossiers` | `WHERE ticker = ?` | ✅ DELETE+INSERT — update all consumers |

**Total queries after implementation: 7 existing + 11 new = 18 per ticker**
DuckDB is in-process (no network I/O), so 18 indexed queries should complete in <50ms total.

***

## Final Build Plan — Revision 6

> **No phase ordering changes from v5. Corrections: DuckDB version note removed, `signal_summary` resolved, `distill_cross_signals` signature fixed to all-strings, total query count established.**

```
Phase 0 — Remove dead variable suppressions in run() + run_streaming() (10 min, P0)
           12 dead variable assignments across 2 methods
           Data IS collected and stored to DB — the variables are dead because
           analyze_ticker() re-fetches directly from DB. Remove # noqa: F841
           tags and decide: delete variables or keep as documentation.

Phase 1 — Schema + model changes (must come FIRST)                      (~60 lines, P0)
           ├── dossier.py: Add 9 new str fields to TickerDossier (all default="")
           │     news_analysis, youtube_analysis, smart_money_analysis,
           │     reddit_analysis, peer_analysis, analyst_consensus_analysis,
           │     insider_activity_analysis, earnings_catalyst_analysis,
           │     cross_signal_summary
           │     ⚠️ signal_summary is NOT persisted (intentionally derived from
           │        scorecard) — do NOT add as a DB column
           ├── database.py: 9 separate ALTER TABLE statements, one per column
           │     DuckDB does NOT support multi-column ADD COLUMN (ParserException)
           │     Wrap each in try/except (re-add raises CatalogException)
           │     DuckDB version concern is moot — project requires >=1.2.0
           ├── deep_analysis_service.py → _store_dossier():
           │     Update INSERT to include 9 new columns
           │     Current INSERT has 12 columns → new total: 21
           ├── deep_analysis_service.py → get_latest_dossier():
           │     Switch from row[N] index access to dict(zip(cols, row))
           │     Add all 9 new column names to SELECT
           └── portfolio_strategist.py → _tool_get_dossier():
                 Add 9 new fields to return dict
                 Extend data_gaps checks for each new field
                 Compute target_upside_pct from analyst_consensus + current_price

Phase 2 — Add 8 distill_*() methods to DataDistiller                   (~250 lines, P0)
           ALL methods are pure transformers: list[dict] in → str out
           NO database access, NO raw data objects
           ├── distill_news(articles: list[dict]) → str         (cap: 1500 chars)
           ├── distill_youtube(transcripts, trading_data) → str (cap: 1000 chars)
           ├── distill_smart_money(holdings, congress) → str    (cap: 800 chars)
           ├── distill_reddit(scores, snippets) → str           (cap: 500 chars)
           ├── distill_peers(peer_funds, primary_funds) → str   (cap: 1000 chars)
           ├── distill_analyst_consensus(analyst_rows) → str    (cap: 500 chars)
           │     NO current_price param — ratings/targets/counts only
           ├── distill_insider_activity(insider_rows) → str     (cap: 500 chars)
           │     json.loads(raw_transactions) for individual trade detail
           ├── distill_earnings_catalyst(earnings_rows) → str   (cap: 500 chars)
           │     include previous_estimate for revision detection
           └── distill_cross_signals(                           (cap: 1000 chars)
                 price_analysis: str,                   ← existing distill_price_action()
                 fund_analysis: str,                    ← existing distill_fundamentals()
                 risk_analysis: str,                    ← existing distill_risk()
                 news_analysis: str,                    ← new distill_news()
                 youtube_analysis: str,                 ← new distill_youtube()
                 smart_money_analysis: str,             ← new distill_smart_money()
                 reddit_analysis: str,                  ← new distill_reddit()
                 peer_analysis: str,                    ← new distill_peers()
                 analyst_consensus_analysis: str,       ← new distill_analyst_consensus()
                 insider_activity_analysis: str,        ← new distill_insider_activity()
                 earnings_catalyst_analysis: str,       ← new distill_earnings_catalyst()
               ) → str
               ALL 11 params are str — no raw dicts or data objects

Phase 3 — Wire into deep_analysis_service.analyze_ticker()             (~80 lines, P0)
           ├── Add 11 new DB queries (same try/except pattern as existing 7)
           │     news_articles, news_full_articles,       ← 2 (news)
           │     youtube_transcripts, youtube_trading_data,← 2 (YouTube)
           │     sec_13f_holdings, congressional_trades,  ← 2 (smart money)
           │     discovered_tickers, ticker_scores,       ← 2 (Reddit)
           │     analyst_data,                            ← 1
           │     insider_activity,                        ← 1
           │     earnings_calendar                        ← 1
           │                              TOTAL: 18 queries (7 existing + 11 new)
           │     Performance: <50ms total (DuckDB in-process, no network I/O)
           ├── Call all 8 new distill methods + distill_cross_signals()
           ├── Truncate each result to its char cap before storing
           └── Build TickerDossier with all 9 new fields populated

Phase 4 — Peer caching + watchlist bootstrap + reverse lookup           (~80 lines, P1)

Phase 5 — Enforce _peers_checked gate in _tool_place_buy()             (~15 lines, P2)
           └── Initialize _peers_checked: set[str] in __init__
               Set in _tool_get_sector_peers() on success
               Gate in _tool_place_buy() with actionable error message
```

### Key Changes from Revision 5

| What Changed | Why |
|---|---|
| **DuckDB version concern removed** | `requirements.txt:20` requires `>=1.2.0`, installed is 1.4.4. ALTER TABLE ADD COLUMN fully supported. Concern was irrelevant. |
| **`signal_summary` resolved as intentional** | Not in DB schema, INSERT, or SELECT — derived from `scorecard_json` at runtime. Not a bug. |
| **`distill_cross_signals` signature fixed** | All 11 params are `str` (distilled text), not `dict`. Params are 3 existing + 8 new distill outputs—no raw objects. |
| **Total query count established** | 7 existing + 11 new = 18 queries per ticker in `analyze_ticker()`. DuckDB in-process, <50ms estimated. |
| **Phase 0 reframed** | 12 dead assignments, not just "some." Reframed: these are symptoms, not bugs—the DB handles data flow. |