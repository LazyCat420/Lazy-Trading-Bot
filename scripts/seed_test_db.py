#!/usr/bin/env python3
"""Seed the test database with COMPREHENSIVE data for battle-testing the full pipeline.

Usage:
    python scripts/seed_test_db.py

This creates data/trading_bot_test.duckdb with rich, realistic data across
ALL 18+ tables the pipeline reads. Every phase gets exercised:
  Discovery → Collection → Embedding → Deep Analysis → Trading

Tables seeded:
  1.  watchlist                (active ticker entry)
  2.  price_history            (90 days OHLCV with realistic volatility)
  3.  technicals               (50+ indicators per day, 30 days)
  4.  fundamentals             (full snapshot with all columns)
  5.  financial_history        (5 years revenue/income)
  6.  balance_sheet            (5 years assets/liabilities)
  7.  cash_flows               (5 years FCF/buybacks)
  8.  risk_metrics             (full risk profile)
  9.  analyst_data             (consensus ratings)
  10. insider_activity         (net buying data)
  11. earnings_calendar        (upcoming earnings)
  12. news_articles            (10 articles, mixed sentiment)
  13. news_full_articles       (3 full RSS/EDGAR articles)
  14. youtube_transcripts      (5 transcripts with real analysis text)
  15. youtube_trading_data     (3 structured trading data entries)
  16. sec_13f_holdings         (institutional ownership — 5 filers)
  17. congressional_trades     (3 congress trades)
  18. discovered_tickers       (Reddit + YouTube discovery entries)
  19. ticker_scores            (aggregate discovery score)
  20. trade_decisions          (1 prior trade for delta analysis)
  21. portfolio_snapshots      (initial $100k portfolio)
"""

import hashlib
import json
import math
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

DB_PATH = PROJECT_ROOT / "data" / "trading_bot_test.duckdb"

# ── Reproducible randomness ──────────────────────────────────────────
random.seed(42)

TICKER = "AAPL"
NOW = datetime.now()
TODAY = NOW.date()


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]


def main():
    print(f"🔨 Battle-Test Seed: {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
        print("  Removed existing test DB")

    conn = duckdb.connect(str(DB_PATH))
    from app.database import _init_tables
    _init_tables(conn)
    print("  ✅ Schema initialized\n")

    # ══════════════════════════════════════════════════════════════
    # 1. WATCHLIST — active ticker
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO watchlist (ticker, source, added_at, status, bot_id, "
        "discovery_score, sentiment_hint) VALUES (?,?,?,?,?,?,?)",
        [TICKER, "reddit+youtube", NOW - timedelta(days=3), "active", "default", 8.5, "bullish"],
    )
    print(f"  [1/21] watchlist: {TICKER} (active, score=8.5)")

    # ══════════════════════════════════════════════════════════════
    # 2. PRICE HISTORY — 90 days with realistic walk
    # ══════════════════════════════════════════════════════════════
    prices = []
    price = 172.0  # Start price 3 months ago
    for i in range(90):
        date = (NOW - timedelta(days=89 - i)).date()
        # Brownian motion with slight upward drift
        drift = 0.0005 + random.gauss(0, 0.015)
        price *= (1 + drift)
        day_range = price * random.uniform(0.005, 0.025)
        open_p = price + random.uniform(-day_range / 2, day_range / 2)
        high = max(open_p, price) + random.uniform(0, day_range)
        low = min(open_p, price) - random.uniform(0, day_range)
        close = price
        vol = random.randint(40_000_000, 120_000_000)
        prices.append((TICKER, date, round(open_p, 2), round(high, 2),
                        round(low, 2), round(close, 2), vol, round(close, 2)))
    conn.executemany(
        "INSERT INTO price_history (ticker, date, open, high, low, close, volume, adj_close) "
        "VALUES (?,?,?,?,?,?,?,?)", prices,
    )
    last_close = prices[-1][5]
    print(f"  [2/21] price_history: 90 days (${prices[0][5]:.2f} → ${last_close:.2f})")

    # ══════════════════════════════════════════════════════════════
    # 3. TECHNICALS — 30 days, 50+ indicators
    # ══════════════════════════════════════════════════════════════
    for i in range(30):
        date = (NOW - timedelta(days=29 - i)).date()
        p = prices[60 + i]  # last 30 days of price data
        close = p[5]
        sma20 = close * random.uniform(0.97, 1.03)
        sma50 = close * random.uniform(0.95, 1.05)
        sma200 = close * random.uniform(0.90, 1.10)
        bb_mid = sma20
        bb_width = close * 0.04
        atr = close * random.uniform(0.01, 0.03)
        rsi = random.uniform(30, 75)

        conn.execute(
            "INSERT INTO technicals (ticker, date, rsi, macd, macd_signal, macd_hist, "
            "sma_20, sma_50, sma_200, bb_upper, bb_middle, bb_lower, atr, "
            "stoch_k, stoch_d, ema_9, ema_21, ema_50, ema_200, "
            "cci, willr, mfi, roc, mom, adx, obv, cmf, zscore, "
            "aroon_up, aroon_down, supertrend, natr, "
            "ichi_conv, ichi_base, ichi_span_a, ichi_span_b) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                TICKER, date, rsi,
                random.uniform(-2, 3),       # macd
                random.uniform(-1, 2),       # macd_signal
                random.uniform(-1, 1),       # macd_hist
                round(sma20, 2), round(sma50, 2), round(sma200, 2),
                round(bb_mid + bb_width, 2), round(bb_mid, 2), round(bb_mid - bb_width, 2),
                round(atr, 2),
                random.uniform(20, 80),      # stoch_k
                random.uniform(20, 80),      # stoch_d
                round(close * random.uniform(0.98, 1.02), 2),  # ema_9
                round(close * random.uniform(0.97, 1.03), 2),  # ema_21
                round(sma50, 2),  # ema_50
                round(sma200, 2),  # ema_200
                random.uniform(-100, 200),   # cci
                random.uniform(-80, -20),    # willr
                random.uniform(30, 70),      # mfi
                random.uniform(-5, 8),       # roc
                random.uniform(-5, 10),      # mom
                random.uniform(15, 45),      # adx
                random.uniform(1e8, 5e8),    # obv
                random.uniform(-0.2, 0.3),   # cmf
                random.uniform(-1.5, 2.0),   # zscore
                random.uniform(40, 100),     # aroon_up
                random.uniform(0, 60),       # aroon_down
                round(close * 0.95, 2),      # supertrend
                round(atr / close * 100, 2), # natr
                round(close * 0.99, 2),      # ichi_conv
                round(close * 0.98, 2),      # ichi_base
                round(close * 1.01, 2),      # ichi_span_a
                round(close * 0.96, 2),      # ichi_span_b
            ],
        )
    print(f"  [3/21] technicals: 30 days × 36 indicators")

    # ══════════════════════════════════════════════════════════════
    # 4. FUNDAMENTALS — full snapshot
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO fundamentals (ticker, snapshot_date, market_cap, trailing_pe, "
        "forward_pe, peg_ratio, price_to_sales, price_to_book, enterprise_value, "
        "ev_to_revenue, ev_to_ebitda, profit_margin, operating_margin, "
        "return_on_assets, return_on_equity, revenue, revenue_growth, "
        "net_income, trailing_eps, total_cash, total_debt, debt_to_equity, "
        "free_cash_flow, dividend_rate, dividend_yield, payout_ratio, "
        "sector, industry, description) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            TICKER, TODAY, 2_950_000_000_000, 31.5, 28.2, 2.1,
            7.8, 48.9, 3_100_000_000_000,
            8.1, 25.4, 0.265, 0.305,
            0.31, 1.72, 394_000_000_000, 0.08,
            97_000_000_000, 6.42,
            62_000_000_000, 111_000_000_000, 1.87,
            110_000_000_000, 1.00, 0.0052, 0.155,
            "Technology", "Consumer Electronics",
            "Apple Inc. designs, manufactures, and markets smartphones, "
            "personal computers, tablets, wearables, and accessories.",
        ],
    )
    print(f"  [4/21] fundamentals: full snapshot (PE=31.5, MCap=$2.95T)")

    # ══════════════════════════════════════════════════════════════
    # 5. FINANCIAL HISTORY — 5 years P&L
    # ══════════════════════════════════════════════════════════════
    fin_data = [
        (2020, 274_500_000_000, 57_400_000_000, 0.382, 0.241, 0.209, 3.28),
        (2021, 365_800_000_000, 94_700_000_000, 0.418, 0.298, 0.259, 5.61),
        (2022, 394_300_000_000, 99_800_000_000, 0.433, 0.307, 0.253, 6.11),
        (2023, 383_300_000_000, 97_000_000_000, 0.441, 0.298, 0.253, 6.13),
        (2024, 394_000_000_000, 97_000_000_000, 0.462, 0.305, 0.265, 6.42),
    ]
    conn.executemany(
        "INSERT INTO financial_history (ticker, year, revenue, net_income, "
        "gross_margin, operating_margin, net_margin, eps) VALUES (?,?,?,?,?,?,?,?)",
        [(TICKER, *row) for row in fin_data],
    )
    print(f"  [5/21] financial_history: 5 years (2020-2024)")

    # ══════════════════════════════════════════════════════════════
    # 6. BALANCE SHEET — 5 years
    # ══════════════════════════════════════════════════════════════
    bs_data = [
        (2020, 323_900_000_000, 258_500_000_000, 65_300_000_000,
         143_700_000_000, 105_400_000_000, 1.36,
         112_400_000_000, 38_000_000_000, 38_300_000_000, 0, 65_300_000_000),
        (2021, 351_000_000_000, 287_900_000_000, 63_100_000_000,
         134_800_000_000, 125_500_000_000, 1.07,
         124_700_000_000, 35_900_000_000, 9_300_000_000, 0, 63_100_000_000),
        (2022, 352_800_000_000, 302_100_000_000, 50_700_000_000,
         135_400_000_000, 153_900_000_000, 0.88,
         120_100_000_000, 48_300_000_000, -18_500_000_000, 0, 50_700_000_000),
        (2023, 352_600_000_000, 290_400_000_000, 62_200_000_000,
         143_600_000_000, 145_300_000_000, 0.99,
         111_100_000_000, 29_900_000_000, -1_700_000_000, 0, 62_200_000_000),
        (2024, 364_900_000_000, 308_000_000_000, 56_900_000_000,
         152_900_000_000, 176_400_000_000, 0.87,
         104_600_000_000, 29_900_000_000, -23_500_000_000, 0, 56_900_000_000),
    ]
    conn.executemany(
        "INSERT INTO balance_sheet (ticker, year, total_assets, total_liabilities, "
        "stockholders_equity, current_assets, current_liabilities, current_ratio, "
        "total_debt, cash_and_equivalents, net_working_capital, goodwill, "
        "tangible_book_value) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(TICKER, *row) for row in bs_data],
    )
    print(f"  [6/21] balance_sheet: 5 years")

    # ══════════════════════════════════════════════════════════════
    # 7. CASH FLOWS — 5 years
    # ══════════════════════════════════════════════════════════════
    cf_data = [
        (2020, 80_700_000_000, -7_300_000_000, 73_400_000_000,
         -86_800_000_000, -4_300_000_000, -14_100_000_000, -72_400_000_000, -10_400_000_000),
        (2021, 104_000_000_000, -11_100_000_000, 93_000_000_000,
         -93_400_000_000, -14_500_000_000, -14_500_000_000, -85_500_000_000, -3_900_000_000),
        (2022, 122_200_000_000, -10_700_000_000, 111_400_000_000,
         -110_700_000_000, -22_400_000_000, -14_800_000_000, -89_400_000_000, -10_900_000_000),
        (2023, 110_500_000_000, -11_000_000_000, 99_600_000_000,
         -108_500_000_000, -3_700_000_000, -15_000_000_000, -77_600_000_000, -1_700_000_000),
        (2024, 118_300_000_000, -9_900_000_000, 108_400_000_000,
         -121_700_000_000, -3_800_000_000, -15_200_000_000, -94_900_000_000, -3_200_000_000),
    ]
    conn.executemany(
        "INSERT INTO cash_flows (ticker, year, operating_cashflow, capital_expenditures, "
        "free_cashflow, financing_cashflow, investing_cashflow, dividends_paid, "
        "share_buybacks, net_change_in_cash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(TICKER, *row) for row in cf_data],
    )
    print(f"  [7/21] cash_flows: 5 years (FCF $73B→$108B)")

    # ══════════════════════════════════════════════════════════════
    # 8. RISK METRICS — comprehensive risk profile
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO risk_metrics (ticker, computed_date, z_score_20, z_score_50, "
        "sharpe_ratio, sortino_ratio, calmar_ratio, treynor_ratio, var_95, var_99, "
        "cvar_95, cvar_99, max_drawdown, max_drawdown_duration_days, current_drawdown, "
        "daily_volatility, annualized_volatility, downside_deviation, volatility_skew, "
        "return_kurtosis, beta, alpha, r_squared, correlation_to_spy, "
        "gain_to_pain_ratio, tail_ratio, ulcer_index, information_ratio) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            TICKER, TODAY,
            0.85, 1.2,              # z-scores
            1.45, 2.1, 0.95, 0.12,  # risk-adjusted returns
            -0.022, -0.035,         # VaR
            -0.031, -0.048,         # CVaR
            -0.127, 15, -0.034,     # drawdown
            0.0145, 0.229,          # volatility
            0.0098, -0.35, 3.8,     # higher moments
            1.18, 0.05, 0.82, 0.89, # market beta/alpha
            2.1, 1.35, 4.2, 0.42,  # other ratios
        ],
    )
    print(f"  [8/21] risk_metrics: full profile (Sharpe=1.45, Beta=1.18)")

    # ══════════════════════════════════════════════════════════════
    # 9. ANALYST DATA — consensus ratings
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO analyst_data (ticker, snapshot_date, target_mean, target_median, "
        "target_high, target_low, num_analysts, strong_buy, buy, hold, sell, strong_sell) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [TICKER, TODAY, 210.50, 208.00, 250.00, 165.00, 42, 12, 18, 8, 3, 1],
    )
    print(f"  [9/21] analyst_data: 42 analysts, median target $208")

    # ══════════════════════════════════════════════════════════════
    # 10. INSIDER ACTIVITY — recent insider trades
    # ══════════════════════════════════════════════════════════════
    txns = json.dumps([
        {"insider": "Tim Cook", "relation": "CEO", "type": "Sale", "shares": 50000,
         "value": 9_500_000, "date": (NOW - timedelta(days=15)).strftime("%Y-%m-%d")},
        {"insider": "Luca Maestri", "relation": "CFO", "type": "Sale", "shares": 25000,
         "value": 4_750_000, "date": (NOW - timedelta(days=30)).strftime("%Y-%m-%d")},
        {"insider": "Jeff Williams", "relation": "COO", "type": "Purchase", "shares": 10000,
         "value": 1_850_000, "date": (NOW - timedelta(days=45)).strftime("%Y-%m-%d")},
    ])
    conn.execute(
        "INSERT INTO insider_activity (ticker, snapshot_date, net_insider_buying_90d, "
        "institutional_ownership_pct, raw_transactions) VALUES (?,?,?,?,?)",
        [TICKER, TODAY, -8_400_000, 0.612, txns],
    )
    print(f"  [10/21] insider_activity: 3 insider trades (net selling)")

    # ══════════════════════════════════════════════════════════════
    # 11. EARNINGS CALENDAR — upcoming event
    # ══════════════════════════════════════════════════════════════
    next_earnings = (NOW + timedelta(days=12)).date()
    conn.execute(
        "INSERT INTO earnings_calendar (ticker, snapshot_date, next_earnings_date, "
        "days_until_earnings, earnings_estimate, previous_actual, previous_estimate, "
        "surprise_pct) VALUES (?,?,?,?,?,?,?,?)",
        [TICKER, TODAY, next_earnings, 12, 1.58, 1.53, 1.46, 4.8],
    )
    print(f"  [11/21] earnings_calendar: next earnings in 12 days (est=$1.58)")

    # ══════════════════════════════════════════════════════════════
    # 12. NEWS ARTICLES — 10 articles, mixed sentiment
    # ══════════════════════════════════════════════════════════════
    articles = [
        ("Apple Vision Pro Sales Disappoint in Q4, Analysts Warn of AR Headset Headwinds",
         "Bloomberg", "Analysts report Vision Pro unit sales fell short of expectations, "
         "raising questions about Apple's AR strategy. Some see it as a long-term play "
         "while bears argue the $3,499 price point limits mass adoption."),
        ("Apple's Services Revenue Hits Record $24.2B, Driving Margin Expansion",
         "Reuters", "Apple's services segment reported record revenue of $24.2B in Q4, "
         "continuing its multi-year growth trajectory. Services now account for 26% of "
         "total revenue with significantly higher margins than hardware."),
        ("iPhone 16 Pro Max Outsells Samsung Galaxy S25 Ultra by 3:1 in US Market",
         "CNBC", "New market data shows Apple's flagship iPhone 16 Pro Max outselling "
         "Samsung's competing device 3 to 1 in the US market, demonstrating continued "
         "iOS ecosystem stickiness and brand premium."),
        ("Apple Faces DOJ Antitrust Suit Over App Store Monopoly Practices",
         "Wall Street Journal", "The Department of Justice filed an antitrust lawsuit "
         "against Apple, alleging monopolistic practices in its App Store. The case "
         "could force Apple to allow third-party payment systems, impacting services revenue."),
        ("Warren Buffett's Berkshire Hathaway Trims Apple Stake by 25%",
         "Financial Times", "Berkshire Hathaway disclosed selling approximately 25% of its "
         "Apple position in Q4, reducing its stake from $174B to $130B. Buffett cited "
         "tax planning but analysts note the position remains Berkshire's largest equity holding."),
        ("Apple Intelligence Drives Strong iPad Pro and MacBook Demand",
         "TechCrunch", "Apple's AI features, branded as 'Apple Intelligence,' are driving "
         "strong upgrade cycles for iPad Pro and MacBook. CEO Tim Cook said AI-related "
         "features will be 'the single most important catalyst' for hardware sales in 2025."),
        ("Foxconn Reports 22% Revenue Jump Amid Strong Apple iPhone Orders",
         "Nikkei", "Foxconn, Apple's primary manufacturing partner, reported a 22% jump "
         "in quarterly revenue, signaling strong iPhone production volumes ahead of the "
         "holiday season."),
        ("Apple Stock Hits All-Time High as S&P 500 Weight Reaches 7.2%",
         "MarketWatch", "Apple shares touched a new all-time high, pushing the company's "
         "weighting in the S&P 500 to 7.2%. Some portfolio managers express concern about "
         "concentration risk in passive index funds."),
        ("EU Digital Markets Act Forces Apple to Open iOS to Third-Party App Stores",
         "The Verge", "Under the EU's DRA rules, Apple must allow third-party app stores "
         "on iOS devices sold in Europe. Apple has complied but imposed a 'Core Technology Fee' "
         "that critics say undermines the spirit of the regulation."),
        ("Apple's India Manufacturing Push: 25% of iPhones Now Made Locally",
         "Economic Times", "Apple now manufactures 25% of its iPhones in India, up from "
         "7% two years ago. The supply chain diversification reduces China concentration risk "
         "and qualifies for India's production-linked incentive scheme."),
    ]
    for idx, (title, publisher, summary) in enumerate(articles):
        conn.execute(
            "INSERT INTO news_articles (ticker, article_hash, title, publisher, url, "
            "published_at, summary, source) VALUES (?,?,?,?,?,?,?,?)",
            [
                TICKER, _hash(title), title, publisher,
                f"https://example.com/aapl-{idx}",
                NOW - timedelta(hours=idx * 8 + 1),
                summary, random.choice(["yfinance", "rss"]),
            ],
        )
    print(f"  [12/21] news_articles: 10 articles (mixed bull/bear/neutral)")

    # ══════════════════════════════════════════════════════════════
    # 13. NEWS FULL ARTICLES — 3 deep pieces from RSS/EDGAR
    # ══════════════════════════════════════════════════════════════
    full_articles = [
        ("Apple's Path to $4 Trillion: Services, AI, and India",
         "Barron's",
         "Apple Inc. ($AAPL) is uniquely positioned to reach a $4 trillion market cap "
         "within the next 12 months, driven by three key catalysts:\n\n"
         "1. SERVICES FLYWHEEL: With 1.1 billion paid subscriptions and growing, Apple's "
         "services revenue is approaching $100B annually at ~72% gross margins. This recurring "
         "revenue stream provides predictability that hardware alone cannot.\n\n"
         "2. AI INTEGRATION: Apple Intelligence represents the largest forced upgrade cycle "
         "since the iPhone 6. Device-level AI requires the A17 Pro chip or newer, creating "
         "a natural replacement cycle for 800M+ older devices.\n\n"
         "3. INDIA EXPANSION: Manufacturing 25% of iPhones in India reduces geopolitical risk "
         "and opens the world's fastest-growing smartphone market. Apple's India revenue grew "
         "46% YoY versus flat China growth.\n\n"
         "RISKS: The DOJ antitrust suit could force App Store changes impacting $22B in "
         "annual commission revenue. Vision Pro's slow adoption suggests AR is still a "
         "multi-year bet rather than an immediate growth driver."),
        ("Technical Analysis: Apple Tests Key Support at 200-Day Moving Average",
         "Investor's Business Daily",
         "$AAPL pulled back to its 200-day moving average this week, a critical technical "
         "level that has held as support in 7 of the last 8 tests over the past 2 years.\n\n"
         "The stock shows an RS Rating of 85/99, meaning it outperforms 85% of all stocks.\n"
         "Volume patterns show accumulation: 4 weeks of above-average volume on up days vs "
         "2 weeks on down days.\n\n"
         "The MACD histogram is turning positive after a 3-week bearish crossover, suggesting "
         "momentum is shifting back to the bulls. RSI at 48 leaves room for upside before "
         "hitting overbought territory."),
        ("SEC 13F Analysis: Institutional Ownership Trends for Apple Inc.",
         "SEC Filings Monitor",
         "Analysis of Q4 2024 13F filings reveals shifting institutional positioning in $AAPL:\n\n"
         "NEW POSITIONS: 47 funds initiated new positions totaling 12.3M shares ($2.3B)\n"
         "INCREASED: 234 funds added to existing positions (+89.5M shares)\n"
         "DECREASED: 189 funds trimmed positions (-67.2M shares)\n"
         "ELIMINATED: 31 funds fully exited positions (-18.1M shares)\n\n"
         "NET BUYING: +16.5M shares ($3.1B) — modestly bullish institutional sentiment.\n"
         "Notable: Berkshire Hathaway reduced by 100M shares but remains largest holder."),
    ]
    for idx, (title, publisher, content) in enumerate(full_articles):
        conn.execute(
            "INSERT INTO news_full_articles (article_hash, title, url, publisher, "
            "published_at, summary, content, content_length, tickers_found, source_feed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                _hash(title), title, f"https://example.com/deep-{idx}",
                publisher, NOW - timedelta(days=idx + 1),
                content[:200], content, len(content),
                "AAPL", "rss_finance",
            ],
        )
    print(f"  [13/21] news_full_articles: 3 deep analysis pieces")

    # ══════════════════════════════════════════════════════════════
    # 14. YOUTUBE TRANSCRIPTS — 5 transcripts
    # ══════════════════════════════════════════════════════════════
    yt_transcripts = [
        ("vid_001", "AAPL Stock Deep Dive: Why I'm Loading Up Before Earnings",
         "StockMoe", 1200,
         "Let's talk about Apple. I've been adding to my position aggressively. "
         "The services growth is insane - $24 billion in a single quarter. "
         "The AI play with Apple Intelligence could drive the biggest upgrade "
         "cycle we've seen since the iPhone 6 days. Entry price target $185, "
         "stop loss at $170, take profit at $220. Risk-reward is excellent."),
        ("vid_002", "Is Apple Overvalued? Bear Case for AAPL at 31x PE",
         "The Bear Den", 900,
         "Everyone's hyped about Apple but let's look at the math. At 31x trailing PE "
         "and single-digit revenue growth, the PEG ratio is 2.1 - that's expensive. "
         "iPhone unit growth is flat. Vision Pro is a flop. The DOJ antitrust case could "
         "hit services revenue hard. I'm staying on the sidelines."),
        ("vid_003", "Apple's Secret Weapon: India Manufacturing and Services Moat",
         "CompoundingCapital", 1500,
         "Three massive developments investors are sleeping on: First, Apple's India "
         "manufacturing has gone from 7% to 25% of iPhones in just two years. "
         "This is a game-changer for geopolitical risk. Second, the services ecosystem "
         "has 1.1 billion paid subs - that's a moat wider than the Grand Canyon. "
         "Third, Apple Intelligence creates a hardware upgrade supercycle."),
        ("vid_004", "AAPL Technical Analysis: Cup and Handle Setting Up?",
         "TraderTV", 600,
         "Looking at the daily chart, Apple is forming what appears to be a cup and handle "
         "pattern with a potential breakout above $195. The 50-day SMA just crossed above "
         "the 200-day SMA - that's a golden cross. Volume is confirming - accumulation days "
         "outnumber distribution 3 to 1 over the past month. Target: $215."),
        ("vid_005", "My $500K Apple Position: Full Portfolio Update March 2025",
         "FinanceGuy", 1800,
         "Detailed walkthrough of my concentrated Apple position. Holding 2,600 shares "
         "at $183 average. Conviction thesis: (1) Services will compound at 15%+ for 5 years, "
         "(2) Apple Intelligence drives upgrade supercycle, (3) capital returns via buybacks "
         "reduce share count by 3-4% annually. My DCF model gives fair value of $225 "
         "using 10% discount rate. Position sizing: 40% of portfolio."),
    ]
    for vid_id, title, channel, duration, transcript in yt_transcripts:
        conn.execute(
            "INSERT INTO youtube_transcripts (ticker, video_id, title, channel, "
            "published_at, duration_seconds, raw_transcript, collected_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [TICKER, vid_id, title, channel,
             NOW - timedelta(days=random.randint(1, 14)),
             duration, transcript, NOW],
        )
    print(f"  [14/21] youtube_transcripts: 5 transcripts (bull/bear/technical)")

    # ══════════════════════════════════════════════════════════════
    # 15. YOUTUBE TRADING DATA — structured trade signals
    # ══════════════════════════════════════════════════════════════
    yt_trades = [
        ("vid_001", "AAPL Stock Deep Dive: Why I'm Loading Up",
         "StockMoe", json.dumps({
             "action": "BUY", "entry": 185, "stop_loss": 170,
             "take_profit": 220, "position_size": "10%",
             "thesis": "Services growth + AI upgrade cycle",
             "risk_reward": "2.3:1", "timeframe": "6-12 months",
         })),
        ("vid_004", "AAPL Technical Analysis: Cup and Handle",
         "TraderTV", json.dumps({
             "action": "BUY", "entry": 195, "stop_loss": 185,
             "take_profit": 215, "position_size": "5%",
             "pattern": "Cup and Handle breakout",
             "risk_reward": "2.0:1", "timeframe": "2-4 weeks",
         })),
        ("vid_005", "My $500K Apple Position",
         "FinanceGuy", json.dumps({
             "action": "HOLD", "current_position": "2600 shares @ $183",
             "dcf_fair_value": 225, "conviction": "HIGH",
             "thesis": "Services compounding + buybacks + AI",
         })),
    ]
    for vid_id, title, channel, data in yt_trades:
        conn.execute(
            "INSERT INTO youtube_trading_data (ticker, video_id, title, channel, "
            "trading_data, collected_at) VALUES (?,?,?,?,?,?)",
            [TICKER, vid_id, title, channel, data, NOW],
        )
    print(f"  [15/21] youtube_trading_data: 3 structured trade signals")

    # ══════════════════════════════════════════════════════════════
    # 16. SEC 13F HOLDINGS — 5 institutional filers
    # ══════════════════════════════════════════════════════════════
    filers = [
        ("0001067983", "Berkshire Hathaway", 400_000_000, 130_000_000_000),
        ("0001166559", "Vanguard Group", 1_300_000_000, 245_000_000_000),
        ("0000036405", "BlackRock", 1_100_000_000, 207_000_000_000),
        ("0001363508", "State Street", 600_000_000, 113_000_000_000),
        ("0001418135", "Fidelity", 350_000_000, 66_000_000_000),
    ]
    for cik, name, shares, value in filers:
        conn.execute(
            "INSERT INTO sec_13f_holdings (cik, ticker, name_of_issuer, value_usd, "
            "shares, share_type, filing_quarter, filing_date) VALUES (?,?,?,?,?,?,?,?)",
            [cik, TICKER, name, value, shares, "SH", "2024-Q4",
             (NOW - timedelta(days=45)).date()],
        )
    print(f"  [16/21] sec_13f_holdings: 5 institutional filers ($761B total)")

    # ══════════════════════════════════════════════════════════════
    # 17. CONGRESSIONAL TRADES — 3 trades
    # ══════════════════════════════════════════════════════════════
    congress = [
        (str(uuid.uuid4())[:8], "Nancy Pelosi", "House", "purchase",
         (NOW - timedelta(days=20)).date(), (NOW - timedelta(days=10)).date(),
         "$1,000,001 - $5,000,000"),
        (str(uuid.uuid4())[:8], "Dan Crenshaw", "House", "sale_full",
         (NOW - timedelta(days=35)).date(), (NOW - timedelta(days=25)).date(),
         "$15,001 - $50,000"),
        (str(uuid.uuid4())[:8], "Tommy Tuberville", "Senate", "purchase",
         (NOW - timedelta(days=50)).date(), (NOW - timedelta(days=40)).date(),
         "$100,001 - $250,000"),
    ]
    for id_, name, chamber, tx_type, tx_date, filed_date, amount in congress:
        conn.execute(
            "INSERT INTO congressional_trades (id, member_name, chamber, ticker, "
            "tx_type, tx_date, filed_date, amount_range) VALUES (?,?,?,?,?,?,?,?)",
            [id_, name, chamber, TICKER, tx_type, tx_date, filed_date, amount],
        )
    print(f"  [17/21] congressional_trades: 3 trades (2 buys, 1 sell)")

    # ══════════════════════════════════════════════════════════════
    # 18. DISCOVERED TICKERS — Reddit + YouTube mentions
    # ══════════════════════════════════════════════════════════════
    discoveries = [
        ("reddit_wallstreetbets", "r/wallstreetbets", 9.2, "bullish",
         "AAPL calls printing after earnings beat. Services segment is insane."),
        ("reddit_stocks", "r/stocks", 7.5, "bullish",
         "Bought 100 shares of AAPL at $183. Long-term hold thesis still intact."),
        ("reddit_investing", "r/investing", 6.0, "neutral",
         "Apple is a great company but at 30x PE, where's the upside?"),
        ("youtube_stockmoe", "youtube/StockMoe", 8.8, "bullish",
         "Deep dive on AAPL — services moat + AI upgrade super-cycle incoming."),
        ("youtube_bearfactory", "youtube/BearDen", 3.2, "bearish",
         "AAPL is overvalued. Vision Pro flop. DOJ antitrust risk."),
    ]
    for source, detail, score, sentiment, snippet in discoveries:
        conn.execute(
            "INSERT INTO discovered_tickers (ticker, source, source_detail, "
            "discovery_score, sentiment_hint, context_snippet, discovered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [TICKER, source, detail, score, sentiment, snippet,
             NOW - timedelta(hours=random.randint(1, 72))],
        )
    print(f"  [18/21] discovered_tickers: 5 mentions (3 Reddit, 2 YouTube)")

    # ══════════════════════════════════════════════════════════════
    # 19. TICKER SCORES — aggregate discovery score
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO ticker_scores (ticker, total_score, youtube_score, reddit_score, "
        "mention_count, first_seen, last_seen, sentiment_hint, is_validated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [TICKER, 8.5, 6.0, 7.5, 5,
         NOW - timedelta(days=3), NOW - timedelta(hours=2), "bullish", True],
    )
    print(f"  [19/21] ticker_scores: aggregate score=8.5, 5 mentions")

    # ══════════════════════════════════════════════════════════════
    # 20. TRADE DECISIONS — 1 prior decision for delta analysis
    # ══════════════════════════════════════════════════════════════
    old_decision_ts = NOW - timedelta(days=5)
    conn.execute(
        "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, confidence, "
        "rationale, risk_level, time_horizon, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            str(uuid.uuid4())[:8], "default", TICKER, old_decision_ts,
            "HOLD", 0.55,
            "Apple's services growth is strong but PE at 31x limits upside. "
            "Waiting for pullback to $180 support for better entry.",
            "MED", "SWING", "logged",
        ],
    )
    print(f"  [20/21] trade_decisions: 1 prior HOLD decision (5 days ago)")

    # ══════════════════════════════════════════════════════════════
    # 21. PORTFOLIO SNAPSHOTS — $100k starting portfolio
    # ══════════════════════════════════════════════════════════════
    conn.execute(
        "INSERT INTO portfolio_snapshots (timestamp, cash_balance, "
        "total_positions_value, total_portfolio_value, bot_id) VALUES (?,?,?,?,?)",
        [NOW, 100_000.0, 0.0, 100_000.0, "default"],
    )
    print(f"  [21/21] portfolio_snapshots: $100,000 cash")

    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"✅ Battle-test database seeded at: {DB_PATH}")
    print(f"   Ticker: {TICKER}")
    print(f"   Price range: 90 days (${prices[0][5]:.2f} → ${last_close:.2f})")
    print(f"   Data sources: 21 tables populated")
    print(f"   News: 10 articles + 3 deep analysis pieces")
    print(f"   YouTube: 5 transcripts + 3 trade signals")
    print(f"   Financials: 5 years P&L, balance sheet, cash flows")
    print(f"   Smart money: 5 institutions + 3 congress trades")
    print(f"   Prior decisions: 1 HOLD (for delta analysis)")
    print(f"{'='*60}")
    print(f"\n   Switch to it:")
    print(f"   curl -X POST http://localhost:8000/api/settings/db-profile \\")
    print(f"     -H 'Content-Type: application/json' \\")
    print(f"     -d '{{\"profile\": \"test\"}}'")


if __name__ == "__main__":
    main()
