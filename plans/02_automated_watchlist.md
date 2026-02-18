# Phase 2 — Automated Watchlist & Deep Analysis Pipeline

> **Goal**: Replace the static `watchlist.json` with an intelligent, auto-managed
> watchlist. For each ticker, run a **4-layer analysis funnel** that generates
> quant signal scores, uses LLMs to ask follow-up questions about the data,
> answers those questions via RAG/search, and synthesizes everything into a
> compact decision dossier — all before the Trading Engine (Phase 3) touches it.

---

## How Phase 2 Connects to Phase 1 Data

Phase 1 collects raw data across **12 steps** and stores it in DuckDB. Phase 2
reads this data, enriches it with quantitative analysis, and produces a compact
**Ticker Dossier** that Phase 3 consumes for trading decisions.

```
Phase 1 (Already Built — 12 Data Steps)
──────────────────────────────────────────
├─ price_history       → DuckDB `price_history` (1y daily OHLCV)
├─ fundamentals        → DuckDB `fundamentals`  (24 .info metrics)
├─ financial_history   → DuckDB `financials`    (multi-year income stmt)
├─ balance_sheet       → DuckDB `balance_sheet` (multi-year)
├─ cashflow            → DuckDB `cash_flows`    (multi-year)
├─ analyst_data        → DuckDB `analyst_data`  (targets + recs)
├─ insider_activity    → DuckDB `insider_activity` (transactions + inst %)
├─ earnings_calendar   → DuckDB `earnings_calendar` (next date + surprise)
├─ technicals          → DuckDB `technicals`    (154 pandas-ta indicators)
├─ risk_metrics        → DuckDB `risk_metrics`  (25+ quant computed)
├─ news                → DuckDB `news`          (yfinance + Google + SEC)
└─ youtube_transcripts → DuckDB `youtube_transcripts` (full transcripts)

Phase 2 (This Plan — 4-Layer Funnel)
──────────────────────────────────────────
  Layer 1: Quant Signal Engine     ← reads Phase 1 numeric data
  Layer 2: LLM Question Generator  ← reads Layer 1 outputs
  Layer 3: RAG Answer Engine        ← searches Phase 1 text data + web
  Layer 4: Dossier Synthesizer      ← compresses everything → 8K token dossier

Phase 3 (Trading Engine — Consumes Dossier)
──────────────────────────────────────────
  SignalRouter reads the dossier → BUY/SELL/HOLD → Order Manager
```

### What Phase 1 Already Collects (yfinance audit)

The `YFinanceCollector` already has **8 methods** fully implemented:

| Method | Data | Status |
|--------|------|--------|
| `collect_price_history()` | 1yr daily OHLCV candles | ✅ Built |
| `collect_fundamentals()` | 24 metrics from `.info` (PE, EPS, market cap, etc.) | ✅ Built |
| `collect_financial_history()` | Multi-year income statement | ✅ Built |
| `collect_balance_sheet()` | Multi-year balance sheet | ✅ Built |
| `collect_cashflow()` | Multi-year cash flow statement | ✅ Built |
| `collect_analyst_data()` | Price targets + recommendation counts | ✅ Built |
| `collect_insider_activity()` | Insider transactions + institutional % | ✅ Built |
| `collect_earnings_calendar()` | Next earnings date + historical surprise | ✅ Built |

Additionally, `TechnicalComputer` computes 154 pandas-ta indicators, and
`RiskComputer` computes 25+ quantitative risk metrics (Sharpe, Sortino, VaR, etc.).

> [!NOTE]
> Phase 1 already captures all available yfinance data. No new yfinance
> methods are needed. Phase 2 focuses on **analyzing** this data more deeply.

---

## 2.1 — The 4-Layer Analysis Funnel

The key insight: raw data alone isn't enough. We need to **ask questions** about
the data, **find answers**, and **synthesize** everything into a context-window-
friendly dossier. This is the pipeline that turns 200K+ chars of raw data into
an 8K-10K token dossier the LLM can actually reason over.

```
                    ┌─────────────────────────┐
                    │  Phase 1 Raw Data        │
                    │  (~200K chars per ticker) │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  LAYER 1: Quant Signals  │  Pure math, no LLM
                    │  Z-Score, %B, Sortino,   │  → Structured scores
                    │  Calmar, VaR, Kelly      │  → Flag anomalies
                    │  (~2K chars output)       │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  LAYER 2: LLM Questions  │  One LLM call
                    │  "Based on these scores, │  → 5-8 follow-up questions
                    │  what should we dig into?"│  (~1K chars output)
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  LAYER 3: RAG Answers    │  Search Phase 1 text data
                    │  Search transcripts,      │  → Concise answers
                    │  news, filings for each   │  (~3K chars output)
                    │  question                 │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  LAYER 4: Dossier        │  One final LLM call
                    │  Synthesize everything    │  → 8K-10K token dossier
                    │  into decision-ready      │  → Stored in DuckDB
                    │  format                   │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  WATCHLIST DECISION       │
                    │  Auto-add / Auto-remove   │
                    │  based on dossier scores  │
                    └─────────────────────────┘
```

---

## 2.2 — Layer 1: Quant Signal Engine

### Purpose

Run every ticker through a battery of **quantitative functions** — pure math,
zero LLM calls, extremely fast. The output is a structured "quant scorecard"
with anomaly flags that tell Layer 2 what questions to ask.

### Signal Generation Functions

#### Standard Z-Score (already in `RiskComputer`)

```python
z_score = (price - rolling_mean) / rolling_std
# Interpretation: >2 = overbought, <-2 = oversold
```

#### Bollinger Band %B

```python
def bollinger_pct_b(price: float, upper: float, lower: float) -> float:
    """Where is price relative to Bollinger Bands?
    >1.0 = above upper (overbought/momentum)
    <0.0 = below lower (oversold/panic)
    """
    return (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
```

#### Robust Z-Score (MAD-based)

```python
def robust_z_score(prices: list[float], window: int = 20) -> float:
    """Uses Median and IQR instead of Mean and StdDev.
    Less sensitive to fat-tail crashes / black swan events.
    """
    recent = prices[-window:]
    median = np.median(recent)
    q75, q25 = np.percentile(recent, [75, 25])
    iqr = q75 - q25
    return (prices[-1] - median) / (iqr * 0.7413) if iqr > 0 else 0.0
```

#### Percentile Rank

```python
def percentile_rank(values: list[float], current: float) -> float:
    """Non-parametric: what % of historical values are below current?
    No assumption about distribution shape.
    99th percentile = extreme territory.
    """
    return sum(1 for v in values if v < current) / len(values) * 100
```

#### Cointegration Z-Score (for Pairs Trading)

```python
from statsmodels.tsa.stattools import coint

def cointegration_z_score(
    prices_a: list[float],
    prices_b: list[float],
) -> dict:
    """Z-score of the spread between two correlated assets.
    When spread Z hits ±2, relationship likely mean-reverts.
    """
    _, p_value, _ = coint(prices_a, prices_b)
    spread = np.array(prices_a) - np.array(prices_b)
    z = (spread[-1] - np.mean(spread)) / np.std(spread)
    return {"z_score": z, "p_value": p_value, "is_cointegrated": p_value < 0.05}
```

### Risk/Reward Analysis Metrics

#### Sortino Ratio (already in `RiskComputer`)

```python
sortino = (mean_return - risk_free) / downside_deviation
# Only penalizes DOWNSIDE volatility. >2 = excellent.
```

#### Calmar Ratio (already in `RiskComputer`)

```python
calmar = annualized_return / abs(max_drawdown)
# >2.0 = excellent risk-adjusted return
```

#### Omega Ratio (NEW)

```python
def omega_ratio(
    returns: np.ndarray,
    threshold: float = 0.0,
) -> float:
    """Probability-weighted gains vs losses above threshold.
    Unlike Sharpe, captures skewness and kurtosis (tail risk).
    """
    excess = returns - threshold
    gains = np.sum(excess[excess > 0])
    losses = np.abs(np.sum(excess[excess < 0]))
    return gains / losses if losses > 0 else float("inf")
```

#### Kelly Criterion (NEW)

```python
def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Optimal position size for maximum wealth growth.
    Full Kelly is volatile — use Half-Kelly (result / 2) in practice.
    """
    if avg_loss == 0:
        return 0.0
    b = avg_win / avg_loss  # payoff ratio
    return (b * win_rate - (1 - win_rate)) / b

def half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Half-Kelly: half the optimal bet for reduced variance."""
    return kelly_fraction(win_rate, avg_win, avg_loss) / 2
```

#### VaR / CVaR (already in `RiskComputer`)

```python
var_95 = np.percentile(returns, 5)          # 95% confidence worst loss
cvar_95 = np.mean(returns[returns <= var_95])  # Expected Shortfall
```

### Quant Scorecard Output

```python
class QuantScorecard(BaseModel):
    """Layer 1 output — pure numeric signals per ticker."""
    ticker: str
    computed_at: datetime

    # Signal Generation
    z_score_20d: float = 0.0
    robust_z_score_20d: float = 0.0
    bollinger_pct_b: float = 0.5
    percentile_rank_price: float = 50.0
    percentile_rank_volume: float = 50.0

    # Risk/Reward
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    omega_ratio: float = 0.0
    kelly_fraction: float = 0.0
    half_kelly: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0

    # Anomaly Flags (for Layer 2 to investigate)
    flags: list[str] = []
    # e.g., ["z_score_extreme_high", "volume_99th_percentile",
    #        "insider_net_buying_spike", "earnings_in_3_days"]
```

### Anomaly Detection Logic

```python
def generate_flags(scorecard: QuantScorecard, data: dict) -> list[str]:
    """Auto-detect anomalies that warrant follow-up questions."""
    flags = []

    # Price extremes
    if abs(scorecard.z_score_20d) > 2.0:
        flags.append(f"z_score_{'high' if scorecard.z_score_20d > 0 else 'low'}")
    if scorecard.bollinger_pct_b > 1.0:
        flags.append("price_above_upper_band")
    elif scorecard.bollinger_pct_b < 0.0:
        flags.append("price_below_lower_band")

    # Volume spike
    if scorecard.percentile_rank_volume > 95:
        flags.append("volume_spike_95th")

    # Risk
    if scorecard.max_drawdown < -0.20:
        flags.append("drawdown_exceeds_20pct")
    if scorecard.calmar_ratio > 3.0:
        flags.append("exceptional_calmar")
    if scorecard.sortino_ratio < 0:
        flags.append("negative_sortino")

    # Earnings proximity
    if data.get("days_until_earnings") and data["days_until_earnings"] <= 5:
        flags.append(f"earnings_in_{data['days_until_earnings']}_days")

    # Insider activity
    if data.get("net_insider_buying_90d", 0) > 500_000:
        flags.append("insider_buying_spike")
    elif data.get("net_insider_buying_90d", 0) < -500_000:
        flags.append("insider_selling_spike")

    return flags
```

---

## 2.3 — Layer 2: LLM Question Generator

### Purpose

Given the quant scorecard + anomaly flags, ask the LLM to generate **5-8
follow-up questions** that would help a human analyst make a decision.
This is how we "go through each stock" and "find follow-up questions."

### How it works

One LLM call per ticker. Input is the quant scorecard (~2K chars).
Output is a structured list of questions.

```python
QUESTION_GENERATOR_PROMPT = """
You are a senior quant analyst reviewing a stock scorecard.
Based on the data and anomaly flags, generate exactly 5 follow-up
questions that would help determine if this is a BUY, HOLD, or SELL.

Rules:
- Questions must be ANSWERABLE from: news articles, YouTube transcripts,
  SEC filings, analyst reports, or company financials
- Each question should target a DIFFERENT data source
- Prioritize questions about the anomaly flags
- Be specific: "What caused the volume spike on Feb 14?" not "Why volume?"

Scorecard:
{scorecard_json}

Respond with a JSON array of exactly 5 objects:
[
  {
    "question": "...",
    "target_source": "news" | "transcripts" | "fundamentals" | "technicals" | "insider",
    "priority": "high" | "medium" | "low"
  }
]
"""
```

### Example Output

```json
[
  {
    "question": "What event caused NVDA's volume to spike to the 99th percentile on Feb 14?",
    "target_source": "news",
    "priority": "high"
  },
  {
    "question": "Are YouTube finance channels still bullish on NVDA after the recent earnings miss flag?",
    "target_source": "transcripts",
    "priority": "high"
  },
  {
    "question": "Has insider buying continued in the last 30 days or was it a one-time event?",
    "target_source": "insider",
    "priority": "medium"
  },
  {
    "question": "Is the Z-score of 2.3 reflecting momentum or an overbought condition given current ADX trend?",
    "target_source": "technicals",
    "priority": "medium"
  },
  {
    "question": "How does current free cash flow compare to the 3-year average — is growth sustainable?",
    "target_source": "fundamentals",
    "priority": "low"
  }
]
```

---

## 2.4 — Layer 3: RAG Answer Engine

### Purpose

For each question from Layer 2, search the Phase 1 data stores to find the
answer. This is **semantic search over our own collected data**, not external
API calls. This is where we "find answers" to the follow-up questions.

### Architecture

```
Question from Layer 2
      │
      ▼
┌─────────────────────────────┐
│  Source Router               │  Routes question to right data store
│  based on target_source      │
└──────────┬──────────────────┘
           │
    ┌──────▼──────────────────────────────────┐
    │  news      → DuckDB news table search    │
    │  transcripts → DuckDB transcript search  │
    │  fundamentals → DuckDB balance/cash/fin  │
    │  technicals → DuckDB technicals table    │
    │  insider   → DuckDB insider_activity     │
    └──────────┬──────────────────────────────┘
               │
    ┌──────────▼──────────────────────────────┐
    │  Chunk & Search                          │
    │  1. Pull relevant rows from DuckDB       │
    │  2. Chunk text into 1K-2K char segments  │
    │  3. Rank by keyword relevance (BM25)     │
    │  4. Take top 3 chunks                    │
    └──────────┬──────────────────────────────┘
               │
    ┌──────────▼──────────────────────────────┐
    │  LLM Answer Extraction                   │
    │  "Given these chunks, answer the          │
    │   question in 2-3 sentences."             │
    │  (~2K chars input + ~200 chars output)    │
    └──────────────────────────────────────────┘
```

### Chunking Strategy

> [!IMPORTANT]
> Keep chunks at **1K-2K chars** (~250-500 tokens). This keeps each LLM
> call tiny and fast. We do **more small calls** rather than one big one.

```python
def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for search.
    Overlap ensures we don't cut mid-sentence on boundaries.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start = end - overlap
    return chunks
```

### Search Implementation (BM25 — no vector DB needed)

We use **BM25** (keyword ranking) instead of vector embeddings. Why:

- Zero infrastructure: no vector DB to set up/maintain
- Fast: pure Python, runs in milliseconds
- Good enough for financial text (domain-specific terms are highly distinctive)
- Library: `rank_bm25` (pip install rank-bm25)

```python
from rank_bm25 import BM25Okapi

class RAGEngine:
    """Search Phase 1 text data to answer follow-up questions."""

    def search_and_answer(
        self,
        question: str,
        target_source: str,
        ticker: str,
    ) -> str:
        """
        1. Pull text from the right DuckDB table
        2. Chunk it
        3. BM25 rank chunks by question terms
        4. Take top 3 chunks
        5. Send to LLM for extraction
        """
        # Step 1: Get raw text
        raw_texts = self._get_source_texts(ticker, target_source)

        # Step 2: Chunk
        all_chunks = []
        for text in raw_texts:
            all_chunks.extend(chunk_text(text))

        if not all_chunks:
            return "No data available to answer this question."

        # Step 3: BM25 rank
        tokenized = [c.lower().split() for c in all_chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(question.lower().split())
        top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:3]
        top_chunks = [all_chunks[i] for i in top_idx]

        # Step 4: LLM extract answer
        context = "\n---\n".join(top_chunks)
        return self._extract_answer(question, context)
```

### Why BM25 Over Vector DB

| Factor | BM25 (Our Choice) | Vector DB (Pinecone/Chroma) |
|--------|-------------------|---------------------------|
| Setup | `pip install rank-bm25` | Install + run server |
| Speed | <10ms per search | ~50-100ms per search |
| Accuracy | Excellent for financial terms | Slightly better for vague queries |
| Maintenance | Zero | Embedding model updates, index management |
| Cost | Free | Embedding API costs or local model VRAM |

> We can upgrade to vector search later if BM25 proves insufficient.
> But for financial text where terms like "earnings", "revenue", "guidance"
> are highly distinctive, BM25 is excellent.

---

## 2.5 — Layer 4: Dossier Synthesizer

### Purpose

Take all outputs from Layers 1-3 and compress them into a single
**8K-10K token dossier** that the Trading Engine can consume.

### Context Window Strategy

> [!IMPORTANT]
> **Target: 10K tokens per dossier** (≈ 40K chars).
> This is the sweet spot between awareness and speed.

| Consideration | Value | Reasoning |
|---------------|-------|-----------|
| LLM Context Window | 32K tokens | Gemma 27B / Llama 3 sweet spot |
| System Prompt | ~2K tokens | Agent instructions |
| Dossier Input | ~10K tokens | All ticker analysis |
| LLM Working Memory | ~5K tokens | Room for reasoning |
| Response | ~2K tokens | Decision output |
| **Headroom** | **~13K tokens** | Safety margin |

**Why not 20K?** Models degrade quality in the middle of long contexts
("Lost in the Middle" problem). 10K keeps everything in the high-attention
zone at the start and end of the context window.

**Why not 5K?** Too much information loss. We'd have to cut the quant
scorecard or Q&A pairs, losing critical context.

### Dossier Structure

```python
class TickerDossier(BaseModel):
    """The final compressed analysis — what Phase 3 consumes."""
    ticker: str
    generated_at: datetime
    version: int = 1

    # Layer 1: Quant Summary (fits in ~1K tokens)
    quant_scorecard: QuantScorecard
    signal_summary: str  # "Overbought with strong momentum" (~50 chars)

    # Layer 2+3: Q&A Pairs (fits in ~3K tokens)
    qa_pairs: list[QAPair]  # 5 questions + answers

    # Layer 4: Synthesis (fits in ~2K tokens)
    executive_summary: str      # 3-5 sentence overview
    bull_case: str              # Why buy
    bear_case: str              # Why not buy
    key_catalysts: list[str]    # Upcoming events that could move price
    conviction_score: float     # 0.0-1.0 overall conviction

    # Metadata
    data_freshness: dict[str, datetime]  # When each data source was last updated
    total_tokens: int           # Self-reported token count

class QAPair(BaseModel):
    question: str
    answer: str
    source: str       # "news" | "transcripts" | etc.
    confidence: str   # "high" | "medium" | "low"
```

### Synthesis Prompt

```python
DOSSIER_PROMPT = """
You are synthesizing a trading analysis dossier. Compress all information
into a concise, decision-ready format.

QUANT SCORECARD:
{scorecard_json}

Q&A RESEARCH:
{qa_pairs_json}

Generate:
1. executive_summary: 3-5 sentences covering the thesis
2. bull_case: strongest arguments for buying (2-3 sentences)
3. bear_case: strongest arguments against (2-3 sentences)
4. key_catalysts: list of 3-5 upcoming events that could move the stock
5. conviction_score: 0.0-1.0 (0=strong sell, 0.5=hold, 1.0=strong buy)

Keep total output under 2000 chars. Be specific with numbers and dates.
"""
```

---

## 2.6 — Auto-Managed Watchlist

### Design Principles

1. **Discovery feeds the watchlist** — High scoring tickers from Phase 1 are auto-added
2. **User tickers are sacred** — Manually added tickers are never auto-removed
3. **Dossier-based rotation** — Low-conviction dossiers trigger rotation
4. **Size cap** — Max 5 tickers during debug (configurable up to 20)
5. **Cooldown** — Removed tickers can't be re-added for 7 days

### Watchlist Entry Model

```python
class WatchlistEntry(BaseModel):
    ticker: str
    source: Literal["manual", "auto_discovery"]
    added_at: datetime
    discovery_score: float = 0.0          # From Phase 1
    conviction_score: float = 0.0         # From dossier (Layer 4)
    last_analyzed: datetime | None = None
    times_analyzed: int = 0
    status: Literal["active", "pending_analysis", "cooldown", "removed"]
    position_held: bool = False           # True if we have an open position
    dossier_id: str | None = None         # FK to stored dossier
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
    6. → Run Layer 1-4 analysis funnel → Generate dossier
    7. → Update conviction_score from dossier
```

### Auto-Remove Logic

```
After each analysis cycle:
    1. For each auto-discovery ticker (NOT manual):
       - If conviction_score < 0.3 for 2+ consecutive analyses → remove
       - If no new discovery mentions in 5 days AND conviction < 0.5 → remove
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
| 3 | 0.55 | 5.5 |
| 5 | 0.25 | 2.5 |
| 7 | 0.10 | 1.0 (floor) |

---

## 2.7 — Complete Data Flow (Phase 1 → Phase 2 → Phase 3)

```
┌────────────────────────────────────────────────────────────────┐
│                        PHASE 1                                  │
│  Discovery Service (ticker_scanner + reddit_collector)          │
│  → ScoredTicker { ticker, score, sources, sentiment }          │
└─────────────────────────┬──────────────────────────────────────┘
                          │ Top scored tickers
                          ▼
┌────────────────────────────────────────────────────────────────┐
│                       PHASE 2                                   │
│                                                                 │
│  ┌─ WatchlistManager ─────────────────────────────────────┐    │
│  │  Auto-add from discovery → pending_analysis             │    │
│  └────────────────────────┬────────────────────────────────┘    │
│                           │                                     │
│  ┌─ For each pending ticker ──────────────────────────────┐    │
│  │                                                        │    │
│  │  Layer 1: QuantSignalEngine.compute(ticker)            │    │
│  │    ├─ Read price_history, technicals, risk_metrics      │    │
│  │    ├─ Compute: Z-Score, %B, Robust Z, Omega, Kelly     │    │
│  │    ├─ Detect anomalies (flags)                          │    │
│  │    └─ Output: QuantScorecard (~2K chars)                │    │
│  │                                                        │    │
│  │  Layer 2: QuestionGenerator.generate(scorecard)         │    │
│  │    ├─ One LLM call (~2K input, ~1K output)              │    │
│  │    └─ Output: 5 follow-up questions                     │    │
│  │                                                        │    │
│  │  Layer 3: RAGEngine.answer_all(questions, ticker)       │    │
│  │    ├─ For each question:                                │    │
│  │    │   ├─ Route to correct DuckDB table                 │    │
│  │    │   ├─ Chunk + BM25 search                           │    │
│  │    │   └─ LLM extract answer (~2K in, ~200 out)         │    │
│  │    └─ Output: 5 QAPairs (~3K chars)                     │    │
│  │                                                        │    │
│  │  Layer 4: DossierSynthesizer.synthesize(all_above)      │    │
│  │    ├─ One LLM call (~6K input, ~2K output)              │    │
│  │    └─ Output: TickerDossier (~8-10K tokens total)       │    │
│  │                                                        │    │
│  └─ Store dossier → DuckDB ticker_dossiers table          │    │
│                                                                 │
│  Update watchlist → conviction_score from dossier               │
│  Auto-remove low conviction tickers                             │
└─────────────────────────┬──────────────────────────────────────┘
                          │ Active tickers + dossiers
                          ▼
┌────────────────────────────────────────────────────────────────┐
│                       PHASE 3                                   │
│  Trading Engine reads TickerDossier                             │
│  → SignalRouter: BUY / SELL / HOLD                              │
│  → Order Manager: execute trades                                │
│  → Price Monitor: stop-loss / take-profit triggers              │
└────────────────────────────────────────────────────────────────┘
```

---

## 2.8 — LLM Call Budget Per Ticker

| Layer | LLM Calls | Input Tokens | Output Tokens | Purpose |
|-------|-----------|-------------|---------------|---------|
| 1 | 0 | 0 | 0 | Pure math |
| 2 | 1 | ~500 | ~300 | Generate questions |
| 3 | 5 | ~500 each | ~100 each | Answer each question |
| 4 | 1 | ~1500 | ~500 | Synthesize dossier |
| **Total** | **7** | **~4500** | **~1300** | **~6K tokens total** |

For 5 tickers on the watchlist = **35 LLM calls** per analysis cycle.
At ~2 seconds per call = **~70 seconds total** (with parallelism: ~30 seconds).

---

## 2.9 — DuckDB Persistence

```sql
-- Watchlist (same as before, with dossier link)
CREATE TABLE IF NOT EXISTS watchlist (
    ticker           VARCHAR PRIMARY KEY,
    source           VARCHAR DEFAULT 'manual',
    added_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovery_score  DOUBLE DEFAULT 0.0,
    conviction_score DOUBLE DEFAULT 0.0,
    last_analyzed    TIMESTAMP,
    times_analyzed   INTEGER DEFAULT 0,
    status           VARCHAR DEFAULT 'active',
    position_held    BOOLEAN DEFAULT FALSE,
    last_signal      VARCHAR DEFAULT 'HOLD',
    consecutive_low  INTEGER DEFAULT 0,
    removed_at       TIMESTAMP,
    dossier_id       VARCHAR
);

-- Quant scorecards (one per ticker per run)
CREATE TABLE IF NOT EXISTS quant_scorecards (
    id               VARCHAR PRIMARY KEY,
    ticker           VARCHAR NOT NULL,
    computed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    z_score_20d      DOUBLE,
    robust_z_score   DOUBLE,
    bollinger_pct_b  DOUBLE,
    pctl_rank_price  DOUBLE,
    pctl_rank_volume DOUBLE,
    sharpe_ratio     DOUBLE,
    sortino_ratio    DOUBLE,
    calmar_ratio     DOUBLE,
    omega_ratio      DOUBLE,
    kelly_fraction   DOUBLE,
    half_kelly       DOUBLE,
    var_95           DOUBLE,
    cvar_95          DOUBLE,
    max_drawdown     DOUBLE,
    flags            VARCHAR DEFAULT '[]'
);

-- Ticker dossiers (the final synthesized analysis)
CREATE TABLE IF NOT EXISTS ticker_dossiers (
    id                VARCHAR PRIMARY KEY,
    ticker            VARCHAR NOT NULL,
    generated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version           INTEGER DEFAULT 1,
    scorecard_json    VARCHAR,
    qa_pairs_json     VARCHAR,
    executive_summary VARCHAR,
    bull_case         VARCHAR,
    bear_case         VARCHAR,
    key_catalysts     VARCHAR DEFAULT '[]',
    conviction_score  DOUBLE DEFAULT 0.5,
    total_tokens      INTEGER DEFAULT 0
);
```

---

## 2.10 — New Files

```
app/
├── services/
│   ├── watchlist_manager.py     # Auto-add/remove logic + DuckDB persistence
│   └── deep_analysis_service.py # Orchestrates Layer 1-4 funnel
├── engine/
│   ├── quant_signals.py         # Layer 1: All quant functions
│   ├── question_generator.py    # Layer 2: LLM question generation
│   ├── rag_engine.py            # Layer 3: BM25 search + LLM extraction
│   └── dossier_synthesizer.py   # Layer 4: Final synthesis
├── models/
│   └── dossier.py               # QuantScorecard, QAPair, TickerDossier
```

---

## 2.11 — `app/services/watchlist_manager.py`

```python
class WatchlistManager:
    """Manages the automated watchlist lifecycle."""

    MAX_SIZE = 5                # Start small for debugging
    COOLDOWN_DAYS = 7
    MIN_DISCOVERY_SCORE = 3.0
    STALE_DAYS = 5
    CONSECUTIVE_LOW_LIMIT = 2   # Low-conviction analyses before removal

    def get_active_tickers(self) -> list[str]:
        """Return current active watchlist tickers."""

    def add_manual(self, ticker: str) -> bool:
        """User manually adds a ticker. Always succeeds (up to MAX_SIZE)."""

    def remove_manual(self, ticker: str) -> bool:
        """User manually removes a ticker."""

    def process_discovery_results(self, scored: list[ScoredTicker]) -> list[str]:
        """Auto-add top candidates from discovery. Returns newly added."""

    def process_dossier(self, ticker: str, dossier: TickerDossier):
        """Update watchlist after dossier generation.
        May trigger auto-removal if conviction too low.
        """

    def sync_to_json(self):
        """Write current active tickers to watchlist.json for backward compat."""
```

---

## 2.12 — API Endpoints

```
GET    /api/watchlist                → Get full watchlist with metadata
POST   /api/watchlist/{ticker}       → Manually add ticker (source=manual)
DELETE /api/watchlist/{ticker}       → Manually remove ticker
GET    /api/watchlist/auto-manage    → Trigger auto-add/remove cycle
GET    /api/watchlist/cooldown       → List tickers on cooldown

# Deep Analysis
POST   /api/analysis/deep/{ticker}  → Run Layer 1-4 for one ticker
GET    /api/dossiers/{ticker}       → Get latest dossier for ticker
GET    /api/dossiers/{ticker}/history → All historical dossiers
GET    /api/scorecards/{ticker}     → Get latest quant scorecard
```

---

## 2.13 — Frontend Changes

Enhance existing watchlist table to show:

- **Source badge**: "Manual" (blue) vs "Auto" (green)
- **Conviction score**: Color-coded bar (red → yellow → green)
- **Anomaly flags**: Pill badges showing active flags
- **Days on list**: How long the ticker has been tracked
- **Dossier preview**: Expandable executive summary
- **Auto-remove countdown**: Visual indicator before auto-removal
- **Run Deep Analysis**: Button to trigger Layer 1-4 for a single ticker

---

## 2.14 — Quant Function Summary Table

| Function | Type | Already Built? | Best Used For |
|----------|------|---------------|---------------|
| **Z-Score** | Signal | ✅ `RiskComputer` | Mean reversion detection |
| **Robust Z-Score** | Signal | ❌ New | Noisy data with fat tails |
| **Bollinger %B** | Signal | 🟡 Partial (bands computed, %B not) | Volatility breakouts |
| **Percentile Rank** | Signal | ❌ New | Distribution-free regime detection |
| **Cointegration Z** | Signal | ❌ New (pairs trading) | Mean reversion on spreads |
| **Sortino Ratio** | Risk | ✅ `RiskComputer` | Downside-only risk eval |
| **Calmar Ratio** | Risk | ✅ `RiskComputer` | Drawdown evaluation |
| **Omega Ratio** | Risk | ❌ New | Full distribution risk (tails) |
| **Kelly Criterion** | Sizing | ❌ New | Optimal position sizing |
| **VaR / CVaR** | Risk | ✅ `RiskComputer` | Tail risk estimation |
| **Sharpe Ratio** | Risk | ✅ `RiskComputer` | Standard risk-adjusted return |

---

## Testing Plan

1. **Unit tests** for each new quant function (Omega, Kelly, Robust Z, %B, Percentile)
2. **Unit tests** for BM25 chunking and search
3. **Unit tests** for score decay calculation
4. **Unit tests** for auto-add logic (respects max size, cooldown, validation)
5. **Unit tests** for auto-remove logic (protects manual, protects positions)
6. **Integration test**: Layer 1→2→3→4 full funnel with mock data
7. **Integration test**: Discovery → auto-add → deep analysis → dossier
8. **End-to-end test**: 5 tickers through full pipeline, verify dossier quality
9. **Performance test**: Confirm <30 seconds for 5 tickers with parallelism
10. **Migration test**: Import from watchlist.json → DuckDB → export back

## Dependencies

- Phase 1 (Ticker Discovery) must be complete ✅
- `rank-bm25` — BM25 search (pip install rank-bm25)
- `statsmodels` — Cointegration test (pip install statsmodels)
- `empyrical` — Omega ratio, Calmar ratio (pip install empyrical)
- Existing: `numpy`, `scipy`, `pandas`, `yfinance`, DuckDB, LLMService
