# Pipeline Audit Plan — 2026-03-04

> Audit of runs from **2026-03-03 20:48** through **2026-03-04 04:00** across 2 full logs, 4 health reports, and 1 strategist audit.

---

## Issue 1 — CRITICAL: Massive LLM Timeout Rate (olmo-3:32b)

**Evidence:**

- `health_2026-03-04_032553.md`: **18/63 calls timed out** (28%), pipeline took **124 min** (target: 30 min)
- `health_2026-03-04_040029.md`: **16/27 calls timed out** (59%), pipeline took **60 min**
- `health_2026-03-04_012113.md`: **5/39 calls timed out** (13%), pipeline took **45 min**
- olmo-3 run at 20:48: ITT, UBER, MELI all timed out during data-only collection phase (180s+ each)

**Root cause:** `olmo-3:32b` generates tokens too slowly on the Jetson Orin AGX. Despite having 64GB VRAM headroom (~30GB free after model load), the 32B model's compute throughput is the bottleneck — thinking chains of 5,000-9,000 chars exhaust the 180s timeout. Parallel LLM requests during collection compound the issue.

**Fix:**

- [ ] **Reduce olmo-3 context window** to 32768 or switch to a smaller model for the collection/distillation LLM calls
- [ ] **Add timeout retry with context truncation** — if an LLM call hits 120s, cancel and retry with 50% context
- [ ] **Cap concurrent LLM calls** during collection phase — logs show 3 parallel Ollama requests firing simultaneously (lines 459-461 of the 22:15 log), which compounds VRAM pressure

---

## Issue 2 — HIGH: BUY Bias in nemotron-3-nano (Everything is a BUY)

**Evidence (22:15 log, trading phase starting line 624):**

- WWD → BUY 0.85, NYT → BUY 0.92, GEV → BUY 0.92, CB → BUY 0.85, DVA → BUY 0.85, ADI → BUY 0.85, PFE → BUY 0.78, TKO → BUY 0.85, BEPC → BUY 0.68, ITT → BUY 0.85, HON → BUY 0.78, KO → BUY 0.85
- **12 consecutive BUYs** before the first HOLD (GOOGL at 0.60)
- Some of these stocks have `bankruptcy_risk_high` or `drawdown_exceeds_20pct` flags — the LLM is ignoring quant warnings

**Root cause:** System prompt is too weak on grounding, or the dossier data is missing critical risk signals (see Issue 4).

**Fix:**

- [ ] **Add explicit anti-BUY guardrails** to the system prompt: "If bankruptcy_risk_high or drawdown_exceeds_20pct flags are present, your confidence MUST be below 0.60"
- [ ] **Log the full prompt** sent to the LLM for at least 1 BUY decision per cycle so we can audit what context it actually sees
- [ ] **Consider ensemble check**: if quant conviction < 0.40 but LLM says BUY > 0.80, flag as potential hallucination

---

## Issue 3 — HIGH: Strategist Runs Out of Cash, Wastes All Turns

**Evidence (`strategist_audit_2026-03-02_113000.md`):**

- Cash = $13.27, portfolio = $100k, but the strategist tried to buy WWD ($400), GOOG ($307), HON ($247), KO ($80) — **all rejected with "max safe qty is 0"**
- Used **10/10 turns** and placed **0 orders**
- Pattern: get_dossier → place_buy(rejected) → get_dossier → place_buy(rejected) → repeat

**Root cause:** The strategist doesn't check available cash before attempting to buy. The `get_market_overview` tool returns candidates but doesn't surface cash position prominently enough.

**Fix:**

- [ ] **Inject cash position into system prompt header** — "Available cash: $X.XX" should be the first line the LLM sees
- [ ] **Pre-filter candidates by affordability** — don't show tickers the bot can't afford in `get_market_overview`
- [ ] **Early termination** — if 3 consecutive buy attempts fail with "max safe qty is 0", stop the loop and log "insufficient_cash"

---

## Issue 4 — HIGH: 34/34 Tickers Missing Dossier Data

**Evidence (`strategist_audit_2026-03-02_113000.md` lines 10-47):**

- Every single ticker is missing: `executive_summary`, `bull_case`, `bear_case`, `key_catalysts`, `conviction_score`, `industry`, `market_cap_tier`, scorecard fields
- All 34 tickers are stuck at conviction=0.50 ("dead zone")

**Root cause:** The dossier generated during DeepAnalysis Layer 2 is not populating these fields. The `executive_summary` shows "Insufficient price data for pattern analysis" despite the pipeline storing 251+ price rows for each ticker. The distilled text is being stuffed into the wrong fields.

**Fix:**

- [ ] **Audit `DataDistiller.distill()`** — verify the output maps to the correct dossier fields (`executive_summary`, `bull_case`, `bear_case`)
- [ ] **Add test**: after a full collection run for one ticker, query the dossier and assert these fields are non-empty
- [ ] **Check if the "Insufficient price data" message** comes from a threshold check that's miscalibrated (e.g., requiring 500 rows when only 251 are available)

---

## Issue 5 — MEDIUM: YouTube Transcript Collection 0% Success (Discovery Phase)

**Evidence (22:15 log lines 189-284):**

- Discovery tried to collect transcripts for 10 tickers: NEXT, READ, AMD, ZTS, GRAB, FOLD, NOW, VEEV, KVUE, PFE
- Result: **0/10 tickers got transcripts**
- Pattern: yt-dlp finds videos → "All recent YouTube videos for $X already collected" → "no transcript found"

**Root cause:** Videos ARE found and confirmed as already-collected, but the transcripts aren't being returned because the DB entries for these videos may not actually have transcript text. The `already collected` check passes but the stored rows have empty transcript columns.

**Fix:**

- [ ] **Add transcript_text IS NOT NULL check** to the "already collected" logic — don't consider a video collected if its transcript is empty
- [ ] **Add metric to health report**: "Transcripts available: X/Y" to track this gap

---

## Issue 6 — MEDIUM: QQQM.MX and CNBC Are Junk Tickers Leaking Through

**Evidence:**

- `QQQM.MX`: AllStudy crashes with `unsupported operand type(s) for +: 'float' and 'NoneType'`, risk metrics all `NaN`, no financials, eventually removed by `illiquid` gate (22:15 log line 619)
- `CNBC`: No price data, no financials, no balance sheet — flagged in health report but still enters the pipeline (health_2026-03-04_040029.md line 76-79, 103)

**Root cause:** These tickers pass the initial validation (CNBC is a real symbol for a closed-end fund, QQQM.MX is a Mexican-listed ETF) but have no usable data.

**Fix:**

- [ ] **Add CNBC to exclusion list** — it's not a stock, it's being picked up from RSS feeds because of the news network name
- [ ] **Add `.MX` suffix filter** — reject all Mexican-exchange tickers unless explicitly configured
- [ ] **Fail-fast in collection phase**: if price history returns 0 rows, skip ALL downstream steps for that ticker immediately

---

## Issue 7 — MEDIUM: Pershing Square 404 (Recurring)

**Evidence:** Every single run logs: `[SEC 13F] Submissions https://data.sec.gov/submissions/CIK0001116304.json returned 404`

**Fix:**

- [ ] **Remove Pershing Square from the 13F filers list** or update the CIK number. This is a persistent 404 that wastes time and log noise.

---

## Issue 8 — LOW: olmo-3 "Ollama request FAILED" with Empty Error Messages

**Evidence (20:48 log):**

- Lines 543, 592, 641: `Ollama request FAILED -> 49.6s:` (empty error message)
- These are NOT timeouts — they complete in 39-49s but return an error

**Fix:**

- [ ] **Log the full error body** from the Ollama response when status != 200, not just the status text
- [ ] **Differentiate FAILED vs TIMEOUT** in health reports — currently they're lumped together

---

## Issue 9 — LOW: Nemotron Trading Phase Too Slow (Sequential Processing)

**Evidence (22:15 log):**

- 45 tickers processed **sequentially** at ~30s each = **22+ minutes** just for trading decisions
- The entire trading phase for the 11:45 run (15812 lines): 1496.8s = **25 min**

**Fix:**

- [ ] **Batch tickers** — process 2-3 trading decisions in parallel (nemotron has 131K ctx and doesn't timeout like olmo-3)
- [ ] **Priority ordering** — process BUY-signal tickers first, HOLD/SELL-signal tickers last, with early termination if daily trade limit is reached

---

## Issue 10 — LOW: Duplicate Bot Runs on Same Data

**Evidence:**

- olmo-3 run at 20:48 and nemotron run at 22:15 both scan the same Reddit threads, same 13F data, same Congress data
- Discovery returns nearly identical ticker lists across both runs

**Fix:**

- [ ] **Share discovery results** across bots in the same RunAll batch — run discovery once, then let each bot do its own analysis/trading
- [ ] **Mark data as "collected today"** to avoid re-scraping within the same RunAll

---

## Priority Order

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | LLM Timeout Rate | CRITICAL | Medium — config + retry logic |
| 2 | BUY Bias (nemotron) | HIGH | Low — prompt tuning |
| 3 | Strategist Cash Check | HIGH | Low — prompt + pre-filter |
| 4 | Missing Dossier Data | HIGH | Medium — debug DataDistiller |
| 5 | YouTube 0% Transcripts | MEDIUM | Low — SQL check fix |
| 6 | Junk Tickers (CNBC/QQQM.MX) | MEDIUM | Low — exclusion list + filter |
| 7 | Pershing Square 404 | MEDIUM | Trivial — remove from list |
| 8 | Empty Error Messages | LOW | Low — logging improvement |
| 9 | Sequential Trading Phase | LOW | Medium — parallelization |
| 10 | Duplicate Discovery Across Bots | LOW | Medium — shared discovery |
