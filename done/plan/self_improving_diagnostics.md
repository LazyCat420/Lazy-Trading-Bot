# Self-Improving Diagnostics System

The trading bot already collects rich data across 6 services but it's scattered across DuckDB tables, Markdown reports, and disk artifacts. This plan builds an **Improvement Feed** — a single structured report that aggregates everything into an AI-readable diagnostic so you can hand it directly to me and I know exactly what to fix.

## Existing Data Infrastructure

| Service | Data | Storage |
|---------|------|---------|
| `LLMAuditLogger` | Every LLM prompt/response/timing | `llm_audit_logs` table |
| `DecisionLogger` | Every trade decision + execution | `trade_decisions` + `trade_executions` |
| `CrossBotAuditor` | Model-vs-model audit scores | `bot_audit_reports` table |
| `StrategistAudit` | Turn-by-turn strategist actions | `reports/strategist_audit_*.md` |
| `HealthTracker` | Phase timing, LLM errors/warnings | `reports/health_*.md` |
| `ArtifactLogger` | Raw context/prompt/response per ticker | `data/artifacts/` |

> [!IMPORTANT]
> **No new data collection needed.** We're building an *aggregation layer* on top of what already exists.

## Proposed Changes

### Improvement Feed Service

#### [NEW] [ImprovementFeed.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/ImprovementFeed.py)

A service that queries all 6 data sources and produces a structured report with these sections:

1. **Pipeline Errors & Failures** — What broke and needs code fixes
   - Source: `HealthTracker` errors/warnings, `llm_audit_logs` failures
   - Output: Categorized error list with frequency counts and stack traces

2. **LLM Quality Scorecard** — Are models producing good output?
   - Source: `llm_audit_logs` (JSON parse failures, timeouts, token usage)
   - Output: Success rate per agent_step, avg tokens, avg latency

3. **Cross-Model Consistency Check** — Trading model vs audit model
   - Source: `CrossBotAuditor` results + `trade_decisions`
   - Output: Where the trading bot and its auditor disagree, and who was right

4. **Trade Decision Accuracy** — Are trades actually making money?
   - Source: `trade_decisions` + `trade_executions` + portfolio P&L
   - Output: Win rate, avg gain/loss, confidence calibration (high confidence = high accuracy?)

5. **Data Completeness Gaps** — What data is missing?
   - Source: `StrategistAudit` candidate gaps
   - Output: Most common missing fields, which data collectors need fixing

6. **Improvement Priority Queue** — Rank-ordered list of what to fix next
   - Synthesized from all above sections
   - Each item tagged with severity (critical/high/medium/low)
   - Each item includes the specific file/function that needs changing

---

### Report Format

#### [NEW] `reports/improvement_feed_YYYY-MM-DD_HHMMSS.md`

The feed is a Markdown file structured for AI consumption:

```markdown
# Improvement Feed — 2026-03-13 17:30:00

## Priority Queue (What To Fix Next)

| # | Severity | Category | Issue | Fix Location |
|---|----------|----------|-------|-------------|
| 1 | CRITICAL | Pipeline | Extraction phase timing out 40% of runs | `discovery_service.py:extract_tickers()` |
| 2 | HIGH | LLM Quality | JSON parse failures in trading step (15%) | `portfolio_strategist.py` prompt |
| 3 | HIGH | Data Gap | 60% of tickers missing `key_catalysts` | `data_distiller.py:build_dossier()` |
| 4 | MEDIUM | Accuracy | Confidence calibration off — high conf trades losing | `trading_agent.py` threshold logic |

## Section 1: Pipeline Errors (last N cycles)
...
## Section 2: LLM Quality Scorecard
...
## Section 3: Cross-Model Consistency
...
## Section 4: Trade Decision Accuracy
...
## Section 5: Data Completeness Gaps
...
## Section 6: Benchmark Statistics
...
```

---

### Pipeline Integration

#### [MODIFY] [autonomous_loop.py](file:///home/braindead/github/Lazy-Trading-Bot/app/services/autonomous_loop.py)

After each full pipeline cycle completes:
1. Generate the improvement feed report
2. Keep the latest 10 reports (auto-prune older ones)

```python
# At end of run_full_loop():
from app.services.ImprovementFeed import ImprovementFeed
feed = ImprovementFeed()
feed.generate_report(lookback_hours=24)
```

---

### API Endpoint

#### [MODIFY] [server.py](file:///home/braindead/github/Lazy-Trading-Bot/server.py)

Add `GET /api/improvement-feed` endpoint that returns the latest feed report:
- Returns the latest `improvement_feed_*.md` file content
- Optionally accepts `?regenerate=true` to produce a fresh one on demand

---

### Benchmark Statistics Tracking

#### [NEW] `benchmark_stats` DB table

Tracks per-cycle statistics for trend analysis:

| Column | Type | Description |
|--------|------|-------------|
| cycle_id | VARCHAR | Trading cycle ID |
| timestamp | TIMESTAMP | When the cycle ran |
| json_parse_success_rate | FLOAT | % of LLM calls that produced valid JSON |
| trade_accuracy | FLOAT | % of trades that were profitable |
| avg_llm_latency_ms | INTEGER | Average LLM response time |
| data_completeness | FLOAT | % of dossier fields filled |
| cross_audit_score | FLOAT | Average cross-bot audit score |
| total_errors | INTEGER | Number of pipeline errors |
| total_warnings | INTEGER | Number of pipeline warnings |

This allows the improvement feed to show **trends** (getting better/worse).

---

## Verification Plan

### Automated Tests
```bash
cd /home/braindead/github/Lazy-Trading-Bot && source venv/bin/activate
python -m pytest tests/test_improvement_feed.py -v
```
- Test query methods with empty DB (graceful handling)
- Test report generation format
- Test priority ranking logic
- Test benchmark stats persistence

### Manual Verification
1. Run a pipeline cycle
2. Check `reports/improvement_feed_*.md` was generated
3. Read the report and verify it correctly identifies known issues
4. Hit `GET /api/improvement-feed` and verify it returns the report
