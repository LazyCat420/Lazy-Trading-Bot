# Trading Pipeline Audit — Last 3 Runs (2026-03-03)

## Runs Audited

| # | Loop ID | Model | Duration | Tickers | Orders | LLM Calls |
|---|---------|-------|----------|---------|--------|-----------|
| 1 | `f2cba32a` | gemma3:4b (ctx=98304) | 8m 45s | 7 analyzed | **8 orders** | 32 |
| 2 | `12ab5d9d` | GLM-4-32B-Q4_K_M (ctx=32768) | 15m 6s | 7 analyzed | 2 orders | 30 |
| 3 | `ca11eed3` | gemma3:27b (ctx=32768) | 19m 20s | 15 analyzed | 2 orders | 37 |

> [!IMPORTANT]
> Most recent granite3.2:8b-50k run (not in health reports but in DB) analyzed **33 tickers**, issued **only 2 BUYs** (PLTR, ZS) — everything else was HOLD at exactly 60% confidence. This suggests the LLM is seeing insufficient context to make real decisions.

---

## 🔴 Critical Bugs

### 1. Dossiers Have No Price/Technical Data — `DataDistiller` Object vs Dict Mismatch

**Root cause:** `data_distiller.py:52` extracts prices with:

```python
closes = [float(p.close) for p in prices if hasattr(p, "close")]
```

But `deep_analysis_service.py:100-104` loads prices from DuckDB as **dicts** (`dict(zip(cols, r))`), not objects. Dicts don't have `hasattr(d, "close")` → the list comprehension yields **zero closes** → every dossier says `"Insufficient price data for pattern analysis."`.

**Impact:** 100% of dossiers across all 3 runs have zero price analysis, zero trend detection, zero crossover detection. The LLM gets blank technical context.

**Fix:** Change `distill_price_action` to handle both dicts and objects:

```python
closes = [float(p.get("close", 0) if isinstance(p, dict) else p.close) for p in prices if ...]
```

Apply the same pattern to `distill_fundamentals`, `distill_risk`, and all `getattr` calls on `technicals`.

---

### 2. Conviction Score ↔ Trading Decision Disconnect

The deep analysis sets conviction scores purely from quant signals (no LLM), but the trading agent ignores them completely. Evidence:

| Ticker | Dossier Conviction | Dossier Signal | Trading Decision | Trading Confidence |
|--------|-------------------|----------------|------------------|--------------------|
| ZS | **0.00** | SELL | **BUY** | 0.70 |
| PLTR | 0.35 | HOLD | **BUY** | 0.75 |
| KHC | 0.00 | SELL | HOLD | 0.70 |
| ADI | 0.75 | BUY | HOLD | 0.60 |

**Root cause:** The trading agent prompt in `trading_agent.py` only receives `technical_summary`, `quant_summary`, and `news_summary` fields from the dossier. It never sees the actual `conviction_score` or `signal`. Since all dossiers are empty (bug #1), the LLM only has price/volume/change data from yfinance `fast_info` — it defaults to HOLD.

**Fix:** Add conviction score and dossier signal to the trading agent context in `_build_context()` and `_build_prompt()`.

---

### 3. YouTube Scanner LLM Returns Dict Instead of List

**Warning:** `[YouTube Scanner] LLM returned non-list: <class 'dict'>` — appears in 4/6 runs.

**Root cause:** `ticker_scanner.py:236` checks `isinstance(tickers, list)` but some models return `{"tickers": ["AAPL", "TSLA"]}`. The dict case is silently dropped → zero tickers extracted from those transcripts.

**Fix:** Add dict unwrapping before the list check:

```python
if isinstance(tickers, dict):
    tickers = tickers.get("tickers", tickers.get("symbols", []))
```

---

## 🟡 Significant Issues

### 4. AllStudy NoneType Crash in Technical Computation

`technical_service.py:100` — `AllStudy failed, falling back to individual: unsupported operand type(s) for +: 'float' and 'NoneType'`

Appears in every run. The pandas-ta `AllStudy` crashes when `None` values exist in OHLCV data. The fallback to individual indicators works but may miss some. Should add `df.dropna()` or `df.fillna(method='ffill')` before running `AllStudy`.

### 5. QQQM.MX Illiquid Ticker Keeps Recurring

Every run discovers QQQM.MX → collection fails (no financial data) → deep analysis flags `illiquid` → removes from watchlist → next run discovers it again. The `rejected_symbols` table exists but isn't being checked during import.

**Fix:** After quality gate removal, also add to `rejected_symbols` with a cooldown period.

### 6. Watchlist Duplication Across Bots

BEPC appears **7 times** as `active` across different bot_ids. The same ticker analyzed redundantly by every bot. Not a correctness bug but wastes LLM calls during `run_all_bots`.

### 7. Scheduler `pre_market` Column Mismatch Error

`scheduler_runs` shows `pre_market` failed with: `Referenced column "confidence_score" not found ... Candidate bindings: "confidence"`. Column was renamed but scheduler query wasn't updated.

### 8. SEC 13F 404 on CIK0001116304

Persistent 404 in every run. This CIK should be removed from the filer list or the error demoted to debug level.

---

## 🟢 Pipeline Architecture Analysis & Improvement Opportunities

### Current Flow

```
Discovery (Reddit + YouTube + SEC 13F + Congress + RSS)
    ↓ scored tickers
Import (top tickers → watchlist)
    ↓ active watchlist
Collection (yfinance OHLCV + fundamentals → DuckDB)
    ↓ stored financial data
Deep Analysis (QuantSignalEngine + DataDistiller → TickerDossier)
    ↓ conviction scores + distilled summaries
Trading (TradingAgent: one LLM call per ticker → BUY/SELL/HOLD)
    ↓ TradeAction
Execution (ExecutionService: risk rules + paper trader)
```

### What the LLM Currently Sees (Per Ticker)

The `TradingAgent._build_prompt()` builds this context:

```
TICKER: PLTR
PRICE: $147.22  |  TODAY: +0.97%
VOLUME: 45,000,000  |  AVG VOLUME: 60,000,000

TECHNICAL ANALYSIS:
(empty — because of bug #1)

QUANT SIGNALS:
(empty — because dossier has no quant summary)

NEWS DIGEST:
(empty — because executive_summary is "Insufficient price data...")

PORTFOLIO: Cash=$5,000  |  Total=$50,000  |  Max position=15%
EXISTING POSITION: None
```

The LLM is making decisions on **price, volume, and today's change alone** — no fundamentals, no technicals, no news, no quant signals. That's why 94% of decisions are HOLD at 60%.

### Recommended Improvements (Priority Order)

#### P0 — Fix Data Pipeline (Unlocks Everything Else)

1. **Fix DataDistiller dict/object mismatch** — This single fix will populate dossiers with real technical analysis, trend detection, crossovers, support/resistance, divergences, volume profiles, and quant scores
2. **Pipe conviction + signal into TradingAgent** — Add `context["dossier_conviction"]` and `context["dossier_signal"]` from the dossier

#### P1 — Improve LLM Decision Quality

3. **Add sector/industry context** — Include sector, industry, and market cap from yfinance `info` dict
2. **Add recent news headlines** — The `news_articles` table exists in DuckDB but isn't queried by `_build_context()`
3. **Add earnings proximity** — The `earnings_calendar` table exists; flag tickers near earnings dates with a warning
4. **Add insider activity signal** — The `insider_activity` table exists; summarize net insider buying/selling

#### P2 — Improve Discovery Quality

7. **Fix YouTube dict→list unwrapping** — Recover lost tickers from transcripts
2. **Add QQQM.MX to permanent reject list** — or implement cooldown in `rejected_symbols`
3. **Add sentiment from discovery** — Currently all discovered tickers get `neutral` sentiment; use the actual sentiment from Reddit/YouTube context

#### P3 — Improve Trading Agent Prompt

10. **Follow-up questions system** — Currently the agent makes ONE call per ticker. Add a two-pass system:
    - Pass 1: Quick scan with high-level data → flag tickers worth deeper analysis
    - Pass 2: Deep dive with full dossier + news + insider data for flagged tickers only
2. **Add market regime context** — Tell the LLM if the overall market (SPY) is trending up/down/sideways, what VIX is, etc.
3. **Historical trade context** — Show the LLM past trades for this ticker (P&L history) so it learns from its own mistakes

#### P4 — Infrastructure

13. **Fix scheduler column mismatch** — Update `confidence_score` → `confidence`
2. **Clean SEC 13F filer list** — Remove dead CIKs
3. **Deduplicate watchlist across bots** — Share discovery data, separate only portfolio isolation

---

## Trading Performance Summary (Last 24h)

| Metric | Value |
|--------|-------|
| Total orders | 14 |
| BUY orders | 12 |
| SELL orders | 2 (ITT, BEPC) |
| Stop-loss triggers | 2 (ITT, LRCX auto-stop-loss) |
| Unique tickers traded | PLTR, ZS, ADI, BEPC, MA, DBX, MSFT, UBER, TKO, ITT, HON, LRCX |
| Avg conviction at trade | 0.70 |
| All executions | filled (no broker errors) |

---

## Status: Ready for Implementation

The single highest-impact fix is **bug #1 (DataDistiller dict/object mismatch)**. Once dossiers contain real data, trading decisions will improve dramatically because the LLM will see actual technical analysis instead of empty context.
