Fix this in three layers: (1) **ticker validation at ingestion** so non-assets like `$READ` never enter the scoreboard, (2) add a **user exclusion/delete** feature that persists (so it doesn’t come back next run), and (3) rebuild the **filter system** as a single, testable “filter pipeline” that runs before anything writes to DuckDB/watchlist/scoreboard.  

I don’t have the exact diff of your newest refactor commit in this chat, so the plan below assumes your new structure is heading toward `services/`, `components/`, `agents/` and that you still have a watchlist/scoreboard persisted in DuckDB and updated during analysis runs (similar to how your analysis currently updates the `watchlist` row and can auto-remove junk tickers) .

## 1) Block `$READ` (symbol validation)

**Goal:** Only real, tradeable symbols can be inserted into scoreboard/watchlist/quant tables.

Ticket A — Add `SymbolNormalizer` + `SymbolValidator` (components)

- `normalize_symbol(raw: str) -> str | None`: strip `$`, trim spaces, uppercase, collapse weird unicode, reject empty.
- `is_valid_symbol_format(sym: str)`: allow `[A-Z][A-Z0-9.\-]{0,9}` (tune length to your universe), explicitly reject anything containing `/`, whitespace, emoji, etc.
- `is_tradeable_asset(sym: str)`: verify against your broker/exchange asset list *or* a market-data source (recommended: broker list if you trade through a broker; fallback: yfinance quoteType / market price presence).

Ticket B — Enforce validation at **every insertion point**

- Wherever symbols are discovered/extracted (LLM output, news parsing, “$TICKER” regex, UI add-ticker), run:
  1) normalize → 2) format-check → 3) asset-check → 4) “not in user exclusions” → only then write.
- Add a hard guard right before any DB write: `assert validate_symbol(symbol)`; if not valid, log + skip.

Ticket C — Add a “quarantine” table/log for rejects

- Create `rejected_symbols(symbol, source, reason, created_at, raw_payload_hash)` so you can see *where* `$READ` originated (LLM answer, transcript parsing, UI, etc.) and stop it at the source.

Acceptance criteria

- `$READ` (and any `$<word>`) never appears in scoreboard/watchlist DB again unless it passes the tradeable asset check.
- The pipeline run completes even if 100 bad symbols appear in upstream text (they’re skipped + logged).

## 2) Manual delete from scoreboard (persisted exclusions)

**Goal:** Users can remove symbols, and removals persist across refresh/runs.

Ticket D — Add `UserExclusionsService` (services)

- DB table: `user_exclusions(id, bot_id, symbol, created_at, reason, created_by)` (bot-scoped so one bot doesn’t poison another).
- API:
  - `DELETE /api/scoreboard/{symbol}` → adds (or upserts) `user_exclusions` + removes from scoreboard/watchlist rows.
  - `GET /api/exclusions` → list excluded symbols for UI.
  - Optional: `POST /api/exclusions/{symbol}/restore` to undo.
- Pipeline integration: add `UserExclusionFilter` early so excluded symbols never get re-added.

Ticket E — UI: delete button + “excluded” view

- Scoreboard row action: “Remove” (confirm dialog).
- Provide a small “Excluded symbols” drawer so users can restore.

Acceptance criteria

- Deleted symbol disappears immediately from UI.
- Next scheduled run does not re-add it (because exclusion filter blocks it).

## 3) Fix the filter system (single pipeline)

**Goal:** One place defines “what is allowed into the scoreboard,” with consistent behavior across discovery/manual add/LLM extraction.

Ticket F — Create a composable `FilterPipeline`
Implement `Filter` interface like:

- `apply(symbol, context) -> (pass|fail, reason_code, metadata)`

Recommended default order:

1) `NormalizeFilter` (strip `$`, uppercase)
2) `SymbolFormatFilter` (regex / length)
3) `UserExclusionFilter` (manual deletions)
4) `AssetTypeFilter` (equity/etf only; optionally allow crypto if supported)
5) `MarketDataAvailableFilter` (has price, volume, etc.)
6) `LiquidityFilter` (min avg volume, min price, max spread proxy)
7) `QualityGateFilter` (your “junk flags” logic; today you already have a junk gate that can auto-remove tickers when flags hit)
8) `CooldownFilter` (don’t reprocess too often)

Ticket G — Make filters configurable (no code edits)

- Put thresholds in config: min price, min avg volume, allowed exchanges, allowed quoteTypes, etc.
- Add a “Filter Debug” mode: return per-symbol failure reason to the UI (why it didn’t make scoreboard).

Acceptance criteria

- Every symbol has a deterministic reason for inclusion/exclusion.
- Filter behavior is identical whether ticker came from discovery, user add, or LLM output.

## 4) Implementation steps (1-week executable plan)

Day 1–2

- Implement `SymbolNormalizer` + `SymbolValidator`.
- Add DB table `user_exclusions` + service.
- Wire validation into all write paths (scoreboard/watchlist insert/update).

Day 3

- Implement filter pipeline + initial filters (normalize, format, exclusions, asset-check).
- Add quarantine logging for rejected symbols.

Day 4

- Add manual delete endpoints + UI button.
- Ensure delete also removes existing rows from scoreboard/watchlist and prevents re-add.

Day 5

- Add filter debug surface in UI.
- Add tests + regression: `$READ` input from every source path.

## 5) Tests you should require

- Unit tests:
  - `normalize_symbol("$read") == "READ"`, then fails asset-check.
  - Symbols with spaces/slashes fail format filter.
- Integration tests:
  - “LLM returns `$READ` in JSON” → pipeline skips symbol; does not write DB.
  - User deletes symbol → it is excluded on next run.
- Regression on LLM JSON handling:
  - Keep using your current LLM JSON retry/cleanup behavior (it already retries when JSON format comes back empty and strips to the first JSON object) to reduce breakage from malformed outputs. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/26893963/a718800b-dc8b-4b73-8fe7-b578a8a38a66/llm_service.py)

If you paste (a) the file name that builds the scoreboard list and (b) the exact place you saw `$READ` getting inserted (log line or DB table), I can turn Ticket A/B into exact code-level diffs (functions + insertion points) for your dev team.
