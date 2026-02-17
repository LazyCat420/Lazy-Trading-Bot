"""yFinance collector — fetches price, fundamentals, financials, balance sheet,
cash flow, analyst data, insider activity, and earnings calendar.

Optimisations (Feb 2026):
  • Ticker-object cache — one yf.Ticker per symbol per run
  • Daily guards on EVERY method — skip yfinance calls when data was already
    collected today
  • Batch DuckDB inserts via executemany()
  • Retry-with-backoff decorator — handles Yahoo 429 rate-limits
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from functools import wraps
from typing import Any, Callable, TypeVar

import pandas as pd
import yfinance as yf

from app.database import get_db
from app.models.market_data import (
    AnalystData,
    BalanceSheetRow,
    CashFlowRow,
    EarningsCalendar,
    FundamentalSnapshot,
    FinancialHistoryRow,
    InsiderSummary,
    OHLCVRow,
)
from app.utils.logger import logger

# ---------------------------------------------------------------------------
# Retry decorator — exponential backoff on Yahoo rate-limits
# ---------------------------------------------------------------------------
F = TypeVar("F", bound=Callable[..., Any])


def _retry_on_rate_limit(max_retries: int = 3, base_delay: float = 2.0):
    """Retry an async method when Yahoo Finance returns a rate-limit error."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    err_str = str(exc).lower()
                    is_rate_limit = (
                        "429" in err_str
                        or "too many requests" in err_str
                        or "rate" in err_str
                    )
                    if is_rate_limit and attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Rate-limited on %s (attempt %d/%d), retrying in %.1fs …",
                            func.__name__, attempt + 1, max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                        last_exc = exc
                    else:
                        raise
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class YFinanceCollector:
    """Collects all data available from yfinance for a single ticker."""

    # ------------------------------------------------------------------
    # Ticker cache — avoid creating a new yf.Ticker per method call
    # ------------------------------------------------------------------
    _ticker_cache: dict[str, yf.Ticker] = {}

    @classmethod
    def _get_ticker(cls, symbol: str) -> yf.Ticker:
        """Return a cached yf.Ticker, creating one on first access."""
        if symbol not in cls._ticker_cache:
            cls._ticker_cache[symbol] = yf.Ticker(symbol)
        return cls._ticker_cache[symbol]

    @classmethod
    def clear_cache(cls, symbol: str | None = None) -> None:
        """Drop cached Ticker(s).  Call between pipeline runs if desired."""
        if symbol:
            cls._ticker_cache.pop(symbol, None)
        else:
            cls._ticker_cache.clear()

    # ------------------------------------------------------------------
    # Step 1: Price History & OHLCV
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_price_history(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> list[OHLCVRow]:
        """Fetch OHLCV candles and persist to DuckDB.

        Returns the list of rows inserted/updated.
        """
        logger.info("Collecting price history for %s (period=%s)", ticker, period)

        # Daily guard — skip if we already have today's data
        db = get_db()
        today = date.today()
        existing = db.execute(
            "SELECT COUNT(*) FROM price_history WHERE ticker = ? AND date = ?",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info("Price history for %s already collected today, skipping yfinance call", ticker)
            rows_raw = db.execute(
                "SELECT ticker, date, open, high, low, close, volume, adj_close "
                "FROM price_history WHERE ticker = ? ORDER BY date",
                [ticker],
            ).fetchall()
            return [
                OHLCVRow(
                    ticker=r[0], date=r[1], open=r[2], high=r[3],
                    low=r[4], close=r[5], volume=r[6], adj_close=r[7],
                )
                for r in rows_raw
            ]

        t = self._get_ticker(ticker)
        df: pd.DataFrame = t.history(period=period, interval=interval)

        if df.empty:
            logger.warning("No price data returned for %s", ticker)
            return []

        rows: list[OHLCVRow] = []
        for idx, row in df.iterrows():
            dt = idx.date() if hasattr(idx, "date") else idx
            rows.append(
                OHLCVRow(
                    ticker=ticker,
                    date=dt,
                    open=round(float(row["Open"]), 4),
                    high=round(float(row["High"]), 4),
                    low=round(float(row["Low"]), 4),
                    close=round(float(row["Close"]), 4),
                    volume=int(row["Volume"]),
                    adj_close=round(float(row.get("Adj Close", row["Close"])), 4),
                )
            )

        # Persist — batch insert
        db.executemany(
            """
            INSERT OR REPLACE INTO price_history
                (ticker, date, open, high, low, close, volume, adj_close)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [r.ticker, r.date, r.open, r.high, r.low, r.close, r.volume, r.adj_close]
                for r in rows
            ],
        )

        logger.info("Stored %d price rows for %s", len(rows), ticker)
        return rows

    # ------------------------------------------------------------------
    # Step 2: Fundamental Data
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_fundamentals(self, ticker: str) -> FundamentalSnapshot:
        """Fetch ticker .info and persist a daily snapshot."""
        logger.info("Collecting fundamentals for %s", ticker)

        # Daily guard — skip if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            "SELECT 1 FROM fundamentals WHERE ticker = ? AND snapshot_date = ?",
            [ticker, today],
        ).fetchone()
        if existing:
            logger.info("Fundamentals for %s already collected today, skipping", ticker)
            row = db.execute(
                "SELECT * FROM fundamentals WHERE ticker = ? AND snapshot_date = ?",
                [ticker, today],
            ).fetchone()
            cols = [desc[0] for desc in db.description]
            row_dict = dict(zip(cols, row))
            return FundamentalSnapshot(**row_dict)

        t = self._get_ticker(ticker)
        info: dict = t.info or {}

        def safe_float(key: str) -> float:
            val = info.get(key)
            if val is None:
                return 0.0
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        snap = FundamentalSnapshot(
            ticker=ticker,
            snapshot_date=today,
            market_cap=safe_float("marketCap"),
            trailing_pe=safe_float("trailingPE"),
            forward_pe=safe_float("forwardPE"),
            peg_ratio=safe_float("pegRatio"),
            price_to_sales=safe_float("priceToSalesTrailing12Months"),
            price_to_book=safe_float("priceToBook"),
            enterprise_value=safe_float("enterpriseValue"),
            ev_to_revenue=safe_float("enterpriseToRevenue"),
            ev_to_ebitda=safe_float("enterpriseToEbitda"),
            profit_margin=safe_float("profitMargins"),
            operating_margin=safe_float("operatingMargins"),
            return_on_assets=safe_float("returnOnAssets"),
            return_on_equity=safe_float("returnOnEquity"),
            revenue=safe_float("totalRevenue"),
            revenue_growth=safe_float("revenueGrowth"),
            net_income=safe_float("netIncomeToCommon"),
            trailing_eps=safe_float("trailingEps"),
            total_cash=safe_float("totalCash"),
            total_debt=safe_float("totalDebt"),
            debt_to_equity=safe_float("debtToEquity"),
            free_cash_flow=safe_float("freeCashflow"),
            dividend_rate=safe_float("dividendRate"),
            dividend_yield=safe_float("dividendYield"),
            payout_ratio=safe_float("payoutRatio"),
            sector=info.get("sector", ""),
            industry=info.get("industry", ""),
            description=info.get("longBusinessSummary", ""),
        )

        # Persist
        db.execute(
            """
            INSERT OR REPLACE INTO fundamentals
                (ticker, snapshot_date, market_cap, trailing_pe, forward_pe,
                 peg_ratio, price_to_sales, price_to_book, enterprise_value,
                 ev_to_revenue, ev_to_ebitda, profit_margin, operating_margin,
                 return_on_assets, return_on_equity, revenue, revenue_growth,
                 net_income, trailing_eps, total_cash, total_debt, debt_to_equity,
                 free_cash_flow, dividend_rate, dividend_yield, payout_ratio,
                 sector, industry, description, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snap.ticker, snap.snapshot_date, snap.market_cap, snap.trailing_pe,
                snap.forward_pe, snap.peg_ratio, snap.price_to_sales,
                snap.price_to_book, snap.enterprise_value, snap.ev_to_revenue,
                snap.ev_to_ebitda, snap.profit_margin, snap.operating_margin,
                snap.return_on_assets, snap.return_on_equity, snap.revenue,
                snap.revenue_growth, snap.net_income, snap.trailing_eps,
                snap.total_cash, snap.total_debt, snap.debt_to_equity,
                snap.free_cash_flow, snap.dividend_rate, snap.dividend_yield,
                snap.payout_ratio, snap.sector, snap.industry, snap.description,
                str(info),
            ],
        )

        logger.info("Stored fundamentals snapshot for %s", ticker)
        return snap

    # ------------------------------------------------------------------
    # Step 3: Financial History (Multi-Year Income Statement)
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_financial_history(self, ticker: str) -> list[FinancialHistoryRow]:
        """Fetch multi-year income statement data from yfinance."""
        logger.info("Collecting financial history for %s", ticker)

        # Daily guard — skip if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            "SELECT COUNT(*) FROM financial_history WHERE ticker = ? "
            "AND year = EXTRACT(YEAR FROM CAST(? AS DATE))",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info("Financial history for %s already has current-year data, skipping", ticker)
            rows_raw = db.execute(
                "SELECT ticker, year, revenue, net_income, gross_margin, "
                "operating_margin, net_margin, eps "
                "FROM financial_history WHERE ticker = ? ORDER BY year",
                [ticker],
            ).fetchall()
            return [
                FinancialHistoryRow(
                    ticker=r[0], year=r[1], revenue=r[2], net_income=r[3],
                    gross_margin=r[4], operating_margin=r[5], net_margin=r[6], eps=r[7],
                )
                for r in rows_raw
            ]

        t = self._get_ticker(ticker)
        fin: pd.DataFrame = t.financials

        if fin is None or fin.empty:
            logger.warning("No financial history returned for %s", ticker)
            return []

        # Sort columns (dates) ascending
        fin = fin[sorted(fin.columns)]
        rows: list[FinancialHistoryRow] = []

        def get_val(keys: list[str], col: object) -> float:
            for k in keys:
                if k in fin.index:
                    val = fin.loc[k, col]
                    if pd.notnull(val):
                        return float(val)
            return 0.0

        for date_col in fin.columns:
            year = date_col.year if hasattr(date_col, "year") else int(date_col)
            rev = get_val(["Total Revenue", "Revenue"], date_col)
            net_inc = get_val(["Net Income", "Net Income Common Stockholders"], date_col)
            gross = get_val(["Gross Profit"], date_col)
            op_inc = get_val(["Operating Income", "EBIT"], date_col)

            rows.append(
                FinancialHistoryRow(
                    ticker=ticker,
                    year=year,
                    revenue=rev,
                    net_income=net_inc,
                    gross_margin=round(gross / rev, 4) if rev else 0.0,
                    operating_margin=round(op_inc / rev, 4) if rev else 0.0,
                    net_margin=round(net_inc / rev, 4) if rev else 0.0,
                    eps=get_val(["Basic EPS", "Diluted EPS"], date_col),
                )
            )

        # Persist — batch insert
        db.executemany(
            """
            INSERT OR REPLACE INTO financial_history
                (ticker, year, revenue, net_income, gross_margin,
                 operating_margin, net_margin, eps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [r.ticker, r.year, r.revenue, r.net_income, r.gross_margin,
                 r.operating_margin, r.net_margin, r.eps]
                for r in rows
            ],
        )

        logger.info("Stored %d years of financial history for %s", len(rows), ticker)
        return rows

    # ==================================================================
    # Phase 8: New Collector Methods
    # ==================================================================

    # ------------------------------------------------------------------
    # Step 4: Balance Sheet (Multi-Year)
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_balance_sheet(self, ticker: str) -> list[BalanceSheetRow]:
        """Fetch multi-year balance sheet data from yfinance."""
        logger.info("Collecting balance sheet for %s", ticker)

        # Daily guard — skip if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            "SELECT COUNT(*) FROM balance_sheet WHERE ticker = ? "
            "AND year = EXTRACT(YEAR FROM CAST(? AS DATE))",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info("Balance sheet for %s already has current-year data, skipping", ticker)
            rows_raw = db.execute(
                "SELECT ticker, year, total_assets, total_liabilities, "
                "stockholders_equity, current_assets, current_liabilities, "
                "current_ratio, total_debt, cash_and_equivalents, "
                "net_working_capital, goodwill, tangible_book_value "
                "FROM balance_sheet WHERE ticker = ? ORDER BY year",
                [ticker],
            ).fetchall()
            return [
                BalanceSheetRow(
                    ticker=r[0], year=r[1], total_assets=r[2],
                    total_liabilities=r[3], stockholders_equity=r[4],
                    current_assets=r[5], current_liabilities=r[6],
                    current_ratio=r[7], total_debt=r[8],
                    cash_and_equivalents=r[9], net_working_capital=r[10],
                    goodwill=r[11], tangible_book_value=r[12],
                )
                for r in rows_raw
            ]

        t = self._get_ticker(ticker)
        bs: pd.DataFrame = t.balance_sheet

        if bs is None or bs.empty:
            logger.warning("No balance sheet data returned for %s", ticker)
            return []

        bs = bs[sorted(bs.columns)]
        rows: list[BalanceSheetRow] = []

        def get_val(keys: list[str], col: object) -> float:
            for k in keys:
                if k in bs.index:
                    val = bs.loc[k, col]
                    if pd.notnull(val):
                        return float(val)
            return 0.0

        for date_col in bs.columns:
            year = date_col.year if hasattr(date_col, "year") else int(date_col)

            total_assets = get_val(["Total Assets"], date_col)
            total_liab = get_val(
                ["Total Liabilities Net Minority Interest", "Total Liab"], date_col
            )
            equity = get_val(
                ["Stockholders Equity", "Total Stockholders Equity",
                 "Common Stock Equity"], date_col
            )
            current_assets = get_val(["Current Assets", "Total Current Assets"], date_col)
            current_liab = get_val(
                ["Current Liabilities", "Total Current Liabilities"], date_col
            )
            total_debt = get_val(["Total Debt"], date_col)
            cash = get_val(
                ["Cash And Cash Equivalents", "Cash Financial",
                 "Cash Cash Equivalents And Short Term Investments"], date_col
            )
            goodwill = get_val(["Goodwill"], date_col)

            current_ratio = (
                round(current_assets / current_liab, 4) if current_liab else 0.0
            )
            nwc = current_assets - current_liab
            tangible_bv = equity - goodwill

            rows.append(
                BalanceSheetRow(
                    ticker=ticker,
                    year=year,
                    total_assets=total_assets,
                    total_liabilities=total_liab,
                    stockholders_equity=equity,
                    current_assets=current_assets,
                    current_liabilities=current_liab,
                    current_ratio=current_ratio,
                    total_debt=total_debt,
                    cash_and_equivalents=cash,
                    net_working_capital=nwc,
                    goodwill=goodwill,
                    tangible_book_value=tangible_bv,
                )
            )

        # Persist — batch insert
        db.executemany(
            """
            INSERT OR REPLACE INTO balance_sheet
                (ticker, year, total_assets, total_liabilities,
                 stockholders_equity, current_assets, current_liabilities,
                 current_ratio, total_debt, cash_and_equivalents,
                 net_working_capital, goodwill, tangible_book_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [r.ticker, r.year, r.total_assets, r.total_liabilities,
                 r.stockholders_equity, r.current_assets, r.current_liabilities,
                 r.current_ratio, r.total_debt, r.cash_and_equivalents,
                 r.net_working_capital, r.goodwill, r.tangible_book_value]
                for r in rows
            ],
        )

        logger.info("Stored %d years of balance sheet for %s", len(rows), ticker)
        return rows

    # ------------------------------------------------------------------
    # Step 5: Cash Flow Statement (Multi-Year)
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_cashflow(self, ticker: str) -> list[CashFlowRow]:
        """Fetch multi-year cash flow statement data from yfinance."""
        logger.info("Collecting cash flow data for %s", ticker)

        # Daily guard — skip if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            "SELECT COUNT(*) FROM cash_flows WHERE ticker = ? "
            "AND year = EXTRACT(YEAR FROM CAST(? AS DATE))",
            [ticker, today],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info("Cash flow for %s already has current-year data, skipping", ticker)
            rows_raw = db.execute(
                "SELECT ticker, year, operating_cashflow, capital_expenditures, "
                "free_cashflow, financing_cashflow, investing_cashflow, "
                "dividends_paid, share_buybacks, net_change_in_cash "
                "FROM cash_flows WHERE ticker = ? ORDER BY year",
                [ticker],
            ).fetchall()
            return [
                CashFlowRow(
                    ticker=r[0], year=r[1], operating_cashflow=r[2],
                    capital_expenditures=r[3], free_cashflow=r[4],
                    financing_cashflow=r[5], investing_cashflow=r[6],
                    dividends_paid=r[7], share_buybacks=r[8],
                    net_change_in_cash=r[9],
                )
                for r in rows_raw
            ]

        t = self._get_ticker(ticker)
        cf: pd.DataFrame = t.cashflow

        if cf is None or cf.empty:
            logger.warning("No cash flow data returned for %s", ticker)
            return []

        cf = cf[sorted(cf.columns)]
        rows: list[CashFlowRow] = []

        def get_val(keys: list[str], col: object) -> float:
            for k in keys:
                if k in cf.index:
                    val = cf.loc[k, col]
                    if pd.notnull(val):
                        return float(val)
            return 0.0

        for date_col in cf.columns:
            year = date_col.year if hasattr(date_col, "year") else int(date_col)

            op_cf = get_val(
                ["Operating Cash Flow", "Total Cash From Operating Activities",
                 "Cash Flow From Continuing Operating Activities"], date_col
            )
            capex = get_val(
                ["Capital Expenditure", "Capital Expenditures"], date_col
            )
            inv_cf = get_val(
                ["Investing Cash Flow",
                 "Cash Flow From Continuing Investing Activities"], date_col
            )
            fin_cf = get_val(
                ["Financing Cash Flow",
                 "Cash Flow From Continuing Financing Activities"], date_col
            )
            divs = get_val(
                ["Cash Dividends Paid", "Common Stock Dividend Paid"], date_col
            )
            buybacks = get_val(
                ["Repurchase Of Capital Stock",
                 "Common Stock Payments"], date_col
            )
            net_change = get_val(
                ["Changes In Cash", "Net Change In Cash"], date_col
            )

            fcf = op_cf + capex  # capex is usually negative

            rows.append(
                CashFlowRow(
                    ticker=ticker,
                    year=year,
                    operating_cashflow=op_cf,
                    capital_expenditures=capex,
                    free_cashflow=fcf,
                    financing_cashflow=fin_cf,
                    investing_cashflow=inv_cf,
                    dividends_paid=divs,
                    share_buybacks=buybacks,
                    net_change_in_cash=net_change,
                )
            )

        # Persist — batch insert
        db.executemany(
            """
            INSERT OR REPLACE INTO cash_flows
                (ticker, year, operating_cashflow, capital_expenditures,
                 free_cashflow, financing_cashflow, investing_cashflow,
                 dividends_paid, share_buybacks, net_change_in_cash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [r.ticker, r.year, r.operating_cashflow, r.capital_expenditures,
                 r.free_cashflow, r.financing_cashflow, r.investing_cashflow,
                 r.dividends_paid, r.share_buybacks, r.net_change_in_cash]
                for r in rows
            ],
        )

        logger.info("Stored %d years of cash flow for %s", len(rows), ticker)
        return rows

    # ------------------------------------------------------------------
    # Step 6: Analyst Price Targets & Recommendations
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_analyst_data(self, ticker: str) -> AnalystData | None:
        """Fetch analyst price targets and recommendation counts."""
        logger.info("Collecting analyst data for %s", ticker)

        # Daily guard — return stored data if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            """SELECT ticker, snapshot_date, target_mean, target_median,
                      target_high, target_low, num_analysts,
                      strong_buy, buy, hold, sell, strong_sell
               FROM analyst_data WHERE ticker = ? AND snapshot_date = ?""",
            [ticker, today],
        ).fetchone()
        if existing:
            logger.info("Analyst data for %s already collected today, returning stored", ticker)
            return AnalystData(
                ticker=existing[0], snapshot_date=existing[1],
                target_mean=existing[2] or 0.0, target_median=existing[3] or 0.0,
                target_high=existing[4] or 0.0, target_low=existing[5] or 0.0,
                num_analysts=existing[6] or 0,
                strong_buy=existing[7] or 0, buy=existing[8] or 0,
                hold=existing[9] or 0, sell=existing[10] or 0,
                strong_sell=existing[11] or 0,
            )

        t = self._get_ticker(ticker)

        # Price targets
        target_mean = 0.0
        target_median = 0.0
        target_high = 0.0
        target_low = 0.0
        num_analysts = 0

        try:
            targets = t.analyst_price_targets
            if targets is not None:
                if isinstance(targets, dict):
                    target_mean = float(targets.get("mean", 0) or 0)
                    target_median = float(targets.get("median", 0) or 0)
                    target_high = float(targets.get("high", 0) or 0)
                    target_low = float(targets.get("low", 0) or 0)
                    num_analysts = int(targets.get("numberOfAnalysts", 0) or 0)
                elif isinstance(targets, pd.DataFrame) and not targets.empty:
                    target_mean = float(targets.iloc[0].get("mean", 0) or 0)
                    target_median = float(targets.iloc[0].get("median", 0) or 0)
                    target_high = float(targets.iloc[0].get("high", 0) or 0)
                    target_low = float(targets.iloc[0].get("low", 0) or 0)
        except Exception as e:
            logger.warning("Could not fetch analyst targets for %s: %s", ticker, e)

        # Recommendation summary
        strong_buy = buy = hold = sell = strong_sell = 0
        try:
            recs = t.recommendations_summary
            if recs is not None and isinstance(recs, pd.DataFrame) and not recs.empty:
                latest = recs.iloc[0]
                strong_buy = int(latest.get("strongBuy", 0) or 0)
                buy = int(latest.get("buy", 0) or 0)
                hold = int(latest.get("hold", 0) or 0)
                sell = int(latest.get("sell", 0) or 0)
                strong_sell = int(latest.get("strongSell", 0) or 0)
        except Exception as e:
            logger.warning("Could not fetch recommendations for %s: %s", ticker, e)

        data = AnalystData(
            ticker=ticker,
            snapshot_date=today,
            target_mean=target_mean,
            target_median=target_median,
            target_high=target_high,
            target_low=target_low,
            num_analysts=num_analysts,
            strong_buy=strong_buy,
            buy=buy,
            hold=hold,
            sell=sell,
            strong_sell=strong_sell,
        )

        # Persist
        db.execute(
            """
            INSERT OR REPLACE INTO analyst_data
                (ticker, snapshot_date, target_mean, target_median,
                 target_high, target_low, num_analysts,
                 strong_buy, buy, hold, sell, strong_sell)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [data.ticker, data.snapshot_date, data.target_mean, data.target_median,
             data.target_high, data.target_low, data.num_analysts,
             data.strong_buy, data.buy, data.hold, data.sell, data.strong_sell],
        )

        logger.info(
            "Stored analyst data for %s: target=$%.2f, %d analysts",
            ticker, target_mean, num_analysts,
        )
        return data

    # ------------------------------------------------------------------
    # Step 7: Insider & Institutional Activity
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_insider_activity(self, ticker: str) -> InsiderSummary | None:
        """Fetch insider transactions and institutional ownership."""
        logger.info("Collecting insider activity for %s", ticker)

        # Daily guard — return stored data if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            """SELECT ticker, snapshot_date, net_insider_buying_90d,
                      institutional_ownership_pct, raw_transactions
               FROM insider_activity WHERE ticker = ? AND snapshot_date = ?""",
            [ticker, today],
        ).fetchone()
        if existing:
            logger.info("Insider activity for %s already collected today, returning stored", ticker)
            return InsiderSummary(
                ticker=existing[0], snapshot_date=existing[1],
                net_insider_buying_90d=existing[2] or 0.0,
                institutional_ownership_pct=existing[3] or 0.0,
                raw_transactions_json=existing[4] or "[]",
            )

        t = self._get_ticker(ticker)
        info = t.info or {}

        # Institutional ownership
        inst_pct = 0.0
        try:
            inst_pct = float(info.get("heldPercentInstitutions", 0) or 0) * 100
        except (TypeError, ValueError):
            pass

        # Insider transactions
        transactions: list[dict] = []
        net_buying_90d = 0.0
        cutoff_90d = today - timedelta(days=90)

        try:
            insider_df = t.insider_transactions
            if insider_df is not None and isinstance(insider_df, pd.DataFrame) and not insider_df.empty:
                for _, row in insider_df.iterrows():
                    tx = {
                        "insider": str(row.get("Insider", "")),
                        "relation": str(row.get("Relation", "")),
                        "transaction": str(row.get("Transaction", "")),
                        "date": str(row.get("Start Date", "")),
                        "shares": float(row.get("Shares", 0) or 0),
                        "value": float(row.get("Value", 0) or 0),
                    }
                    transactions.append(tx)

                    # Calculate net buying in last 90 days
                    try:
                        tx_date = pd.to_datetime(row.get("Start Date"))
                        if tx_date and tx_date.date() >= cutoff_90d:
                            tx_type = str(row.get("Transaction", "")).lower()
                            val = float(row.get("Value", 0) or 0)
                            if "purchase" in tx_type or "buy" in tx_type:
                                net_buying_90d += val
                            elif "sale" in tx_type or "sell" in tx_type:
                                net_buying_90d -= val
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            logger.warning("Could not fetch insider transactions for %s: %s", ticker, e)

        summary = InsiderSummary(
            ticker=ticker,
            snapshot_date=today,
            net_insider_buying_90d=net_buying_90d,
            institutional_ownership_pct=inst_pct,
            raw_transactions_json=json.dumps(transactions[:20]),  # Cap at 20
        )

        # Persist
        db.execute(
            """
            INSERT OR REPLACE INTO insider_activity
                (ticker, snapshot_date, net_insider_buying_90d,
                 institutional_ownership_pct, raw_transactions)
            VALUES (?, ?, ?, ?, ?)
            """,
            [summary.ticker, summary.snapshot_date, summary.net_insider_buying_90d,
             summary.institutional_ownership_pct, summary.raw_transactions_json],
        )

        logger.info(
            "Stored insider activity for %s: net_buying_90d=$%.0f, inst=%.1f%%",
            ticker, net_buying_90d, inst_pct,
        )
        return summary

    # ------------------------------------------------------------------
    # Step 8: Earnings Calendar
    # ------------------------------------------------------------------
    @_retry_on_rate_limit()
    async def collect_earnings_calendar(self, ticker: str) -> EarningsCalendar | None:
        """Fetch upcoming earnings date and historical earnings data."""
        logger.info("Collecting earnings calendar for %s", ticker)

        # Daily guard — return stored data if already collected today
        db = get_db()
        today = date.today()
        existing = db.execute(
            """SELECT ticker, snapshot_date, next_earnings_date, days_until_earnings,
                      earnings_estimate, previous_actual, previous_estimate, surprise_pct
               FROM earnings_calendar WHERE ticker = ? AND snapshot_date = ?""",
            [ticker, today],
        ).fetchone()
        if existing:
            logger.info("Earnings calendar for %s already collected today, returning stored", ticker)
            return EarningsCalendar(
                ticker=existing[0], snapshot_date=existing[1],
                next_earnings_date=existing[2], days_until_earnings=existing[3],
                earnings_estimate=existing[4], previous_actual=existing[5],
                previous_estimate=existing[6], surprise_pct=existing[7],
            )

        t = self._get_ticker(ticker)

        next_date = None
        days_until = None
        estimate = None
        prev_actual = None
        prev_estimate = None
        surprise_pct = None

        # Calendar / earnings dates
        try:
            cal = t.calendar
            if cal is not None and isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if earnings_dates:
                    next_dt = earnings_dates[0]
                    if hasattr(next_dt, "date"):
                        next_date = next_dt.date()
                    else:
                        next_date = pd.to_datetime(next_dt).date()
                    days_until = (next_date - today).days

                estimate = float(cal.get("Earnings Average", 0) or 0) or None
        except Exception as e:
            logger.warning("Could not fetch calendar for %s: %s", ticker, e)

        # Historical earnings for surprise calculation
        try:
            earnings_df = t.earnings_dates
            if earnings_df is not None and isinstance(earnings_df, pd.DataFrame) and not earnings_df.empty:
                # Find most recent past earnings
                past = earnings_df[earnings_df.index <= pd.Timestamp(today)]
                if not past.empty:
                    latest = past.iloc[0]
                    prev_actual = float(latest.get("Reported EPS", 0) or 0) or None
                    prev_estimate = float(latest.get("EPS Estimate", 0) or 0) or None
                    if prev_actual is not None and prev_estimate and prev_estimate != 0:
                        surprise_pct = round(
                            (prev_actual - prev_estimate) / abs(prev_estimate) * 100, 2
                        )
        except Exception as e:
            logger.warning("Could not fetch earnings history for %s: %s", ticker, e)

        cal_data = EarningsCalendar(
            ticker=ticker,
            snapshot_date=today,
            next_earnings_date=next_date,
            days_until_earnings=days_until,
            earnings_estimate=estimate,
            previous_actual=prev_actual,
            previous_estimate=prev_estimate,
            surprise_pct=surprise_pct,
        )

        # Persist
        db.execute(
            """
            INSERT OR REPLACE INTO earnings_calendar
                (ticker, snapshot_date, next_earnings_date, days_until_earnings,
                 earnings_estimate, previous_actual, previous_estimate, surprise_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [cal_data.ticker, cal_data.snapshot_date, cal_data.next_earnings_date,
             cal_data.days_until_earnings, cal_data.earnings_estimate,
             cal_data.previous_actual, cal_data.previous_estimate,
             cal_data.surprise_pct],
        )

        logger.info(
            "Stored earnings calendar for %s: next=%s (%s days)",
            ticker, next_date, days_until,
        )
        return cal_data
