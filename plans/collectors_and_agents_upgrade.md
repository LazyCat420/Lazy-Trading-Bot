# Phase 5 — Collectors & Agents Upgrade

> **Goal**: Expand all collectors to pull maximum data and upgrade all agents
> to analyze it with institutional-grade depth.
> Research-backed: web searches + programmatic audit of pandas-ta (154 indicators).

> [!NOTE]
> This phase is **enhancement**, not blocking. The pipeline already works end-to-end
> with Discovery → Watchlist → Deep Analysis → Trading (Phase 3).
> These upgrades make the AI's analysis richer and more accurate.

---

## What Already Exists

| Component | File | Status |
|-----------|------|--------|
| Technical collector (7 indicators) | `app/collectors/technical_computer.py` | ✅ Basic |
| Fundamental collector (24 metrics) | `app/collectors/yfinance_collector.py` | ✅ Basic |
| News collector (yFinance only) | `app/collectors/news_collector.py` | ✅ Basic |
| YouTube collector | `app/collectors/youtube_collector.py` | ✅ Basic |
| Technical agent | `app/agents/technical_agent.py` | ✅ Basic |
| Fundamental agent | `app/agents/fundamental_agent.py` | ✅ Basic |
| Sentiment agent | `app/agents/sentiment_agent.py` | ✅ Basic |
| Risk agent | `app/agents/risk_agent.py` | ✅ Basic |
| DuckDB persistence | `app/database.py` | ✅ Built |
| Deep Analysis (4-layer funnel) | `app/services/deep_analysis_service.py` | ✅ Built |

### What This Phase Adds

| Upgrade | Impact |
|---------|--------|
| 154 pandas-ta indicators (from 7) | Technical agent sees full picture |
| Balance sheet, cash flow, analyst, insider, earnings data | Fundamental agent gets Wall Street-grade data |
| Google News RSS, SEC EDGAR filings | 5x more news sources |
| Full YouTube transcripts (no truncation) | Sentiment agent reads everything |
| Quantitative risk metrics (Sharpe, VaR, Beta, etc.) | Risk agent uses real math |

---

## 1. Technical Collector (`technical_computer.py`)

### Current State

Computes **7 indicator groups**: RSI(14), MACD(12/26/9), SMA(20/50/200), Bollinger Bands(20,2), ATR(14), Stochastic(14/3/3).

### Target: ALL 154 pandas-ta Indicators (Grouped by Category)

We will compute **every indicator** that pandas-ta offers. The full list (confirmed programmatically via `df.ta.indicators()`):

#### Momentum (36 indicators)

`ao`, `apo`, `bias`, `bop`, `brar`, `cci`, `cfo`, `cg`, `cmo`, `coppock`, `crsi`, `cti`,
`er`, `eri`, `fisher`, `inertia`, `kdj`, `kst`, `macd`, `mom`, `pgo`, `ppo`, `psl`,
`pvo`, `qqe`, `roc`, `rsi`, `rsx`, `rvgi`, `rvi`, `slope`, `smi`, `squeeze`,
`squeeze_pro`, `stc`, `stoch`, `stochf`, `stochrsi`, `tmo`, `trix`, `tsi`, `uo`, `willr`

#### Overlap / Moving Averages (28 indicators)

`alma`, `alligator`, `alphatrend`, `dema`, `ema`, `fwma`, `hilo`, `hl2`, `hlc3`,
`hma`, `ht_trendline`, `hwc`, `hwma`, `ichimoku`, `jma`, `kama`, `linreg`, `mcgd`,
`midpoint`, `midprice`, `ohlc4`, `pwma`, `rma`, `sinwma`, `sma`, `smma`, `supertrend`,
`swma`, `t3`, `tema`, `trima`, `vidya`, `vwap`, `vwma`, `wcp`, `wma`, `zlma`

#### Trend (14 indicators)

`adx`, `amat`, `aroon`, `chandelier_exit`, `chop`, `cksp`, `decay`, `decreasing`,
`dm`, `dpo`, `increasing`, `long_run`, `psar`, `qstick`, `rwi`, `short_run`,
`trendflex`, `vhf`, `vhm`, `vortex`, `xsignals`, `zigzag`

#### Volatility (12 indicators)

`aberration`, `accbands`, `atr`, `atrts`, `bbands`, `donchian`, `hwc`, `kc`,
`massi`, `natr`, `pdist`, `thermo`, `true_range`, `ui`

#### Volume (14 indicators)

`ad`, `adosc`, `aobv`, `cmf`, `efi`, `eom`, `kvo`, `mfi`, `nvi`, `obv`,
`pvi`, `pvol`, `pvr`, `pvt`, `tsv`, `vwap`

#### Statistics (8 indicators)

`entropy`, `kurtosis`, `mad`, `median`, `quantile`, `skew`, `stdev`, `variance`, `zscore`

#### Candle Patterns

`cdl_pattern` (wrapper for 60+ candlestick recognition patterns), `cdl_z`, `ha` (Heikin Ashi)

#### Performance

`log_return`, `percent_return`

### Implementation Strategy

Rather than adding 154 columns to one table, **organize by category**:

```python
# Run ALL indicators at once using pandas-ta strategy
strategy = ta.AllCandlesStrategy  # or ta.Strategy("all")
df.ta.strategy(strategy)
```

This appends ~200+ columns to the DataFrame in one call. Then we:

1. **Store the full DataFrame** as a Parquet file per ticker per day (e.g., `data/technicals/NVDA_2026-02-16.parquet`)
2. **Store key summary indicators** in DuckDB `technicals` table for quick agent queries
3. **Pass the last 6 months** of key indicators to the Technical Agent

#### DuckDB Schema Update — Expanded `technicals` Table

Add these columns (all `DOUBLE`, nullable):

```sql
-- Moving Averages
ema_9, ema_21, ema_50, ema_200,
dema_20, tema_20, vwap, vwma_20,
hma_20, kama_10, alma_20, zlma_20,

-- Momentum
cci_20, willr_14, mfi_14, roc_12,
mom_10, ao, apo_12_26, tsi_25_13,
uo, fisher_9, stochrsi_14, squeeze_signal,
coppock, kdj_k, kdj_d, kdj_j,

-- Trend
adx_14, adx_plus_di, adx_minus_di,
aroon_up, aroon_down, aroon_osc,
supertrend, supertrend_direction,
psar, psar_direction,
chop_14, vortex_plus, vortex_minus,

-- Volatility
natr_14, donchian_upper, donchian_lower, donchian_mid,
kc_upper, kc_lower, kc_mid,
true_range, ui_14,

-- Volume
obv, ad, adosc, cmf_20, efi_13,
eom_14, mfi_14, nvi, pvi, pvt, kvo,

-- Statistics
zscore_20, skew_20, kurtosis_20, entropy_10,

-- Candles
cdl_doji, cdl_hammer, cdl_engulfing, cdl_morning_star,

-- Ichimoku (5 components)
ichi_conversion, ichi_base, ichi_span_a, ichi_span_b, ichi_lagging
```

#### Fibonacci Retracement (Custom Computation)

```python
def compute_fibonacci(highs, lows, lookback=120):
    """Compute Fibonacci retracement levels from recent swing high/low."""
    swing_high = max(highs[-lookback:])
    swing_low = min(lows[-lookback:])
    diff = swing_high - swing_low
    return {
        "fib_0": swing_high,
        "fib_236": swing_high - diff * 0.236,
        "fib_382": swing_high - diff * 0.382,
        "fib_500": swing_high - diff * 0.500,
        "fib_618": swing_high - diff * 0.618,
        "fib_786": swing_high - diff * 0.786,
        "fib_1": swing_low,
    }
```

---

## 2. Technical Agent (`technical_agent.py`)

### Current Gap

Sends only **latest day's** indicators to LLM. No trend context at all.

### Fix: Send Last 6 Months (~126 Trading Days)

```python
# Send last 6 months of key daily indicators
recent_6mo = technicals[-126:]
for ta_row in recent_6mo:
    sections.append(
        f"- {ta_row.date}: RSI={ta_row.rsi} MACD={ta_row.macd} "
        f"ADX={ta_row.adx_14} EMA9={ta_row.ema_9} ..."
    )
```

**Why 6 months**: Captures full swing cycles, golden/death crosses in formation, seasonal patterns, earnings reaction patterns (at least 2 quarters).

### Prompt Upgrade (`technical_analysis.md`)

Add expertise for:

- EMA crossover systems (9/21 short-term, 50/200 long-term)
- ADX trend strength (>25 trending, <20 ranging, >40 strong trend)
- Ichimoku cloud analysis (TK cross, Kumo twist, price vs cloud)
- Volume confirmation (OBV divergence, CMF direction)
- Squeeze detection (Bollinger inside Keltner = squeeze → breakout imminent)
- Fibonacci retracement & extension levels
- Supertrend & Parabolic SAR for trend direction
- Multi-timeframe confluence (daily + weekly alignment)
- Candlestick pattern significance (doji at resistance, hammer at support)
- Z-score of price (how far from mean = potential reversion)

---

## 3. Fundamental Collector (`yfinance_collector.py`)

### Current State

Collects from `t.info` (24 metrics) and `t.financials` (income statement only).

### 5 New Collector Methods

#### 3A. `collect_balance_sheet(ticker)`

```python
t = yf.Ticker(ticker)
bs = t.balance_sheet           # yearly
bs_q = t.quarterly_balance_sheet  # quarterly
```

**Fields to extract**: Total Assets, Total Liabilities, Stockholders Equity, Current Assets, Current Liabilities, Current Ratio, Quick Ratio, Total Debt, Cash and Equivalents, Net Working Capital, Goodwill, Intangible Assets, Tangible Book Value.

#### 3B. `collect_cashflow(ticker)`

```python
cf = t.cashflow               # yearly
cf_q = t.quarterly_cashflow   # quarterly
```

**Fields**: Operating Cash Flow, Capital Expenditures, Free Cash Flow, Investment in Plant/Property, Financing Cash Flow, Dividends Paid, Share Buybacks, Net Change in Cash.

#### 3C. `collect_analyst_data(ticker)`

```python
targets = t.analyst_price_targets    # mean, median, high, low, current
recs = t.recommendations            # buy/hold/sell counts
recs_summary = t.recommendations_summary
```

**Fields**: Target Mean, Target Median, Target High, Target Low, Number of Analysts, Strong Buy count, Buy count, Hold count, Sell count, Strong Sell count.

#### 3D. `collect_insider_activity(ticker)`

```python
insiders = t.insider_transactions    # recent insider trades
holders = t.institutional_holders    # top institutions
major = t.major_holders              # ownership breakdown
```

**Fields**: Transaction Type (buy/sell), Shares, Value, Insider Name/Title, Date, Net Insider Buying (sum of recent 90 days), Institutional Ownership %, Top 10 Holders.

#### 3E. `collect_earnings_calendar(ticker)`

```python
cal = t.calendar                    # next earnings date, dividend dates
earnings = t.earnings               # historical earnings
```

**Fields**: Next Earnings Date, Days Until Earnings, Earnings Estimate, Previous Earnings (actual vs estimate), Earnings Surprise %, Forward Guidance (if available).

### New Pydantic Models (`market_data.py`)

```python
class BalanceSheetRow(BaseModel):
    ticker: str
    year: int
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    stockholders_equity: float = 0.0
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    current_ratio: float = 0.0
    total_debt: float = 0.0
    cash_and_equivalents: float = 0.0
    net_working_capital: float = 0.0
    goodwill: float = 0.0
    tangible_book_value: float = 0.0

class CashFlowRow(BaseModel):
    ticker: str
    year: int
    operating_cashflow: float = 0.0
    capital_expenditures: float = 0.0
    free_cashflow: float = 0.0
    financing_cashflow: float = 0.0
    investing_cashflow: float = 0.0
    dividends_paid: float = 0.0
    share_buybacks: float = 0.0
    net_change_in_cash: float = 0.0

class AnalystData(BaseModel):
    ticker: str
    snapshot_date: date
    target_mean: float = 0.0
    target_median: float = 0.0
    target_high: float = 0.0
    target_low: float = 0.0
    num_analysts: int = 0
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0

class InsiderSummary(BaseModel):
    ticker: str
    snapshot_date: date
    net_insider_buying_90d: float = 0.0  # positive = net buying
    institutional_ownership_pct: float = 0.0
    insider_transactions: list[dict] = []  # recent transactions

class EarningsCalendar(BaseModel):
    ticker: str
    next_earnings_date: date | None = None
    days_until_earnings: int | None = None
    earnings_estimate: float | None = None
    previous_earnings_actual: float | None = None
    previous_earnings_estimate: float | None = None
    earnings_surprise_pct: float | None = None
```

### New DB Tables

```sql
CREATE TABLE IF NOT EXISTS balance_sheet (
    ticker VARCHAR NOT NULL, year INTEGER NOT NULL,
    total_assets DOUBLE, total_liabilities DOUBLE, stockholders_equity DOUBLE,
    current_assets DOUBLE, current_liabilities DOUBLE, current_ratio DOUBLE,
    total_debt DOUBLE, cash_and_equivalents DOUBLE, net_working_capital DOUBLE,
    goodwill DOUBLE, tangible_book_value DOUBLE,
    PRIMARY KEY (ticker, year)
);

CREATE TABLE IF NOT EXISTS cash_flows (
    ticker VARCHAR NOT NULL, year INTEGER NOT NULL,
    operating_cashflow DOUBLE, capital_expenditures DOUBLE, free_cashflow DOUBLE,
    financing_cashflow DOUBLE, investing_cashflow DOUBLE,
    dividends_paid DOUBLE, share_buybacks DOUBLE, net_change_in_cash DOUBLE,
    PRIMARY KEY (ticker, year)
);

CREATE TABLE IF NOT EXISTS analyst_data (
    ticker VARCHAR NOT NULL, snapshot_date DATE NOT NULL,
    target_mean DOUBLE, target_median DOUBLE, target_high DOUBLE, target_low DOUBLE,
    num_analysts INTEGER, strong_buy INTEGER, buy INTEGER,
    hold INTEGER, sell INTEGER, strong_sell INTEGER,
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS insider_activity (
    ticker VARCHAR NOT NULL, snapshot_date DATE NOT NULL,
    net_insider_buying_90d DOUBLE, institutional_ownership_pct DOUBLE,
    raw_transactions VARCHAR,
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS earnings_calendar (
    ticker VARCHAR NOT NULL, snapshot_date DATE NOT NULL,
    next_earnings_date DATE, days_until_earnings INTEGER,
    earnings_estimate DOUBLE, previous_actual DOUBLE,
    previous_estimate DOUBLE, surprise_pct DOUBLE,
    PRIMARY KEY (ticker, snapshot_date)
);
```

---

## 4. Fundamental Agent (`fundamental_agent.py`)

### Fix: Feed All New Data

Add to `format_context()`:

- **Balance sheet trends** (multi-year current ratio, debt/equity progression)
- **Cash flow quality** (operating CF growing? Capex sustainable? FCF yield?)
- **Analyst consensus** (target price vs current price = upside/downside %)
- **Insider activity** (net buying = bullish signal, net selling = caution)
- **Earnings proximity** (days until next earnings, historical surprise %)

### Prompt Upgrade

- Cash flow analysis: FCF yield, operating CF margin, capex-to-revenue ratio
- Balance sheet health trends: improving/deteriorating over 3-5 years
- Analyst sentiment: consensus target as a validation metric
- Insider + institutional ownership changes as confidence/contrarian signals
- Earnings catalyst: upcoming earnings as opportunity/risk factor

---

## 5. News Collector (`news_collector.py`)

### Current State

Single source: `yf.Ticker.news` — gives 5-15 articles, often sparse.

### Sources to Add

| Source | Method | Free Tier |
| ------ | ------ | --------- |
| **Google News RSS** | `feedparser` on `news.google.com/rss/search?q={ticker}+stock` | Unlimited |
| **Finnhub** | REST API, free tier 60 calls/min | Yes |
| **Marketaux** | REST API, sentiment analysis included | Yes (100/day) |
| **SEC EDGAR RSS** | RSS for 8-K, 10-K, 10-Q filings | Unlimited |
| **Reddit** | JSON API `/r/stocks/search.json?q={ticker}` | Unauthenticated |

### Implementation Priority

1. **Google News RSS** — broadest coverage, zero cost, requires only `feedparser`
2. **SEC EDGAR** — first-party filings (material events), free
3. **Finnhub** — real-time, includes sentiment if we add API key later
4. **Reddit** — retail sentiment signal

### Google News RSS Implementation

```python
import feedparser
from datetime import datetime, timedelta

async def _fetch_google_news(self, ticker: str, limit: int = 20) -> list[NewsArticle]:
    url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    articles = []
    cutoff = datetime.now() - timedelta(days=3)

    for entry in feed.entries[:limit]:
        pub_date = datetime(*entry.published_parsed[:6]) if entry.published_parsed else None
        if pub_date and pub_date < cutoff:
            continue
        articles.append(NewsArticle(
            ticker=ticker,
            article_hash=hashlib.md5(entry.link.encode()).hexdigest(),
            title=entry.title,
            publisher=entry.get("source", {}).get("title", "Google News"),
            url=entry.link,
            published_at=pub_date,
            summary=entry.get("summary", ""),
        ))
    return articles
```

---

## 6. Sentiment Agent (`sentiment_agent.py`)

### Current Gap

Only sends first **2,000 chars** of each YouTube transcript. That's ~10% of the content.

### Fix: Send FULL Transcripts

**Context length math** (from research):

- 15,000 chars ≈ **3,750-4,500 tokens**
- 3 transcripts × 15,000 chars = 45,000 chars ≈ **11,250-13,500 tokens**
- System prompt + news context ≈ ~2,000 tokens
- Agent response ≈ ~500 tokens
- **Total per sentiment agent call ≈ ~16,000 tokens**

**Recommended LLM context length setting**:

| LLM Provider | Recommended `num_ctx` / `max_tokens` | Why |
| ------------ | ------------------------------------- | --- |
| **Ollama** (Gemma 27B) | `num_ctx: 32768` (32K) | Standard context, 2x headroom |
| **LM Studio** | `context_length: 32768` | Same math |
| **For 5+ transcripts** | `num_ctx: 65536` (64K) | 5 × 15K = 75K chars ≈ 19K tokens + overhead |

> [!IMPORTANT]
> Set `num_ctx` to at least **32,768** in Ollama/LM Studio. Most 27B+ models support this natively.
> In Ollama: `ollama run gemma3:27b --num-ctx 32768`
> Or set via API: `"options": {"num_ctx": 32768}` in the request body.

### Updated `format_context()`

```python
# Send FULL transcripts, no truncation
for i, yt in enumerate(transcripts[:5], 1):
    sections.append(f"\n### Video {i}: [{yt.channel}] {yt.title}")
    if yt.published_at:
        sections.append(f"Published: {yt.published_at.strftime('%Y-%m-%d')}")
    if yt.raw_transcript:
        sections.append(f"Full Transcript:\n{yt.raw_transcript}")  # NO truncation
```

### Prompt Upgrade

- Source credibility weighting (Bloomberg/CNBC > random vlogger)
- Narrative momentum (is sentiment accelerating or plateauing?)
- Contrarian indicators (extreme bullishness as warning)
- Separate institutional vs retail sentiment signals
- Temporal weighting (today's news > 3-day-old news)

---

## 7. Risk Agent (`risk_agent.py`)

### Current Gap

Manually computes volatility from price history instead of using stored data. No quantitative risk metrics at all.

### Quantitative Risk Metrics to Add (Computed in Collector, Passed to Agent)

#### New: `RiskComputer` (pure math, like `TechnicalComputer`)

Create `app/collectors/risk_computer.py` with these quant functions:

```python
import numpy as np
from scipy import stats

class RiskComputer:
    """Computes quantitative risk metrics from price history."""

    def compute(self, prices: list[float], risk_free_rate: float = 0.05) -> dict:
        returns = np.diff(prices) / prices[:-1]
        daily_rf = risk_free_rate / 252

        return {
            # --- Core Risk Metrics ---
            "z_score": self._z_score(prices),
            "sharpe_ratio": self._sharpe(returns, daily_rf),
            "sortino_ratio": self._sortino(returns, daily_rf),
            "calmar_ratio": self._calmar(returns),
            "treynor_ratio": self._treynor(returns, daily_rf, market_returns),

            # --- Value at Risk ---
            "var_95": self._var(returns, 0.05),          # 5% VaR
            "var_99": self._var(returns, 0.01),          # 1% VaR
            "cvar_95": self._cvar(returns, 0.05),        # Conditional VaR (Expected Shortfall)

            # --- Drawdown ---
            "max_drawdown": self._max_drawdown(prices),
            "max_drawdown_duration": self._max_dd_duration(prices),
            "current_drawdown": self._current_drawdown(prices),

            # --- Volatility ---
            "daily_volatility": np.std(returns),
            "annualized_volatility": np.std(returns) * np.sqrt(252),
            "downside_deviation": self._downside_dev(returns),
            "volatility_skew": stats.skew(returns),
            "return_kurtosis": stats.kurtosis(returns),

            # --- Beta & Correlation ---
            "beta": self._beta(returns, market_returns),
            "alpha": self._alpha(returns, market_returns, daily_rf),
            "r_squared": self._r_squared(returns, market_returns),
            "correlation_to_spy": np.corrcoef(returns, market_returns)[0, 1],

            # --- Tail Risk ---
            "gain_to_pain_ratio": self._gain_to_pain(returns),
            "tail_ratio": self._tail_ratio(returns),
            "ulcer_index": self._ulcer_index(prices),
        }

    def _z_score(self, prices: list[float], window: int = 20) -> float:
        """How many std devs current price is from rolling mean."""
        recent = prices[-window:]
        mean = np.mean(recent)
        std = np.std(recent)
        return (prices[-1] - mean) / std if std > 0 else 0.0

    def _sharpe(self, returns, daily_rf) -> float:
        """Annualized Sharpe Ratio."""
        excess = returns - daily_rf
        if np.std(excess) == 0:
            return 0.0
        return (np.mean(excess) / np.std(excess)) * np.sqrt(252)

    def _sortino(self, returns, daily_rf) -> float:
        """Like Sharpe but only penalizes downside volatility."""
        excess = returns - daily_rf
        downside = returns[returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        return (np.mean(excess) / np.std(downside)) * np.sqrt(252)

    def _calmar(self, returns) -> float:
        """Annualized return / max drawdown."""
        ann_return = np.mean(returns) * 252
        prices = np.cumprod(1 + returns)
        max_dd = self._max_drawdown(prices)
        return ann_return / abs(max_dd) if max_dd != 0 else 0.0

    def _var(self, returns, confidence) -> float:
        """Historical Value at Risk."""
        return float(np.percentile(returns, confidence * 100))

    def _cvar(self, returns, confidence) -> float:
        """Conditional VaR (Expected Shortfall) — average of losses beyond VaR."""
        var = self._var(returns, confidence)
        return float(np.mean(returns[returns <= var]))

    def _max_drawdown(self, prices) -> float:
        """Maximum peak-to-trough decline."""
        peak = np.maximum.accumulate(prices)
        drawdown = (prices - peak) / peak
        return float(np.min(drawdown))

    def _downside_dev(self, returns) -> float:
        """Standard deviation of negative returns only."""
        downside = returns[returns < 0]
        return float(np.std(downside)) if len(downside) > 0 else 0.0

    def _beta(self, returns, market_returns) -> float:
        """Beta relative to market (SPY)."""
        cov = np.cov(returns, market_returns)
        return float(cov[0, 1] / cov[1, 1]) if cov[1, 1] != 0 else 1.0

    def _alpha(self, returns, market_returns, daily_rf) -> float:
        """Jensen's Alpha — excess return vs CAPM expected return."""
        beta = self._beta(returns, market_returns)
        ann_return = np.mean(returns) * 252
        market_ann = np.mean(market_returns) * 252
        rf_ann = daily_rf * 252
        return ann_return - (rf_ann + beta * (market_ann - rf_ann))

    def _tail_ratio(self, returns) -> float:
        """95th percentile gain / abs(5th percentile loss)."""
        right = np.percentile(returns, 95)
        left = abs(np.percentile(returns, 5))
        return float(right / left) if left != 0 else 0.0

    def _ulcer_index(self, prices) -> float:
        """Measures depth and duration of drawdowns."""
        peak = np.maximum.accumulate(prices)
        dd_pct = ((prices - peak) / peak) * 100
        return float(np.sqrt(np.mean(dd_pct ** 2)))

    def _gain_to_pain(self, returns) -> float:
        """Sum of all returns / sum of absolute negative returns."""
        total = np.sum(returns)
        pain = np.sum(np.abs(returns[returns < 0]))
        return float(total / pain) if pain != 0 else 0.0
```

### Risk Agent `format_context()` Upgrade

Pass ALL quant metrics to the LLM:

```python
risk_metrics = context.get("risk_metrics", {})
if risk_metrics:
    sections.append("## Quantitative Risk Metrics")
    sections.append(f"- Z-Score (20d): {risk_metrics['z_score']:.2f}")
    sections.append(f"- Sharpe Ratio (annualized): {risk_metrics['sharpe_ratio']:.2f}")
    sections.append(f"- Sortino Ratio: {risk_metrics['sortino_ratio']:.2f}")
    sections.append(f"- Calmar Ratio: {risk_metrics['calmar_ratio']:.2f}")
    sections.append(f"- VaR (95%): {risk_metrics['var_95']:.4f}")
    sections.append(f"- CVaR / Expected Shortfall (95%): {risk_metrics['cvar_95']:.4f}")
    sections.append(f"- Max Drawdown: {risk_metrics['max_drawdown']:.2%}")
    sections.append(f"- Current Drawdown: {risk_metrics['current_drawdown']:.2%}")
    sections.append(f"- Beta (vs SPY): {risk_metrics['beta']:.2f}")
    sections.append(f"- Alpha (annualized): {risk_metrics['alpha']:.4f}")
    sections.append(f"- R² (vs SPY): {risk_metrics['r_squared']:.2f}")
    sections.append(f"- Annualized Volatility: {risk_metrics['annualized_volatility']:.2%}")
    sections.append(f"- Downside Deviation: {risk_metrics['downside_deviation']:.4f}")
    sections.append(f"- Tail Ratio: {risk_metrics['tail_ratio']:.2f}")
    sections.append(f"- Ulcer Index: {risk_metrics['ulcer_index']:.2f}")
    sections.append(f"- Gain-to-Pain Ratio: {risk_metrics['gain_to_pain_ratio']:.2f}")
    sections.append(f"- Volatility Skew: {risk_metrics['volatility_skew']:.2f}")
    sections.append(f"- Return Kurtosis: {risk_metrics['return_kurtosis']:.2f}")
```

### Risk Agent Prompt Upgrade (`risk_assessment.md`)

- **Z-Score interpretation**: >2 = overbought risk, <-2 = oversold opportunity
- **Sharpe/Sortino thresholds**: Sharpe >1 = good, >2 = excellent, <0 = danger
- **VaR/CVaR for position sizing**: Max loss at 95% confidence → size accordingly
- **Max drawdown as survival metric**: >30% drawdown = high risk
- **Beta-adjusted exposure**: High beta + high VaR = reduce position size
- **Earnings proximity**: If <5 days to earnings, flag elevated vol risk
- **Tail risk**: Negative skew + high kurtosis = fat tail risk (crash risk)

---

## 8. YouTube Collector — 24-Hour Filter + Channel List

### Changes

1. Add `--dateafter now-1d` to yt-dlp search
2. Load curated channel list from `app/user_config/youtube_channels.json`
3. Cap at 3 videos for testing (configurable)
4. Remove the 15K char truncation (send full transcript to Sentiment Agent)

---

## New Dependencies

| Library | Purpose | Install |
| ------- | ------- | ------- |
| `feedparser` | Google News RSS | `pip install feedparser` |
| `scipy` | Z-score, VaR, statistical tests | `pip install scipy` |
| `numpy` | All quant risk computations | Already installed (pandas dep) |

---

## Context Length Requirement

### Math

| Agent | Data Size | Tokens (est.) |
| ----- | --------- | ------------- |
| **Technical** | 6 months × ~20 key indicators per day ≈ 50K chars | ~12,500 tokens |
| **Fundamental** | Full snapshot + 5 years history + analyst + insider ≈ 8K chars | ~2,000 tokens |
| **Sentiment** | 3 full transcripts (45K chars) + 20 news headlines ≈ 50K chars | ~12,500 tokens |
| **Risk** | 25 quant metrics + volatility context ≈ 5K chars | ~1,250 tokens |

**Maximum single agent call**: Sentiment agent with full transcripts ≈ **16,000 tokens** (including system prompt + response).

> [!IMPORTANT]
> **Set LLM context to 32,768 tokens minimum (32K)**
>
> - Ollama: `ollama run gemma3:27b` and set `"options": {"num_ctx": 32768}` in API calls
> - LM Studio: Set `Context Length: 32768` in model settings
> - For heavier analysis: bump to 65,536 (64K)

---

## Implementation Priority

```
1. RiskComputer (new file)      — pure math, no LLM, testable immediately
2. Technical Collector expand   — add all pandas-ta indicators
3. Technical Agent upgrade      — 6 months context + expanded prompt
4. Fundamental Collector expand — 5 new yfinance methods
5. Fundamental Agent upgrade    — feed all new data  
6. News Collector broaden       — Google News RSS + SEC EDGAR
7. Sentiment Agent full tx      — remove truncation, update prompt
8. Risk Agent upgrade           — use RiskComputer data + quant prompt
9. YouTube 24h filter           — dateafter + channel list
```
