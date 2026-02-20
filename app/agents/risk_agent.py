"""Risk assessment agent — uses quantitative risk metrics from RiskComputer."""

from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.models.agent_reports import RiskReport


class RiskAgent(BaseAgent):
    """Assesses risk using quantitative metrics from the RiskComputer.

    Now receives comprehensive risk metrics: z-score, Sharpe, Sortino,
    VaR, CVaR, max drawdown, beta, alpha, R², ulcer index, and more.
    """

    prompt_file = "risk_assessment.md"
    output_model = RiskReport

    def format_context(self, ticker: str, context: dict) -> str:
        """Format quantitative risk metrics + fundamental data for the LLM.

        context keys:
            price_history:      list[OHLCVRow]
            technicals:         list[TechnicalRow]
            fundamentals:       FundamentalSnapshot
            risk_metrics:       RiskMetrics
            risk_params:        dict (user-defined risk parameters)
            quant_scorecard:    QuantScorecard | None
            distilled_analysis: str (pre-computed risk analysis)
        """
        parts = []

        # ---- Distilled Analysis (pre-computed risk context & scenarios) ----
        distilled = context.get("distilled_analysis", "")
        if distilled:
            parts.append(distilled)
            parts.append("\n" + "=" * 60)
            parts.append("RAW RISK METRICS (reference for above analysis)")
            parts.append("=" * 60 + "\n")

        # ---- Current Price ----
        prices = context.get("price_history", [])
        if prices:
            latest = prices[-1]
            parts.append(f"CURRENT PRICE: ${latest.close:.2f}")

        # ---- Quantitative Risk Metrics (from RiskComputer) ----
        rm = context.get("risk_metrics")
        if rm:
            parts.append("\n=== QUANTITATIVE RISK METRICS ===")

            parts.append("\n--- Risk/Return Ratios ---")
            parts.append(f"Sharpe Ratio: {rm.sharpe_ratio:.4f}")
            parts.append(f"Sortino Ratio: {rm.sortino_ratio:.4f}")
            parts.append(f"Calmar Ratio: {rm.calmar_ratio:.4f}")
            parts.append(f"Treynor Ratio: {rm.treynor_ratio:.4f}")
            parts.append(f"Information Ratio: {rm.information_ratio:.4f}")
            parts.append(f"Gain-to-Pain Ratio: {rm.gain_to_pain_ratio:.4f}")

            parts.append("\n--- Z-Score (distance from mean) ---")
            parts.append(f"Z-Score (20-day): {rm.z_score_20:.4f}")
            parts.append(f"Z-Score (50-day): {rm.z_score_50:.4f}")

            parts.append("\n--- Value at Risk ---")
            parts.append(f"VaR (95%): {rm.var_95:.4f} ({rm.var_95 * 100:.2f}% daily loss)")
            parts.append(f"VaR (99%): {rm.var_99:.4f} ({rm.var_99 * 100:.2f}% daily loss)")
            parts.append(f"CVaR (95%): {rm.cvar_95:.4f} (Expected Shortfall)")
            parts.append(f"CVaR (99%): {rm.cvar_99:.4f} (Expected Shortfall)")

            parts.append("\n--- Drawdown ---")
            parts.append(f"Max Drawdown: {rm.max_drawdown * 100:.2f}%")
            parts.append(f"Max Drawdown Duration: {rm.max_drawdown_duration_days} trading days")
            parts.append(f"Current Drawdown: {rm.current_drawdown * 100:.2f}%")

            parts.append("\n--- Volatility ---")
            parts.append(f"Daily Volatility: {rm.daily_volatility:.4f}")
            parts.append(f"Annualized Volatility: {rm.annualized_volatility * 100:.2f}%")
            parts.append(f"Downside Deviation: {rm.downside_deviation:.4f}")
            parts.append(f"Volatility Skew: {rm.volatility_skew:.4f}")
            parts.append(f"Return Kurtosis: {rm.return_kurtosis:.4f}")
            parts.append(f"Tail Ratio: {rm.tail_ratio:.4f}")
            parts.append(f"Ulcer Index: {rm.ulcer_index:.4f}")

            parts.append("\n--- Market Sensitivity (vs SPY) ---")
            parts.append(f"Beta: {rm.beta:.4f}")
            parts.append(f"Alpha (annualized): {rm.alpha:.4f}")
            parts.append(f"R²: {rm.r_squared:.4f}")
            parts.append(f"Correlation to SPY: {rm.correlation_to_spy:.4f}")

            # Interpretive helpers
            parts.append("\n--- Key Interpretations ---")

            # Sharpe interpretation
            if rm.sharpe_ratio > 1.5:
                parts.append("Sharpe > 1.5: EXCELLENT risk-adjusted returns")
            elif rm.sharpe_ratio > 1.0:
                parts.append("Sharpe 1.0-1.5: GOOD risk-adjusted returns")
            elif rm.sharpe_ratio > 0.5:
                parts.append("Sharpe 0.5-1.0: MODERATE risk-adjusted returns")
            else:
                parts.append("Sharpe < 0.5: POOR risk-adjusted returns")

            # Beta interpretation
            if rm.beta > 1.5:
                parts.append(f"Beta {rm.beta:.2f}: HIGHLY volatile relative to market")
            elif rm.beta > 1.0:
                parts.append(f"Beta {rm.beta:.2f}: More volatile than market")
            elif rm.beta > 0.5:
                parts.append(f"Beta {rm.beta:.2f}: Less volatile than market")
            else:
                parts.append(f"Beta {rm.beta:.2f}: Defensive / low correlation")

            # Z-score interpretation
            if abs(rm.z_score_20) > 2:
                direction = "ABOVE" if rm.z_score_20 > 0 else "BELOW"
                parts.append(
                    f"Z-Score {rm.z_score_20:.2f}: Price is {abs(rm.z_score_20):.1f} "
                    f"std devs {direction} 20-day mean — EXTREME"
                )

            # Max drawdown interpretation
            if rm.max_drawdown < -0.30:
                parts.append(f"Max DD {rm.max_drawdown * 100:.1f}%: SEVERE historical drawdown")
            elif rm.max_drawdown < -0.15:
                parts.append(f"Max DD {rm.max_drawdown * 100:.1f}%: SIGNIFICANT drawdown risk")

        # ---- Fundamental Risk Factors ----
        f = context.get("fundamentals")
        if f:
            parts.append("\n=== FUNDAMENTAL RISK FACTORS ===")
            if f.debt_to_equity > 0:
                parts.append(f"Debt/Equity: {f.debt_to_equity:.2f}")
            if f.trailing_pe > 0:
                parts.append(f"P/E Ratio: {f.trailing_pe:.2f}")
            if f.free_cash_flow:
                parts.append(f"Free Cash Flow: ${f.free_cash_flow / 1e9:.2f}B")

        # ---- Technical Risk Signals ----
        technicals = context.get("technicals", [])
        if technicals:
            t = technicals[-1]
            parts.append("\n=== TECHNICAL RISK SIGNALS ===")
            if t.atr is not None:
                parts.append(f"ATR(14): {t.atr:.4f}")
            if t.natr is not None:
                parts.append(f"NATR(14): {t.natr:.2f}%")
            if t.bb_upper is not None and t.bb_lower is not None and prices:
                bb_width = (t.bb_upper - t.bb_lower) / t.bb_middle if t.bb_middle else 0
                parts.append(f"Bollinger Band Width: {bb_width:.4f}")

        # ---- User Risk Parameters ----
        risk_params = context.get("risk_params", {})
        if risk_params:
            parts.append("\n=== USER RISK PARAMETERS ===")
            parts.append(f"Risk Tolerance: {risk_params.get('risk_tolerance', 'N/A')}")
            parts.append(f"Max Position Size: ${risk_params.get('max_position_size', 'N/A')}")
            parts.append(
                f"Max Portfolio Allocation: "
                f"{risk_params.get('max_portfolio_allocation_pct', 'N/A')}%"
            )
            parts.append(f"Stop Loss Pct: {risk_params.get('stop_loss_pct', 'N/A')}%")
            parts.append(
                f"Max Drawdown Tolerance: "
                f"{risk_params.get('max_drawdown_pct', 'N/A')}%"
            )

        return "\n".join(parts)
