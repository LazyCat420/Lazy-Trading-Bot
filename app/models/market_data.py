"""Market data models — OHLCV, fundamentals, financial history, technicals, news."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class OHLCVRow(BaseModel):
    """Single candlestick / price row."""

    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float | None = None


class FundamentalSnapshot(BaseModel):
    """Point-in-time snapshot of company fundamentals from yfinance .info."""

    ticker: str
    snapshot_date: date

    # Valuation
    market_cap: float = 0.0
    trailing_pe: float = 0.0
    forward_pe: float = 0.0
    peg_ratio: float = 0.0
    price_to_sales: float = 0.0
    price_to_book: float = 0.0
    enterprise_value: float = 0.0
    ev_to_revenue: float = 0.0
    ev_to_ebitda: float = 0.0

    # Financials
    profit_margin: float = 0.0
    operating_margin: float = 0.0
    return_on_assets: float = 0.0
    return_on_equity: float = 0.0
    revenue: float = 0.0
    revenue_growth: float = 0.0
    net_income: float = 0.0
    trailing_eps: float = 0.0
    total_cash: float = 0.0
    total_debt: float = 0.0
    debt_to_equity: float = 0.0
    free_cash_flow: float = 0.0

    # Dividends
    dividend_rate: float = 0.0
    dividend_yield: float = 0.0
    payout_ratio: float = 0.0

    # Profile
    sector: str = ""
    industry: str = ""
    description: str = ""


class FinancialHistoryRow(BaseModel):
    """One year of income statement data."""

    ticker: str
    year: int
    revenue: float = 0.0
    net_income: float = 0.0
    gross_margin: float = 0.0
    operating_margin: float = 0.0
    net_margin: float = 0.0
    eps: float = 0.0


# ---- Phase 8: New fundamental models ----


class BalanceSheetRow(BaseModel):
    """One year of balance sheet data."""

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
    """One year of cash flow statement data."""

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
    """Analyst price targets and recommendation counts."""

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
    """Summary of insider trading activity."""

    ticker: str
    snapshot_date: date
    net_insider_buying_90d: float = 0.0  # positive = net buying
    institutional_ownership_pct: float = 0.0
    raw_transactions_json: str = "[]"  # JSON array of transactions


class EarningsCalendar(BaseModel):
    """Upcoming and recent earnings data."""

    ticker: str
    snapshot_date: date
    next_earnings_date: date | None = None
    days_until_earnings: int | None = None
    earnings_estimate: float | None = None
    previous_actual: float | None = None
    previous_estimate: float | None = None
    surprise_pct: float | None = None


# ---- Expanded Technical Row ----


class TechnicalRow(BaseModel):
    """Computed technical indicators for a single date.

    Contains key named indicator fields plus a JSON blob of ALL computed
    indicators from pandas-ta (154 total).
    """

    ticker: str
    date: date

    # Original core indicators
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    atr: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None

    # EMAs
    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None

    # Momentum
    cci: float | None = None
    willr: float | None = None
    mfi: float | None = None
    roc: float | None = None
    mom: float | None = None
    ao: float | None = None
    tsi: float | None = None
    uo: float | None = None
    stochrsi_k: float | None = None

    # Trend
    adx: float | None = None
    adx_dmp: float | None = None
    adx_dmn: float | None = None
    aroon_up: float | None = None
    aroon_down: float | None = None
    aroon_osc: float | None = None
    supertrend: float | None = None
    psar: float | None = None
    chop: float | None = None
    vortex_pos: float | None = None
    vortex_neg: float | None = None

    # Volatility
    natr: float | None = None
    true_range: float | None = None
    donchian_upper: float | None = None
    donchian_lower: float | None = None
    donchian_mid: float | None = None
    kc_upper: float | None = None
    kc_lower: float | None = None

    # Volume
    obv: float | None = None
    ad: float | None = None
    cmf: float | None = None
    efi: float | None = None
    pvt: float | None = None

    # Statistics
    zscore: float | None = None
    skew: float | None = None
    kurtosis: float | None = None
    entropy: float | None = None

    # Ichimoku
    ichi_conv: float | None = None
    ichi_base: float | None = None
    ichi_span_a: float | None = None
    ichi_span_b: float | None = None

    # Fibonacci retracement
    fib_0: float | None = None
    fib_236: float | None = None
    fib_382: float | None = None
    fib_500: float | None = None
    fib_618: float | None = None
    fib_786: float | None = None
    fib_1: float | None = None

    # Full JSON of ALL 154 indicator columns for this date
    all_indicators_json: str | None = None


class NewsArticle(BaseModel):
    """A single news article."""

    ticker: str
    article_hash: str
    title: str
    publisher: str = ""
    url: str = ""
    published_at: datetime | None = None
    summary: str = ""
    thumbnail_url: str = ""
    source: str = "yfinance"  # Track which collector got the article


class YouTubeTranscript(BaseModel):
    """A single YouTube video transcript."""

    ticker: str
    video_id: str
    title: str = ""
    channel: str = ""
    published_at: datetime | None = None
    duration_seconds: int = 0
    raw_transcript: str = ""
