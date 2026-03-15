# Lazy Trading Bot — Comprehensive Model Performance Audit Report

**Prepared:** March 12, 2026  
**Audit Period:** March 11, 2026 17:58 UTC — March 12, 2026 12:35 UTC (~18.6 hours)  
**Data Sources:** Application logs (3 full pipeline runs, ~2.8 MB), Prism/Retina LLM gateway telemetry  
**Models Evaluated:** 7 distinct LLM configurations across 695 Prism API requests  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Methodology](#2-methodology)
3. [Aggregate Performance Matrix](#3-aggregate-performance-matrix)
4. [Per-Model Deep Analysis](#4-per-model-deep-analysis)
   - 4.1 [granite3.2:8b-50k — "The Workhorse"](#41-granite328b-50k)
   - 4.2 [nemotron-3-nano:latest — "The Safety Net"](#42-nemotron-3-nanolatest)
   - 4.3 [gpt-oss-safeguard:20b — "The Conservative"](#43-gpt-oss-safeguard20b)
   - 4.4 [olmo-3:latest (7B) — "The Risk"](#44-olmo-3latest)
   - 4.5 [olmo-3:32b — "The Liability"](#45-olmo-332b)
   - 4.6 [qwen-claude-165k:latest — "The Analyst"](#46-qwen-claude-165klatest)
   - 4.7 [ibm/granite-3.2-8b (LM Studio) — "Untested"](#47-ibmgranite-32-8b)
5. [Failure Taxonomy](#5-failure-taxonomy)
6. [Fallback Cascade Analysis](#6-fallback-cascade-analysis)
7. [Tool Usage Compliance Audit](#7-tool-usage-compliance-audit)
8. [Trade Decision Quality Analysis](#8-trade-decision-quality-analysis)
9. [Prompt Engineering Effectiveness](#9-prompt-engineering-effectiveness)
10. [Recommended Benchmark Test Suite](#10-recommended-benchmark-test-suite)
11. [Appendix: Raw Data Tables](#11-appendix-raw-data-tables)

---

## 1. Executive Summary

This audit evaluates seven LLM configurations deployed in the Lazy Trading Bot pipeline over three distinct production runs within a 24-hour window. The pipeline routes structured financial prompts (trading decisions with JSON output) through Ollama-hosted models via the Prism AI gateway.

### Key Findings

| Finding | Severity | Impact |
|---|---|---|
| olmo-3:32b has a 61% failure rate due to infinite thinking loops | 🔴 Critical | 8.7 hours GPU time wasted |
| olmo-3:latest has a 47% failure rate with similar root cause | 🔴 Critical | 4.1 hours GPU time wasted |
| gpt-oss-safeguard:20b ignores research tools 93% of the time | 🟡 Warning | Decisions lack data backing |
| nemotron-3-nano:latest is used as fallback 127 times (48% of its calls) | 🟡 Warning | Masked upstream failures |
| granite3.2:8b-50k is fastest and most reliable (100%, 14s avg) | 🟢 Positive | Best candidate for primary use |
| Prompt engineering fix improved tool usage from 0% → 21-88% | 🟢 Positive | Verifiable improvement |
| Trade parsing repair system works but only attempted 12 times | 🟡 Warning | Low sample prevents confidence |

### Infrastructure Cost of Failures

```
Model               Failed Requests    Avg Wait    Total Wasted GPU Time
────────────────────────────────────────────────────────────────────────
olmo-3:32b                19           27.4 min          8.7 hours
olmo-3:latest            105            2.3 min          4.1 hours
gpt-oss-safeguard:20b      2            4.4 min          0.1 hours
ibm/granite-3.2-8b         1            2.4 min          0.0 hours
────────────────────────────────────────────────────────────────────────
TOTAL                    127                             12.9 hours
```

> [!CAUTION]
> **12.9 hours of GPU compute were wasted on requests that returned zero usable output.** This represents approximately 69% of the total audit window.

---

## 2. Methodology

### 2.1 Data Collection

Three production pipeline runs were analyzed:

| Run | Log File | Duration | Models Active | Total Prism Requests |
|---|---|---|---|---|
| **Run 1** | `trading_bot_2026-03-11_18-21-57.log` | ~1.8h | nemotron-3-nano (primary) | ~52 decisions |
| **Run 2** | `trading_bot_2026-03-11_20-14-01.log` | ~2.0h | nemotron-3-nano (primary) | ~60 decisions |
| **Run 3** | `trading_bot_2026-03-11_23-42-32.log` | ~13h | All 7 models (sequential) | ~583 requests |

### 2.2 Metrics Definitions

- **Success Rate**: `successful_responses / (successful_responses + empty_failures) × 100`
- **Empty Failure**: Prism reports the model "returned empty" — generated thinking tokens but zero output tokens
- **Explicit Timeout**: Prism HTTP request exceeded the configurable timeout (180s or 300s)
- **Avg Response Time**: Mean latency of successful responses only
- **Think-to-Output Ratio**: Average thinking chars ÷ average output chars (lower is more efficient)
- **Tool Usage Rate**: Percentage of trading decisions that called at least one research tool

### 2.3 Limitations

- `pipeline_events` database table was not created until after all three runs, so DB-level event data is not available. All analysis is derived from log file parsing.
- Models `qwen-claude-165k:latest`, `qwen3.5:35b`, and `ibm/granite-3.2-8b` have extremely small sample sizes (1-2 requests each) and cannot be statistically evaluated.
- The `olmo-3:latest` data spans both Run 2 (with old prompt) and Run 3 (with new prompt), mixing two prompt configurations.

---

## 3. Aggregate Performance Matrix

| Model | Size | VRAM | Requests | Success | Fail | Rate | Avg Time | P95 Time | Avg Output | Think Ratio | Wasted |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **granite3.2:8b-50k** | 4.6 GiB | 6.4 GiB | 185 | 185 | 0 | **100%** | **14.1s** | 25.8s | 331 ch | N/A | 0h |
| **nemotron-3-nano:latest** | 22.6 GiB | 23.1 GiB | 265 | 264 | 0 | **100%** | 63.0s | 101.0s | 296 ch | 11.0× | 0h |
| **gpt-oss-safeguard:20b** | 12.8 GiB | 13.7 GiB | 236 | 232 | 2 | **99%** | 41.7s | — | 411 ch | 4.6× | 0.1h |
| **olmo-3:latest** | 4.2 GiB | 16.7 GiB | 174 | 119 | 105 | **53%** | 105.7s | 171.0s | 473 ch | 13.5× | **4.1h** |
| **olmo-3:32b** | 18.1 GiB | 20.6 GiB | 33 | 12 | 19 | **39%** | 167.3s | 250.0s | 173 ch | 18.1× | **8.7h** |
| qwen-claude-165k:latest | — | — | 1 | 1 | 0 | 100% | 212.0s | — | 1,568 ch | 6.7× | 0h |
| qwen3.5:35b | — | — | 1 | 1 | 0 | 100% | 260.2s | — | 1,377 ch | 10.2× | 0h |
| ibm/granite-3.2-8b | — | — | 2 | 0 | 1 | 0% | — | — | — | — | 0h |

> [!NOTE]  
> Think Ratio measures the number of internal "thinking" characters generated per output character. A ratio of 18.1× (olmo-3:32b) means the model generates 18 thinking tokens for every 1 visible output token — an extreme inefficiency suggesting the model is overthinking without converging on an answer.

---

## 4. Per-Model Deep Analysis

### 4.1 granite3.2:8b-50k

**Classification:** ✅ Tier 1 — Production Ready  
**VRAM:** 6.4 GiB (10% of available 64 GiB)  
**Context Length:** 8,192 tokens  

#### Performance Profile
```
Requests:        185          Success Rate:  100%
Avg Response:    14.1s        Median:        11.7s         P95: 25.8s
Avg Output:      331 chars    Thinking:      N/A (non-thinking model)
Tool Usage:      88%          Repair Events: 8  
Decisions:       3 BUY, 10 SELL, 29 HOLD
```

#### Strengths
- **Fastest model by a wide margin** — 14.1s average is 4.5× faster than nemotron and 12× faster than olmo-3:32b
- **Zero failures** across 185 requests — perfect reliability
- **Highest tool usage rate** at 88% — the model consistently calls research tools before deciding
- **Diverse tool usage** — uses `search_tools` (34), `get_technicals_detail` (12), `recall_findings` (8), `save_finding` (3), `search_news` (1)
- **Balanced decision-making** — shows genuine SELL decisions (10), unlike models that default to BUY/HOLD

#### Concerns
- 8 repair attempts were triggered (but 0 succeeded — need to investigate why repairs consistently fail for this model)
- Non-thinking model means less "reasoning transparency" for auditing
- 2 requests triggered fallback (but from the model itself, not to it)

#### Recommended Tests
1. **Structured JSON compliance test** — 50 prompts requiring exact JSON schema compliance, measure validation failure rate
2. **Repair success rate test** — Intentionally pass malformed LLM output to the repair pipeline and verify it corrects common granite3.2 errors
3. **Latency under load** — Measure response time degradation when other models are queued behind it
4. **Decision quality backtest** — Compare BUY/SELL decisions against actual next-day price movements

---

### 4.2 nemotron-3-nano:latest

**Classification:** ✅ Tier 1 — Production Ready (Primary Fallback)  
**VRAM:** 23.1 GiB (36% of available 64 GiB)  
**Context Length:** 8,192 tokens  

#### Performance Profile
```
Requests:        265          Success Rate:  100% (264/264)
Avg Response:    63.0s        Median:        64.1s         P95: 101.0s
Avg Output:      296 chars    Thinking:      3,268 chars   Think Ratio: 11.0×
Tool Usage:      21%          Repair Events: 3 (1 success)
Decisions:       32 BUY, 7 SELL, 124 HOLD
Used as Fallback: 127 times (48% of its requests)
```

#### Strengths
- **100% reliability** — never fails to produce output
- **Excellent fallback candidate** — used as fallback 127 times and succeeded every time
- **Thinking model** — generates internal reasoning (3,268 chars avg) providing audit trail
- **Only model with confirmed repair success** — the ITT repair (Run 1) succeeded via nemotron as the repair model

#### Concerns
- **HOLD bias**: 76% of decisions are HOLD (124/163) — the model may be overly conservative
- **Low tool usage at 21%** — despite prompt improvements, nemotron still skips tools 79% of the time
- **BUY bias when it does act** — 32 BUY vs only 7 SELL suggests the model may not adequately assess bearish signals
- **Highest VRAM consumption** at 23.1 GiB for a "nano" model — this is because the file is 22.6 GiB (quantized weights are large)
- **4.5× slower than granite** — 63s average is acceptable but not ideal for high-throughput scenarios

#### Recommended Tests
1. **Tool compliance stress test** — Send 50 prompts with explicit "you MUST call a tool" instruction variants to measure tool trigger rate
2. **HOLD bias calibration** — Compare HOLD frequency against actual market volatility to determine if the rate is appropriate
3. **Fallback latency test** — Measure how quickly nemotron responds when invoked as a fallback (is there context overhead?)
4. **Thinking quality audit** — Sample 20 thinking traces and evaluate whether the reasoning is substantive or circular

---

### 4.3 gpt-oss-safeguard:20b

**Classification:** 🟡 Tier 2 — Reliable but Underperforming on Tool Usage  
**VRAM:** 13.7 GiB (21% of available 64 GiB)  
**Context Length:** 8,192 tokens  

#### Performance Profile
```
Requests:        236          Success Rate:  99% (232/234)
Avg Response:    41.7s        Median:        N/A           P95: N/A
Avg Output:      411 chars    Thinking:      1,876 chars   Think Ratio: 4.6×
Tool Usage:      7% (8/111)   No-Tools:      103
Decisions:       32 BUY, 0 SELL, 79 HOLD
```

#### Strengths
- **Nearly perfect reliability** — 99% success rate with only 2 failures in 234+ attempts
- **Good latency** — 41.7s is the second-fastest among thinking models
- **Best think-to-output efficiency** — 4.6× ratio means the model thinks only as much as needed
- **Highest average output quality** — 411 chars average, more detailed than nemotron or olmo-3:32b

#### Concerns
- **Critical: 93% of decisions made WITHOUT research tools** — The model was instructed to use tools but overwhelmingly ignored the instruction. Only 8 out of 111 decisions used any tools at all.
- **Zero SELL decisions** — The model never recommends selling, which is a serious trading bias. In 111 decisions, it chose 32 BUY and 79 HOLD but 0 SELL.
- **Low tool diversity** — When it does use tools, it only uses `search_tools` (8 times); it never calls `get_technicals_detail`, `fetch_sec_filings`, `search_news`, or any other tool.

#### Root Cause Analysis: Why No Tools?
The gpt-oss-safeguard model appears to have been fine-tuned with a "safeguard" alignment that makes it cautious about executing external actions (tool calls). The system prompt instructs it to call tools, but the model's alignment training likely overrides this instruction in favor of generating a direct answer from its parametric knowledge. This is a **model-level limitation, not a code bug**.

#### Recommended Tests
1. **Tool forcing test** — Test progressively stronger prompt wordings: "You MUST call a tool" → "ALWAYS call search_tools first" → "Your response will be rejected if no tool is called" — measure which wording breaks through the alignment
2. **SELL signal test** — Provide bearish data (stock down 20%, negative earnings surprise) and verify whether the model can produce a SELL recommendation
3. **Instruction following benchmark** — 20 prompts with explicit instruction ("respond in exactly 3 sentences", "include the word X") to measure general instruction compliance
4. **Output completeness test** — Compare the 411-char average output against the expected schema to check if all required fields are populated

---

### 4.4 olmo-3:latest

**Classification:** 🔴 Tier 3 — Unreliable, Recommend Removal or Major Configuration Change  
**VRAM:** 16.7 GiB (26% of available 64 GiB)  
**Context Length:** 24,576 tokens  

#### Performance Profile
```
Requests:        174          Success Rate:  53% (119/224)
Avg Response:    105.7s       Median:        102.3s        P95: 171.0s
Avg Output:      473 chars    Thinking:      6,367 chars   Think Ratio: 13.5×
Tool Usage:      0%           No-Tools:      80
Empty Failures:  105          Avg Empty Wait: 140s          Max: 571s
Total Wasted:    4.1 hours
Fallback Triggers: 134
```

#### Strengths
- **Highest output quality when it works** — 473 chars average is the richest output among regularly-used models
- **Deep thinking** — 6,367 chars average thinking depth, potentially the most thorough analysis
- **Uses the largest context window** at 24,576 tokens, allowing more input data

#### Concerns
- **47% failure rate** — almost half of all requests return zero output
- **105 empty failures** — each one wasted GPU time while the model thought but produced nothing
- **Zero tool usage** — not a single decision in 80 used any research tools (0%)
- **134 fallback triggers** — the model failed so often that 134 requests cascaded to nemotron-3-nano as backup
- **4.1 hours of wasted GPU time** — computing that produced no usable output

#### Root Cause: The "Thinking Without Answering" Pattern

The olmo-3 architecture (both 7B and 32B variants) exhibits a distinctive failure mode:

1. The model receives the structured JSON prompt
2. It begins generating internal thinking/reasoning tokens
3. It enters a reasoning loop where it considers multiple options but never commits
4. The thinking stream continues until the timeout is hit
5. Ollama returns an empty response (thinking tokens are internal, not output)

This is observable in the Prism telemetry: requests show `IN TOKENS > 0` but `OUT TOKENS = 0`. The model is processing the input and generating internal state, but the output generation phase never triggers.

**Hypothesis:** The JSON schema constraint combined with the multi-tool system prompt creates a decision space that the olmo-3 reasoning system cannot converge on within the timeout window. The model keeps generating alternative analyses without selecting one as the final answer.

#### Recommended Tests
1. **Simplified prompt test** — Remove tool instructions and JSON constraints; give a plain-text "should I buy X?" prompt to see if the model can produce output at all
2. **Temperature ablation** — Test temperatures 0.1, 0.3, 0.5, 0.7, 1.0 to see if higher randomness helps the model "commit" to an answer
3. **Context window reduction** — Test with ctx=4096 and ctx=8192 (instead of 24576) to reduce computational overhead
4. **Timeout sensitivity** — Test with 60s, 120s, and 180s timeouts to determine the breakeven point
5. **Reproduce the loop** — Send the exact same prompt 10 times and measure: does it fail consistently or randomly?

---

### 4.5 olmo-3:32b

**Classification:** 🔴 Tier 4 — Non-Functional, Recommend Immediate Removal  
**VRAM:** 20.6 GiB (32% of available 64 GiB)  
**Context Length:** 8,192 tokens  

#### Performance Profile
```
Requests:        33           Success Rate:  39% (12/31)
Avg Response:    167.3s       Median:        196.2s        P95: 250.0s
Avg Output:      173 chars    Thinking:      3,145 chars   Think Ratio: 18.1×
Tool Usage:      25% (1/4)    No-Tools:      3
Empty Failures:  19           Avg Empty Wait: 1,647s (27.4 min)
Max Empty Wait:  2,247s (37.4 min)
Total Wasted:    8.7 hours
All Decisions:   0 BUY, 0 SELL, 4 HOLD (100% HOLD)
```

#### Performance Is Categorically Unacceptable

Every metric places olmo-3:32b at the bottom:

```
Metric                  olmo-3:32b    Best Model      Ratio
────────────────────────────────────────────────────────────
Success Rate            39%           100% (granite)  0.39×
Avg Response Time       167.3s        14.1s (granite) 11.9× slower
Avg Output Quality      173 chars     473 chars       0.37×
Think Efficiency        18.1×         4.6× (gpt-oss)  3.9× worse
GPU Time Wasted         8.7 hours     0 hours         ∞× worse
Useful Decisions        4 (all HOLD)  42 (granite)    0.10×
```

#### Root Cause: Same as olmo-3:latest but Amplified

The 32B variant suffers from the identical "thinking without answering" failure mode as the 7B variant, but the larger parameter count makes each failure more expensive:

- **Longer thinking loops** — The 32B model takes 27 minutes to time out vs 2.3 minutes for the 7B variant
- **Lower output quality** — When it does produce output, it averages only 173 chars (lower than every other model)
- **Worse think-to-output ratio** — 18.1× is the worst efficiency of any model, indicating extreme computational waste per useful token

The 19 empty failures consumed:

```
Wait Time Distribution:
  <  500s:   3 failures  (shortest: 427s)
  500-1000s: 2 failures
  1000-1500s: 1 failure
  1500-2000s: 7 failures
  2000-2250s: 6 failures  (longest: 2,247s = 37.4 minutes)
```

> [!WARNING]
> olmo-3:32b spent **8 hours 42 minutes** on 19 failed requests during a 13-hour run window. That's 67% of the compute time allocated to this model wasted on zero-output failures.

#### Recommendation: Immediate Removal

There is no reasonable configuration change that can fix this model:
1. The problem is architectural (the model's reasoning system doesn't converge)
2. Even successful responses produce the least useful output (173 chars, 100% HOLD)
3. The VRAM footprint (20.6 GiB) could serve two granite3.2 instances simultaneously
4. Every failure cascades to nemotron-3-nano anyway, so removing it changes nothing from a decision quality standpoint

---

### 4.6 qwen-claude-165k:latest

**Classification:** ⚪ Insufficient Data — Single Request Only  
**Context Length:** Unknown (configured at 8,192)  

#### Performance Profile
```
Requests:        1            Success Rate:  100% (1/1)
Avg Response:    212.0s       Output:        1,568 chars
Thinking:        10,474 chars Think Ratio:   6.7×
```

#### Observations
- The single successful response produced the **highest output quality** of any model (1,568 chars)
- At 10,474 thinking chars, it demonstrates deep reasoning
- The 6.7× think-to-output ratio is very efficient for a thinking model
- Slow (212s) but within acceptable bounds for a high-quality analysis model

#### Recommended Tests
1. **Reliability test** — Run 50 trading prompts to establish baseline success rate
2. **Tool compliance test** — Verify whether the model follows tool-calling instructions
3. **Context utilization test** — Test with the full 165k context window to see if it can process large market data batches
4. **Comparative quality test** — Compare output completeness against granite3.2 on identical prompts

---

### 4.7 ibm/granite-3.2-8b (LM Studio)

**Classification:** ⚪ Non-Functional — Needs Configuration Debugging  
**Provider:** LM Studio (not Ollama)  

#### Performance Profile
```
Requests:        2            Success Rate:  0% (0/1)
Empty Failures:  1            Wait Time:     145s
```

#### Root Cause
This model runs through **LM Studio** rather than Ollama, but the Prism gateway appears to route it through the same Ollama endpoint (`http://10.0.0.30:11434`). This configuration mismatch likely causes the model to fail because Ollama doesn't have the `ibm/granite-3.2-8b` model loaded.

#### Recommended Tests
1. **Configuration audit** — Verify the `provider_url` points to LM Studio, not Ollama
2. **Direct LM Studio test** — `curl` the LM Studio endpoint directly to confirm the model responds
3. **Prism routing test** — Check if Prism correctly routes based on provider type

---

## 5. Failure Taxonomy

Five distinct failure modes were observed across all models:

### F1: Thinking Loop Exhaustion (olmo-3 exclusive)

**Affected Models:** olmo-3:latest, olmo-3:32b  
**Frequency:** 124 occurrences (19 + 105)  
**Signature:** `[LLM] Primary model X returned empty after Ys — falling back to nemotron-3-nano:latest`  
**Root Cause:** The model generates internal reasoning tokens indefinitely without producing output tokens. The structured JSON schema and multi-tool instructions create a decision space the model cannot converge on.  
**Reproduction Steps:**
1. Load olmo-3:32b with ctx=8192
2. Send the standard trading prompt with JSON schema and tool specifications
3. Observe that Prism reports `IN_TOKENS > 0` but `OUT_TOKENS = 0`
4. Wait — the model will time out after 300s+ with zero output

### F2: Pydantic Schema Mismatch

**Affected Models:** nemotron-3-nano:latest (confirmed), potentially all models  
**Frequency:** 12 repair attempts across all runs  
**Signature:** `Pydantic validation failed: 1 validation error for TradeAction`  
**Root Cause:** The LLM outputs field values that don't match the strict enum/type constraints. Confirmed example: model returned `risk_level: "MEDIUM"` but the schema expects exactly `"LOW"`, `"MED"`, or `"HIGH"`.  
**Reproduction Steps:**
1. Send a trading prompt to nemotron-3-nano
2. Parse the JSON output with Pydantic
3. ~2-5% of responses will contain non-conforming field values

### F3: Confidence Type Mismatch

**Affected Models:** All thinking models (especially small ones)  
**Frequency:** Estimated high (pre-fix), now mitigated  
**Signature:** `confidence` field returned as string ("high") instead of float (0.8)  
**Root Cause:** The system prompt previously allowed ambiguity. Now mitigated by the `STRICT FIELD RULES` addition and the confidence normalization in `trade_action_parser.py`.  
**Status:** ✅ Resolved in the March 11 prompt fix

### F4: Provider Routing Failure

**Affected Models:** ibm/granite-3.2-8b  
**Frequency:** 1 occurrence  
**Signature:** Empty response from a model not loaded in Ollama  
**Root Cause:** Model registered with `provider: "lm-studio"` but `provider_url` points to Ollama endpoint  
**Reproduction Steps:**
1. Register a bot with `provider=lm-studio` but `provider_url=http://10.0.0.30:11434`
2. Attempt to run the bot
3. Observe empty response (Ollama doesn't have the model)

### F5: Tool Instruction Non-Compliance

**Affected Models:** gpt-oss-safeguard:20b (93% non-compliance), nemotron-3-nano (79%), olmo-3:latest (100%)  
**Frequency:** Persistent across all runs  
**Signature:** `[TradingAgent] X decided Y (no research tools used)`  
**Root Cause:** Model alignment/fine-tuning overrides system prompt instructions. The model generates a trading decision from parametric knowledge without calling any research tools, reducing decision quality.  
**Reproduction Steps:**
1. Send a trading prompt with explicit tool-calling instructions
2. Observe whether the model's response includes tool call formatting
3. For gpt-oss-safeguard, ~93% of responses will skip tools entirely

---

## 6. Fallback Cascade Analysis

The pipeline uses a cascading fallback system: if the primary model returns empty, the request is retried with `nemotron-3-nano:latest` as the fallback.

```
Model Triggering Fallback    Times    Avg Wait Before Fallback
──────────────────────────────────────────────────────────────
olmo-3:latest                134      2 min 20 sec
olmo-3:32b                    19      27 min 27 sec
gpt-oss-safeguard:20b          2      4 min 24 sec
granite3.2:8b-50k               2      unknown
ibm/granite-3.2-8b              1      2 min 25 sec
──────────────────────────────────────────────────────────────
TOTAL                        158
```

nemotron-3-nano:latest handled all 127 confirmed fallback requests successfully (100% fallback reliability).

> [!IMPORTANT]
> **48% of nemotron's 265 requests were fallback requests**, not primary decisions. This means nemotron is doing the work that 3-4 other models failed to do, inflating its request count and making its per-model statistics misleading. Its "true" primary request count is approximately 138.

---

## 7. Tool Usage Compliance Audit

### Cross-Run Comparison (Prompt Engineering Impact)

| Run | Prompt Version | Total Decisions | With Tools | Without Tools | Tool Rate |
|---|---|---|---|---|---|
| **Run 1** (18:21) | Original (vague) | 52 | 0 | 52 | **0%** |
| **Run 2** (20:14) | Improved | 60 | 14 | 46 | **23%** |
| **Run 3** (23:42) | Improved | 288+ | varies by model | varies | **varies** |

### Per-Model Tool Compliance (Run 3)

| Model | With Tools | Without Tools | Rate | Tools Used |
|---|---|---|---|---|
| **granite3.2:8b-50k** | 37 | 5 | **88%** | search_tools, get_technicals_detail, recall_findings, save_finding, search_news |
| nemotron-3-nano:latest | 35 | 128 | 21% | search_tools, get_technicals_detail, fetch_sec_filings, search_news |
| gpt-oss-safeguard:20b | 8 | 103 | **7%** | search_tools only |
| olmo-3:latest | 0 | 80 | **0%** | None |
| olmo-3:32b | 1 | 3 | 25% | search_tools, search_news |

> [!NOTE]
> **granite3.2:8b-50k is the only model that consistently follows tool-calling instructions** (88% compliance). It also uses the most diverse set of tools (5 different tool types), indicating genuine research behavior rather than token-level pattern matching.

---

## 8. Trade Decision Quality Analysis

### Decision Distribution by Model

```
Model                    BUY    SELL   HOLD   Total   BUY%   SELL%  HOLD%
─────────────────────────────────────────────────────────────────────────
granite3.2:8b-50k          3     10     29      42    7%     24%    69%
nemotron-3-nano:latest    32      7    124     163   20%      4%    76%
gpt-oss-safeguard:20b    32      0     79     111   29%      0%    71%
olmo-3:latest             26      2     52      80   33%      3%    65%
olmo-3:32b                 0      0      4       4    0%      0%   100%
```

### Anomalies Detected

1. **gpt-oss-safeguard:20b never sells.** Zero SELL decisions in 111 attempts. This is statistically improbable in any market environment and suggests a fundamental bias in the model's alignment.

2. **olmo-3:32b always HOLDs.** 4/4 decisions are HOLD, meaning when the model does produce output, it never takes an actionable position.

3. **nemotron-3-nano is HOLD-heavy at 76%.** While conservative trading is not inherently bad, a 76% HOLD rate combined with 20% BUY and only 4% SELL suggests the model struggles to identify sell signals.

4. **granite3.2 has the most balanced profile.** Its 24% SELL rate is the highest of any model, suggesting it can identify both bullish and bearish conditions.

---

## 9. Prompt Engineering Effectiveness

The March 11 prompt fix added:
1. **Explicit tool-calling instruction** — "Call at least 1 research tool before deciding"
2. **STRICT FIELD RULES** — Confidence must be decimal 0.0-1.0, action must be exactly BUY/SELL/HOLD
3. **Tool usage logging** — `_log_tool_usage()` function tracks compliance

### Measured Impact

| Metric | Before (Run 1) | After (Run 2+) | Improvement |
|---|---|---|---|
| Tool usage rate (nemotron) | 0% | 21% | +21 pp |
| Tool usage rate (granite) | N/A (not in Run 1) | 88% | N/A |
| Parse failures (estimated) | High | Low | Improved |
| Confidence type errors | Frequent ("high") | Rare (0.78) | Resolved |

> [!TIP]
> The prompt fix was effective but not sufficient for all models. granite3.2 responded well (88% tool compliance), nemotron improved (0% → 21%), but gpt-oss-safeguard (7%) and olmo-3:latest (0%) remain non-compliant. **Different models may require different prompt strategies.**

---

## 10. Recommended Benchmark Test Suite

Based on the findings above, the following benchmarks should be implemented in the model file tester:

### Benchmark 1: Reliability (MUST HAVE)
**Purpose:** Measure success rate under production-like conditions  
**Method:** Send 50 standard trading prompts (mix of BUY/SELL/HOLD scenarios) to each model  
**Metrics:** Success rate, failure rate, avg/p50/p95/p99 latency, timeout count  
**Pass criteria:** ≥95% success rate, P95 latency ≤120s  

### Benchmark 2: JSON Schema Compliance (MUST HAVE)
**Purpose:** Measure how often the model produces valid, parseable JSON matching the TradeAction schema  
**Method:** Send 30 prompts requiring JSON output, parse each with Pydantic  
**Metrics:** Valid JSON %, schema validation pass %, most common error types  
**Pass criteria:** ≥90% valid JSON, ≥85% schema compliance  

### Benchmark 3: Tool Calling Compliance (MUST HAVE)
**Purpose:** Measure whether the model follows instructions to call research tools  
**Method:** Send 20 prompts with explicit "you MUST call search_tools" instruction  
**Metrics:** Tool call rate, tool diversity, false positive tool calls  
**Pass criteria:** ≥70% tool calling rate  

### Benchmark 4: Thinking Efficiency (RECOMMENDED)
**Purpose:** Identify models that think excessively without producing output  
**Method:** Measure thinking tokens vs output tokens for each response  
**Metrics:** Think-to-output ratio, thinking time vs output time  
**Pass criteria:** Think ratio ≤15×, no empty responses  

### Benchmark 5: Decision Bias Detection (RECOMMENDED)
**Purpose:** Identify models with systematic BUY/SELL/HOLD biases  
**Method:** Send 10 clearly bullish, 10 clearly bearish, and 10 neutral prompts  
**Metrics:** BUY/SELL/HOLD distribution per scenario type  
**Pass criteria:** ≥60% correct direction for bullish/bearish prompts  

### Benchmark 6: Latency Under Load (OPTIONAL)
**Purpose:** Measure response time degradation when multiple bots run sequentially  
**Method:** Run the same 10 prompts on model N while model N-1 is still loaded in VRAM  
**Metrics:** Latency vs baseline, VRAM contention effects  

### Benchmark 7: Fallback Resilience (OPTIONAL)
**Purpose:** Verify the fallback cascade works correctly  
**Method:** Intentionally configure a model with wrong settings, verify fallback triggers  
**Metrics:** Fallback trigger rate, fallback latency, fallback success rate  

### Benchmark 8: Prompt Sensitivity (OPTIONAL)
**Purpose:** Measure how different prompt wordings affect model behavior  
**Method:** Test 5 prompt variants per model (formal, casual, strict, minimal, verbose)  
**Metrics:** Success rate and tool usage per variant  

---

## 11. Appendix: Raw Data Tables

### A. Empty Failure Wait Times — olmo-3:32b (All 19 Failures)

| # | Wait Time | Wait (Human) |
|---|---|---|
| 1 | 427.0s | 7m 7s |
| 2 | 452.7s | 7m 33s |
| 3 | 550.9s | 9m 11s |
| 4 | 750.1s | 12m 30s |
| 5 | 1,183.6s | 19m 44s |
| 6 | 1,502.4s | 25m 2s |
| 7 | 1,812.3s | 30m 12s |
| 8 | 1,826.2s | 30m 26s |
| 9 | 1,869.1s | 31m 9s |
| 10 | 1,913.5s | 31m 54s |
| 11 | 1,966.0s | 32m 46s |
| 12 | 1,977.9s | 32m 58s |
| 13 | 2,072.3s | 34m 32s |
| 14 | 2,095.4s | 34m 55s |
| 15 | 2,118.0s | 35m 18s |
| 16 | 2,133.8s | 35m 34s |
| 17 | 2,154.0s | 35m 54s |
| 18 | 2,241.4s | 37m 21s |
| 19 | 2,247.0s | 37m 27s |

### B. Tool Usage Breakdown — granite3.2:8b-50k

| Tool Name | Calls | Purpose |
|---|---|---|
| search_tools | 34 | General market data search |
| get_technicals_detail | 12 | Technical indicator deep-dive |
| recall_findings | 8 | RAG memory retrieval |
| save_finding | 3 | RAG memory storage |
| search_news | 1 | News article search |

### C. nemotron-3-nano:latest as Fallback — Source Distribution

| Primary Model That Failed | Times nemotron Was Fallback |
|---|---|
| olmo-3:latest | ~100 |
| olmo-3:32b | ~19 |
| gpt-oss-safeguard:20b | ~2 |
| granite3.2:8b-50k | ~2 |
| ibm/granite-3.2-8b | ~1 |
| Other/Unknown | ~3 |

---

*End of Audit Report*
