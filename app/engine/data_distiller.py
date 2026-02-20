"""Data Distiller — transforms raw market data into LLM-ready analysis packets.

Pure Python, zero LLM calls.  This module pre-analyzes chart patterns,
valuation context, and risk metrics so the LLM agents receive structured
summaries instead of raw data dumps.

The key insight: instead of sending 126 rows of OHLCV data to the LLM,
we detect patterns and summarize them in plain English.  The LLM then
reasons about "golden cross detected 3 days ago, RSI divergence bearish"
instead of trying to parse thousands of numbers.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.models.dossier import QuantScorecard


class DataDistiller:
    """Transform raw data into structured LLM-ready analysis packets."""

    # ------------------------------------------------------------------
    # Price Action Distillation
    # ------------------------------------------------------------------

    def distill_price_action(
        self,
        prices: list[Any],
        technicals: list[Any],
        scorecard: QuantScorecard | None = None,
    ) -> str:
        """Pre-analyze chart data and return a structured text summary.

        Detects:
          - Trend regime (uptrend/downtrend/sideways via SMA slopes)
          - Key crossovers (golden cross, death cross, MACD crossover)
          - Support/resistance zones (local min/max clustering)
          - Momentum divergences (price vs RSI/MACD)
          - Volume profile (accumulation/distribution)
          - Pattern formations (higher highs, lower lows, consolidation)
        """
        parts = ["=== PRE-COMPUTED CHART ANALYSIS ===\n"]

        if not prices:
            parts.append("No price data available.")
            return "\n".join(parts)

        # Extract close prices
        closes = [float(p.close) for p in prices if hasattr(p, "close")]
        if len(closes) < 5:
            parts.append("Insufficient price data for pattern analysis.")
            return "\n".join(parts)

        # ── Current Price Context ──
        latest = closes[-1]
        parts.append(f"Current Price: ${latest:.2f}")

        # Price change over multiple timeframes
        for label, days in [("1 week", 5), ("1 month", 21), ("3 months", 63),
                             ("6 months", 126)]:
            if len(closes) >= days:
                change = (closes[-1] / closes[-days] - 1) * 100
                parts.append(f"  {label}: {change:+.1f}%")

        # ── Trend Regime Detection ──
        parts.append("\n--- Trend Regime ---")
        if technicals:
            t = technicals[-1]
            sma20 = getattr(t, "sma_20", None)
            sma50 = getattr(t, "sma_50", None)
            sma200 = getattr(t, "sma_200", None)

            if sma20 and sma50 and sma200:
                if latest > sma20 > sma50 > sma200:
                    parts.append("STRONG UPTREND: Price > SMA20 > SMA50 > SMA200")
                elif latest < sma20 < sma50 < sma200:
                    parts.append("STRONG DOWNTREND: Price < SMA20 < SMA50 < SMA200")
                elif latest > sma200 and sma20 > sma50:
                    parts.append("UPTREND with pullback potential: SMA20 still above SMA50")
                elif latest < sma200 and sma20 < sma50:
                    parts.append("DOWNTREND with bounce potential: SMA20 still below SMA50")
                else:
                    parts.append("SIDEWAYS/TRANSITIONAL: Mixed SMA alignment")

                # Distance from key averages
                parts.append(
                    f"  Distance from SMA200: {(latest / sma200 - 1) * 100:+.1f}%"
                )

        # ── Key Crossover Detection ──
        parts.append("\n--- Key Crossovers (recent 10 days) ---")
        crossovers = self._detect_crossovers(technicals)
        if crossovers:
            for c in crossovers:
                parts.append(f"  ⚡ {c}")
        else:
            parts.append("  No recent crossovers detected")

        # ── RSI / Momentum Analysis ──
        parts.append("\n--- Momentum Status ---")
        if technicals:
            t = technicals[-1]
            rsi = getattr(t, "rsi", None)
            macd = getattr(t, "macd", None)
            macd_signal = getattr(t, "macd_signal", None)
            macd_hist = getattr(t, "macd_hist", None)
            adx = getattr(t, "adx", None)

            if rsi is not None:
                if rsi > 70:
                    parts.append(f"  RSI: {rsi:.0f} — OVERBOUGHT (>70)")
                elif rsi < 30:
                    parts.append(f"  RSI: {rsi:.0f} — OVERSOLD (<30)")
                elif rsi > 60:
                    parts.append(f"  RSI: {rsi:.0f} — Bullish momentum")
                elif rsi < 40:
                    parts.append(f"  RSI: {rsi:.0f} — Bearish momentum")
                else:
                    parts.append(f"  RSI: {rsi:.0f} — Neutral")

            if macd is not None and macd_signal is not None:
                if macd > macd_signal:
                    parts.append(f"  MACD: Bullish (MACD {macd:.4f} > Signal {macd_signal:.4f})")
                else:
                    parts.append(f"  MACD: Bearish (MACD {macd:.4f} < Signal {macd_signal:.4f})")

                if macd_hist is not None:
                    # Check histogram trend
                    hist_vals = [getattr(t2, "macd_hist", None) for t2 in technicals[-5:]]
                    hist_vals = [h for h in hist_vals if h is not None]
                    if len(hist_vals) >= 3:
                        if all(hist_vals[i] > hist_vals[i - 1] for i in range(1, len(hist_vals))):
                            parts.append("  MACD Histogram: Expanding (strengthening)")
                        elif all(hist_vals[i] < hist_vals[i - 1] for i in range(1, len(hist_vals))):
                            parts.append("  MACD Histogram: Contracting (weakening)")

            if adx is not None:
                if adx > 40:
                    parts.append(f"  ADX: {adx:.0f} — VERY STRONG trend")
                elif adx > 25:
                    parts.append(f"  ADX: {adx:.0f} — Moderate trend")
                else:
                    parts.append(f"  ADX: {adx:.0f} — Weak/No trend (range-bound)")

        # ── Divergence Detection ──
        divergences = self._detect_divergences(closes, technicals)
        if divergences:
            parts.append("\n--- Divergence Signals ---")
            for d in divergences:
                parts.append(f"  ⚠️ {d}")

        # ── Support / Resistance ──
        parts.append("\n--- Support & Resistance Zones ---")
        support, resistance = self._find_support_resistance(closes)
        for s in support[:3]:
            dist = (latest / s - 1) * 100
            parts.append(f"  Support: ${s:.2f} ({dist:+.1f}% away)")
        for r in resistance[:3]:
            dist = (latest / r - 1) * 100
            parts.append(f"  Resistance: ${r:.2f} ({dist:+.1f}% away)")

        # ── Volume Profile ──
        parts.append("\n--- Volume Analysis ---")
        if prices:
            recent_vols = [float(p.volume) for p in prices[-20:]
                           if hasattr(p, "volume") and p.volume]
            older_vols = [float(p.volume) for p in prices[-60:-20]
                          if hasattr(p, "volume") and p.volume]

            if recent_vols and older_vols:
                avg_recent = np.mean(recent_vols)
                avg_older = np.mean(older_vols)
                vol_change = (avg_recent / avg_older - 1) * 100
                if vol_change > 30:
                    parts.append(f"  Volume SURGING: +{vol_change:.0f}% vs 60d avg (accumulation)")
                elif vol_change < -30:
                    parts.append(f"  Volume DECLINING: {vol_change:.0f}% vs 60d avg (distribution)")
                else:
                    parts.append(f"  Volume normal: {vol_change:+.0f}% vs 60d avg")

        # ── Quant Scorecard Summary ──
        if scorecard:
            parts.append("\n--- Quant Signals Summary ---")
            parts.append(
                f"  Momentum (12m): {scorecard.momentum_12m:+.1%} "
                f"({'strong' if abs(scorecard.momentum_12m) > 0.3 else 'moderate'})"
            )
            h = scorecard.hurst_exponent
            regime = "TRENDING" if h > 0.55 else "MEAN-REVERTING" if h < 0.45 else "RANDOM"
            parts.append(f"  Hurst Exponent: {h:.2f} → {regime}")
            parts.append(
                f"  Mean Reversion Score: {scorecard.mean_reversion_score:+.2f} "
                f"({'OVERBOUGHT' if scorecard.mean_reversion_score > 2 else 'OVERSOLD' if scorecard.mean_reversion_score < -2 else 'normal'})"
            )
            parts.append(f"  VWAP Deviation: {scorecard.vwap_deviation:+.2%}")
            parts.append(f"  Bollinger %B: {scorecard.bollinger_pct_b:.2f}")
            parts.append(
                f"  Kelly Fraction: {scorecard.kelly_fraction:.1%} "
                f"(Half-Kelly: {scorecard.half_kelly:.1%})"
            )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Fundamental Distillation
    # ------------------------------------------------------------------

    def distill_fundamentals(
        self,
        fundamentals: Any | None,
        financial_history: list[Any] | None,
        balance_sheet: list[Any] | None,
        cashflow: list[Any] | None,
        scorecard: QuantScorecard | None = None,
    ) -> str:
        """Pre-compute valuation context and financial health metrics."""
        parts = ["=== PRE-COMPUTED FUNDAMENTAL ANALYSIS ===\n"]

        if not fundamentals:
            parts.append("No fundamental data available.")
            return "\n".join(parts)

        f = fundamentals

        # ── Valuation Snapshot ──
        parts.append("--- Valuation ---")
        pe = getattr(f, "trailing_pe", 0) or 0
        fpe = getattr(f, "forward_pe", 0) or 0
        ps_ratio = getattr(f, "price_to_sales", 0) or 0
        pb_ratio = getattr(f, "price_to_book", 0) or 0
        peg = getattr(f, "peg_ratio", 0) or 0

        if pe > 0:
            if pe > 40:
                parts.append(f"  P/E: {pe:.1f} — EXPENSIVE (growth premium or overvalued)")
            elif pe > 20:
                parts.append(f"  P/E: {pe:.1f} — Fair/Growth valuation")
            elif pe > 10:
                parts.append(f"  P/E: {pe:.1f} — Reasonable value")
            else:
                parts.append(f"  P/E: {pe:.1f} — DEEP VALUE or earnings concerns")
        if fpe > 0 and pe > 0:
            if fpe < pe:
                parts.append(f"  Forward P/E: {fpe:.1f} — Earnings GROWTH expected")
            else:
                parts.append(f"  Forward P/E: {fpe:.1f} — Earnings DECLINE expected")
        if ps_ratio > 0:
            parts.append(f"  P/S: {ps_ratio:.1f} — {'Expensive' if ps_ratio > 10 else 'Fair' if ps_ratio > 3 else 'Value'}")
        if pb_ratio > 0:
            parts.append(f"  P/B: {pb_ratio:.1f} — {'Premium' if pb_ratio > 5 else 'Fair' if pb_ratio > 1 else 'Below book value'}")
        if peg > 0:
            if peg < 1:
                parts.append(f"  PEG: {peg:.2f} — UNDERVALUED relative to growth")
            elif peg > 2:
                parts.append(f"  PEG: {peg:.2f} — OVERVALUED relative to growth")

        # ── Revenue Trajectory ──
        if financial_history and len(financial_history) >= 2:
            parts.append("\n--- Revenue Trajectory ---")
            revs = [(getattr(fh, "year", 0), getattr(fh, "revenue", 0))
                     for fh in financial_history if getattr(fh, "revenue", None)]
            revs.sort()
            if len(revs) >= 2:
                for i in range(1, len(revs)):
                    if revs[i - 1][1] and revs[i - 1][1] > 0:
                        growth = (revs[i][1] / revs[i - 1][1] - 1) * 100
                        parts.append(
                            f"  {revs[i][0]}: ${revs[i][1] / 1e9:.1f}B "
                            f"({growth:+.1f}% YoY)"
                        )
                    else:
                        parts.append(f"  {revs[i][0]}: ${revs[i][1] / 1e9:.1f}B")

                # Trend acceleration/deceleration
                if len(revs) >= 3:
                    g1 = (revs[-1][1] / revs[-2][1] - 1) if revs[-2][1] else 0
                    g0 = (revs[-2][1] / revs[-3][1] - 1) if revs[-3][1] else 0
                    if g1 > g0 > 0:
                        parts.append("  📈 Revenue growth ACCELERATING")
                    elif g0 > g1 > 0:
                        parts.append("  📉 Revenue growth DECELERATING (still positive)")
                    elif g1 < 0:
                        parts.append("  ❌ Revenue DECLINING")

        # ── Cash Flow Quality ──
        if cashflow:
            cf = cashflow[0]  # most recent year
            ocf = getattr(cf, "operating_cashflow", 0) or 0
            ni = getattr(f, "net_income", 0) or 0
            fcf = getattr(cf, "free_cashflow", 0) or 0

            parts.append("\n--- Cash Flow Quality ---")
            if ni > 0 and ocf > 0:
                quality = ocf / ni
                if quality > 1.2:
                    parts.append(
                        f"  OCF/NI: {quality:.1f}x — HIGH quality earnings "
                        f"(cash exceeds reported profits)"
                    )
                elif quality > 0.8:
                    parts.append(f"  OCF/NI: {quality:.1f}x — Normal earnings quality")
                else:
                    parts.append(
                        f"  OCF/NI: {quality:.1f}x — LOW quality "
                        f"(accruals inflating profits)"
                    )

            if fcf:
                mcap = getattr(f, "market_cap", 0) or 0
                if mcap > 0:
                    fcf_yield = (fcf / mcap) * 100
                    parts.append(f"  FCF Yield: {fcf_yield:.1f}% — "
                                 f"{'Attractive' if fcf_yield > 5 else 'Normal' if fcf_yield > 2 else 'Low'}")

        # ── Quant Scores ──
        if scorecard:
            parts.append("\n--- Financial Health Scores ---")

            az = scorecard.altman_z_score
            if az > 0:
                if az > 2.99:
                    parts.append(f"  Altman Z-Score: {az:.2f} — SAFE ZONE (bankruptcy unlikely)")
                elif az > 1.81:
                    parts.append(f"  Altman Z-Score: {az:.2f} — GREY ZONE (monitor closely)")
                else:
                    parts.append(f"  Altman Z-Score: {az:.2f} — ⚠️ DISTRESS ZONE (bankruptcy risk)")

            pf = scorecard.piotroski_f_score
            if pf > 0:
                if pf >= 7:
                    parts.append(f"  Piotroski F-Score: {pf}/9 — STRONG financial health")
                elif pf >= 4:
                    parts.append(f"  Piotroski F-Score: {pf}/9 — Average financial health")
                else:
                    parts.append(f"  Piotroski F-Score: {pf}/9 — WEAK financial health")

            eyg = scorecard.earnings_yield_gap
            if eyg != 0:
                if eyg > 0.03:
                    parts.append(
                        f"  Earnings Yield Gap: {eyg:+.1%} — "
                        f"CHEAP vs bonds (buy signal)"
                    )
                elif eyg < -0.01:
                    parts.append(
                        f"  Earnings Yield Gap: {eyg:+.1%} — "
                        f"EXPENSIVE vs bonds"
                    )
                else:
                    parts.append(f"  Earnings Yield Gap: {eyg:+.1%} — Fair")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Risk Distillation
    # ------------------------------------------------------------------

    def distill_risk(
        self,
        risk_metrics: Any | None,
        scorecard: QuantScorecard | None = None,
    ) -> str:
        """Distill risk metrics into actionable, contextualized sentences."""
        parts = ["=== PRE-COMPUTED RISK ANALYSIS ===\n"]

        if not risk_metrics and not scorecard:
            parts.append("No risk data available.")
            return "\n".join(parts)

        # ── Risk-Adjusted Performance ──
        parts.append("--- Risk-Adjusted Performance ---")
        if risk_metrics:
            rm = risk_metrics
            sharpe = getattr(rm, "sharpe_ratio", 0) or 0
            sortino = getattr(rm, "sortino_ratio", 0) or 0

            # Contextualize vs benchmarks
            if sharpe > 1.5:
                parts.append(f"  Sharpe: {sharpe:.2f} — TOP QUARTILE (S&P avg ~0.4)")
            elif sharpe > 1.0:
                parts.append(f"  Sharpe: {sharpe:.2f} — Good (above S&P avg ~0.4)")
            elif sharpe > 0.5:
                parts.append(f"  Sharpe: {sharpe:.2f} — Moderate")
            elif sharpe > 0:
                parts.append(f"  Sharpe: {sharpe:.2f} — Below average")
            else:
                parts.append(f"  Sharpe: {sharpe:.2f} — ⚠️ NEGATIVE risk-adjusted returns")

            if sortino > 2.0:
                parts.append(f"  Sortino: {sortino:.2f} — Excellent downside management")
            elif sortino < 0:
                parts.append(f"  Sortino: {sortino:.2f} — ⚠️ Downside exceeds returns")

        # ── Dollar-Amount Risk ──
        if risk_metrics:
            rm = risk_metrics
            var95 = getattr(rm, "var_95", 0) or 0
            cvar95 = getattr(rm, "cvar_95", 0) or 0

            parts.append("\n--- Worst-Case Scenarios (per $10,000) ---")
            parts.append(
                f"  VaR(95%): ${abs(var95) * 10000:.0f} "
                f"daily loss on 1-in-20 bad day"
            )
            parts.append(
                f"  CVaR(95%): ${abs(cvar95) * 10000:.0f} "
                f"avg loss when exceeding VaR"
            )

            mdd = getattr(rm, "max_drawdown", 0) or 0
            cur_dd = getattr(rm, "current_drawdown", 0) or 0
            parts.append(
                f"  Max Historical Drawdown: {mdd * 100:.1f}%"
            )
            parts.append(
                f"  Current Drawdown: {cur_dd * 100:.1f}% "
                f"({'room to deteriorate' if cur_dd > mdd * 0.5 else 'well within historical range'})"
            )

        # ── Position Sizing Guidance ──
        if scorecard:
            parts.append("\n--- Position Sizing (Kelly Criterion) ---")
            parts.append(
                f"  Full Kelly: {scorecard.kelly_fraction:.1%} of portfolio"
            )
            parts.append(
                f"  Half-Kelly (recommended): {scorecard.half_kelly:.1%} of portfolio"
            )

            if scorecard.omega_ratio > 1.5:
                parts.append(
                    f"  Omega Ratio: {scorecard.omega_ratio:.2f} — "
                    f"Gains outweigh losses (favorable)"
                )
            elif scorecard.omega_ratio < 0.8:
                parts.append(
                    f"  Omega Ratio: {scorecard.omega_ratio:.2f} — "
                    f"⚠️ Losses outweigh gains"
                )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_crossovers(technicals: list[Any]) -> list[str]:
        """Detect SMA/EMA crossovers in the last 10 trading days."""
        signals = []
        if len(technicals) < 10:
            return signals

        recent = technicals[-10:]

        for i in range(1, len(recent)):
            prev = recent[i - 1]
            curr = recent[i]

            # Golden Cross / Death Cross (SMA50 vs SMA200)
            p50 = getattr(prev, "sma_50", None)
            c50 = getattr(curr, "sma_50", None)
            p200 = getattr(prev, "sma_200", None)
            c200 = getattr(curr, "sma_200", None)
            if p50 and c50 and p200 and c200:
                if p50 < p200 and c50 >= c200:
                    signals.append(f"GOLDEN CROSS (SMA50 crossed above SMA200) {10 - i}d ago")
                elif p50 > p200 and c50 <= c200:
                    signals.append(f"DEATH CROSS (SMA50 crossed below SMA200) {10 - i}d ago")

            # MACD crossover
            pm = getattr(prev, "macd", None)
            cm = getattr(curr, "macd", None)
            ps = getattr(prev, "macd_signal", None)
            cs = getattr(curr, "macd_signal", None)
            if pm is not None and cm is not None and ps is not None and cs is not None:
                if pm < ps and cm >= cs:
                    signals.append(f"MACD bullish crossover {10 - i}d ago")
                elif pm > ps and cm <= cs:
                    signals.append(f"MACD bearish crossover {10 - i}d ago")

            # RSI crossing 30/70
            pr = getattr(prev, "rsi", None)
            cr = getattr(curr, "rsi", None)
            if pr is not None and cr is not None:
                if pr < 30 and cr >= 30:
                    signals.append(f"RSI crossed up through 30 (oversold exit) {10 - i}d ago")
                elif pr > 70 and cr <= 70:
                    signals.append(f"RSI crossed down through 70 (overbought exit) {10 - i}d ago")

        return signals

    @staticmethod
    def _detect_divergences(
        closes: list[float],
        technicals: list[Any],
    ) -> list[str]:
        """Detect price vs RSI/MACD divergences over the last 20 days."""
        signals = []
        if len(closes) < 20 or len(technicals) < 20:
            return signals

        # Compare first half vs second half of last 20 days
        mid = 10
        p_first = closes[-20:-mid]
        p_second = closes[-mid:]

        rsi_first = [getattr(t, "rsi", None) for t in technicals[-20:-mid]]
        rsi_second = [getattr(t, "rsi", None) for t in technicals[-mid:]]

        rsi_first = [r for r in rsi_first if r is not None]
        rsi_second = [r for r in rsi_second if r is not None]

        if rsi_first and rsi_second:
            price_up = np.mean(p_second) > np.mean(p_first)
            rsi_up = np.mean(rsi_second) > np.mean(rsi_first)

            if price_up and not rsi_up:
                signals.append(
                    "BEARISH DIVERGENCE: Price rising but RSI falling "
                    "(weakening momentum — potential reversal)"
                )
            elif not price_up and rsi_up:
                signals.append(
                    "BULLISH DIVERGENCE: Price falling but RSI rising "
                    "(building strength — potential bounce)"
                )

        return signals

    @staticmethod
    def _find_support_resistance(
        closes: list[float],
        window: int = 5,
    ) -> tuple[list[float], list[float]]:
        """Find support and resistance zones from local min/max."""
        if len(closes) < window * 2 + 1:
            return [], []

        supports = []
        resistances = []

        for i in range(window, len(closes) - window):
            local_window = closes[i - window : i + window + 1]
            if closes[i] == min(local_window):
                supports.append(closes[i])
            elif closes[i] == max(local_window):
                resistances.append(closes[i])

        # Cluster nearby levels (within 2%)
        supports = DataDistiller._cluster_levels(supports, closes[-1])
        resistances = DataDistiller._cluster_levels(resistances, closes[-1])

        # Sort: support below current, resistance above current
        current = closes[-1]
        supports = sorted([s for s in supports if s < current], reverse=True)
        resistances = sorted([r for r in resistances if r > current])

        return supports[:3], resistances[:3]

    @staticmethod
    def _cluster_levels(levels: list[float], reference: float,
                        threshold: float = 0.02) -> list[float]:
        """Cluster nearby price levels together."""
        if not levels:
            return []

        levels = sorted(levels)
        clusters: list[list[float]] = [[levels[0]]]

        for level in levels[1:]:
            if abs(level - clusters[-1][-1]) / max(reference, 1) < threshold:
                clusters[-1].append(level)
            else:
                clusters.append([level])

        return [float(np.mean(c)) for c in clusters]
