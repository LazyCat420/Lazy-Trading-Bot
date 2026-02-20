"""Fundamental analysis agent — analyzes company financials, balance sheet,
cash flow, analyst consensus, insider activity, and earnings proximity.
"""

from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.models.agent_reports import FundamentalReport


class FundamentalAgent(BaseAgent):
    """Analyses fundamental data to assess company health and valuation.

    Now receives expanded data: balance sheet trends, cash flow quality,
    analyst consensus, insider activity, and earnings calendar.
    """

    prompt_file = "fundamental_analysis.md"
    output_model = FundamentalReport

    def format_context(self, ticker: str, context: dict) -> str:
        """Format all fundamental data for the LLM.

        context keys:
            fundamentals:       FundamentalSnapshot
            financial_history:  list[FinancialHistoryRow]
            balance_sheet:      list[BalanceSheetRow]
            cashflow:           list[CashFlowRow]
            analyst_data:       AnalystData | None
            insider_activity:   InsiderSummary | None
            earnings_calendar:  EarningsCalendar | None
            quant_scorecard:    QuantScorecard | None
            distilled_analysis: str (pre-computed fundamental analysis)
        """
        parts = []

        # ---- Distilled Analysis (pre-computed health scores & context) ----
        distilled = context.get("distilled_analysis", "")
        if distilled:
            parts.append(distilled)
            parts.append("\n" + "=" * 60)
            parts.append("RAW FINANCIAL DATA (reference for above analysis)")
            parts.append("=" * 60 + "\n")

        f = context.get("fundamentals")

        # ---- Valuation ----
        if f:
            parts.append("=== VALUATION ===")
            parts.append(f"Market Cap: {self._fmt_big(f.market_cap)}")
            parts.append(f"Trailing P/E: {f.trailing_pe:.2f}")
            parts.append(f"Forward P/E: {f.forward_pe:.2f}")
            parts.append(f"PEG Ratio: {f.peg_ratio:.2f}")
            parts.append(f"Price/Sales: {f.price_to_sales:.2f}")
            parts.append(f"Price/Book: {f.price_to_book:.2f}")
            parts.append(f"EV/Revenue: {f.ev_to_revenue:.2f}")
            parts.append(f"EV/EBITDA: {f.ev_to_ebitda:.2f}")

            parts.append("\n=== PROFITABILITY ===")
            parts.append(f"Profit Margin: {self._pct(f.profit_margin)}")
            parts.append(f"Operating Margin: {self._pct(f.operating_margin)}")
            parts.append(f"ROA: {self._pct(f.return_on_assets)}")
            parts.append(f"ROE: {self._pct(f.return_on_equity)}")

            parts.append("\n=== FINANCIAL POSITION ===")
            parts.append(f"Revenue: {self._fmt_big(f.revenue)}")
            parts.append(f"Revenue Growth: {self._pct(f.revenue_growth)}")
            parts.append(f"Net Income: {self._fmt_big(f.net_income)}")
            parts.append(f"EPS: ${f.trailing_eps:.2f}")
            parts.append(f"Total Cash: {self._fmt_big(f.total_cash)}")
            parts.append(f"Total Debt: {self._fmt_big(f.total_debt)}")
            parts.append(f"D/E Ratio: {f.debt_to_equity:.2f}")
            parts.append(f"Free Cash Flow: {self._fmt_big(f.free_cash_flow)}")

            if f.dividend_yield > 0:
                parts.append(f"\nDividend Yield: {self._pct(f.dividend_yield)}")
                parts.append(f"Payout Ratio: {self._pct(f.payout_ratio)}")

            parts.append(f"\nSector: {f.sector}")
            parts.append(f"Industry: {f.industry}")

        # ---- Income Statement Trend (Multi-Year) ----
        fin_hist = context.get("financial_history", [])
        if fin_hist:
            parts.append("\n=== INCOME STATEMENT TREND ===")
            parts.append("Year | Revenue | Net Income | Margin | EPS")
            parts.append("-" * 50)
            for row in fin_hist:
                parts.append(
                    f"{row.year} | {self._fmt_big(row.revenue)} | "
                    f"{self._fmt_big(row.net_income)} | "
                    f"{self._pct(row.net_margin)} | ${row.eps:.2f}"
                )

        # ---- Balance Sheet Trend ----
        bs_data = context.get("balance_sheet", [])
        if bs_data:
            parts.append("\n=== BALANCE SHEET TREND ===")
            parts.append("Year | Assets | Liabilities | Equity | Debt | Cash | Current Ratio")
            parts.append("-" * 70)
            for row in bs_data:
                parts.append(
                    f"{row.year} | {self._fmt_big(row.total_assets)} | "
                    f"{self._fmt_big(row.total_liabilities)} | "
                    f"{self._fmt_big(row.stockholders_equity)} | "
                    f"{self._fmt_big(row.total_debt)} | "
                    f"{self._fmt_big(row.cash_and_equivalents)} | "
                    f"{row.current_ratio:.2f}"
                )

        # ---- Cash Flow Quality ----
        cf_data = context.get("cashflow", [])
        if cf_data:
            parts.append("\n=== CASH FLOW STATEMENT TREND ===")
            parts.append("Year | Operating CF | CapEx | Free CF | Financing | Buybacks")
            parts.append("-" * 65)
            for row in cf_data:
                parts.append(
                    f"{row.year} | {self._fmt_big(row.operating_cashflow)} | "
                    f"{self._fmt_big(row.capital_expenditures)} | "
                    f"{self._fmt_big(row.free_cashflow)} | "
                    f"{self._fmt_big(row.financing_cashflow)} | "
                    f"{self._fmt_big(row.share_buybacks)}"
                )

        # ---- Analyst Consensus ----
        analyst = context.get("analyst_data")
        if analyst:
            parts.append("\n=== ANALYST CONSENSUS ===")
            parts.append(f"Price Target Mean: ${analyst.target_mean:.2f}")
            parts.append(f"Price Target Median: ${analyst.target_median:.2f}")
            parts.append(f"Target Range: ${analyst.target_low:.2f} - ${analyst.target_high:.2f}")
            parts.append(f"Number of Analysts: {analyst.num_analysts}")
            parts.append(
                f"Recommendations: {analyst.strong_buy}SB / {analyst.buy}B / "
                f"{analyst.hold}H / {analyst.sell}S / {analyst.strong_sell}SS"
            )

        # ---- Insider Activity ----
        insider = context.get("insider_activity")
        if insider:
            parts.append("\n=== INSIDER & INSTITUTIONAL ACTIVITY ===")
            net = insider.net_insider_buying_90d
            direction = "NET BUYING" if net > 0 else "NET SELLING" if net < 0 else "NEUTRAL"
            parts.append(f"90-Day Insider Activity: {direction} ({self._fmt_big(abs(net))})")
            parts.append(
                f"Institutional Ownership: {insider.institutional_ownership_pct:.1f}%"
            )

        # ---- Earnings Calendar ----
        earnings = context.get("earnings_calendar")
        if earnings:
            parts.append("\n=== EARNINGS CALENDAR ===")
            if earnings.next_earnings_date:
                parts.append(f"Next Earnings Date: {earnings.next_earnings_date}")
                parts.append(f"Days Until Earnings: {earnings.days_until_earnings}")
            if earnings.earnings_estimate:
                parts.append(f"EPS Estimate: ${earnings.earnings_estimate:.2f}")
            if earnings.previous_actual is not None:
                parts.append(f"Previous Actual EPS: ${earnings.previous_actual:.2f}")
            if earnings.surprise_pct is not None:
                surprise_label = "BEAT" if earnings.surprise_pct > 0 else "MISSED"
                parts.append(
                    f"Previous Surprise: {surprise_label} by {abs(earnings.surprise_pct):.1f}%"
                )

        # ---- Industry Peers Comparison ----
        peer_fundamentals = context.get("peer_fundamentals", [])
        if peer_fundamentals:
            parts.append("\n=== INDUSTRY PEERS COMPARISON ===")
            parts.append("Peer | Mkt Cap | P/E | Fwd P/E | P/S | Rev Growth | ROE | Margins")
            parts.append("-" * 75)
            for p in peer_fundamentals:
                parts.append(
                    f"{p.ticker} | {self._fmt_big(p.market_cap)} | "
                    f"{p.trailing_pe:.2f} | {p.forward_pe:.2f} | {p.price_to_sales:.2f} | "
                    f"{self._pct(p.revenue_growth)} | {self._pct(p.return_on_equity)} | "
                    f"{self._pct(p.profit_margin)}"
                )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_big(value: float) -> str:
        """Format large numbers with B/M suffixes."""
        if abs(value) >= 1e12:
            return f"${value / 1e12:.2f}T"
        if abs(value) >= 1e9:
            return f"${value / 1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:.2f}M"
        if abs(value) >= 1e3:
            return f"${value / 1e3:.1f}K"
        return f"${value:.2f}"

    @staticmethod
    def _pct(value: float) -> str:
        """Format as percentage."""
        return f"{value * 100:.2f}%" if value else "N/A"
