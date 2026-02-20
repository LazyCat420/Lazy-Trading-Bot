"""Technical analysis agent — interprets 6 months of indicator data."""

from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.models.agent_reports import TechnicalReport


class TechnicalAgent(BaseAgent):
    """Analyses technical indicators to determine trend, momentum, and signals.

    Now receives 6 months (~126 trading days) of key indicators for
    multi-day trend analysis, crossover detection, and divergence signals.
    """

    prompt_file = "technical_analysis.md"
    output_model = TechnicalReport

    def format_context(self, ticker: str, context: dict) -> str:
        """Format 6 months of technical indicator data for the LLM.

        context keys:
            price_history:      list[OHLCVRow]
            technicals:         list[TechnicalRow]
            quant_scorecard:    QuantScorecard | None
            distilled_analysis: str (pre-computed chart analysis)
        """
        parts = []

        # ---- Distilled Analysis (pre-computed patterns & signals) ----
        distilled = context.get("distilled_analysis", "")
        if distilled:
            parts.append(distilled)
            parts.append("\n" + "=" * 60)
            parts.append("RAW INDICATOR DATA (reference for above analysis)")
            parts.append("=" * 60 + "\n")

        # ---- Current Price ----
        prices = context.get("price_history", [])
        if prices:
            latest = prices[-1]
            parts.append(f"CURRENT PRICE: ${latest.close:.2f}")
            parts.append(f"52-WEEK RANGE: ${min(p.low for p in prices):.2f} - "
                         f"${max(p.high for p in prices):.2f}")

            # Price change context
            if len(prices) >= 5:
                pct_5d = (prices[-1].close - prices[-5].close) / prices[-5].close * 100
                parts.append(f"5-DAY CHANGE: {pct_5d:+.2f}%")
            if len(prices) >= 20:
                pct_20d = (prices[-1].close - prices[-20].close) / prices[-20].close * 100
                parts.append(f"20-DAY CHANGE: {pct_20d:+.2f}%")
            if len(prices) >= 60:
                pct_60d = (prices[-1].close - prices[-60].close) / prices[-60].close * 100
                parts.append(f"60-DAY CHANGE: {pct_60d:+.2f}%")

        # ---- Latest Indicator Snapshot ----
        technicals = context.get("technicals", [])
        if technicals:
            latest_t = technicals[-1]
            parts.append("\n--- LATEST INDICATOR SNAPSHOT ---")

            # Core
            self._add_indicator(parts, "RSI(14)", latest_t.rsi)
            self._add_indicator(parts, "MACD", latest_t.macd)
            self._add_indicator(parts, "MACD Signal", latest_t.macd_signal)
            self._add_indicator(parts, "MACD Histogram", latest_t.macd_hist)

            # Moving Averages
            parts.append("\nMOVING AVERAGES:")
            self._add_indicator(parts, "  SMA(20)", latest_t.sma_20)
            self._add_indicator(parts, "  SMA(50)", latest_t.sma_50)
            self._add_indicator(parts, "  SMA(200)", latest_t.sma_200)
            self._add_indicator(parts, "  EMA(9)", latest_t.ema_9)
            self._add_indicator(parts, "  EMA(21)", latest_t.ema_21)
            self._add_indicator(parts, "  EMA(50)", latest_t.ema_50)
            self._add_indicator(parts, "  EMA(200)", latest_t.ema_200)

            # Bollinger
            parts.append("\nBOLLINGER BANDS:")
            self._add_indicator(parts, "  Upper", latest_t.bb_upper)
            self._add_indicator(parts, "  Middle", latest_t.bb_middle)
            self._add_indicator(parts, "  Lower", latest_t.bb_lower)

            # Trend
            parts.append("\nTREND INDICATORS:")
            self._add_indicator(parts, "  ADX(14)", latest_t.adx)
            self._add_indicator(parts, "  +DI", latest_t.adx_dmp)
            self._add_indicator(parts, "  -DI", latest_t.adx_dmn)
            self._add_indicator(parts, "  Aroon Up", latest_t.aroon_up)
            self._add_indicator(parts, "  Aroon Down", latest_t.aroon_down)
            self._add_indicator(parts, "  Aroon Osc", latest_t.aroon_osc)
            self._add_indicator(parts, "  SuperTrend", latest_t.supertrend)
            self._add_indicator(parts, "  PSAR", latest_t.psar)
            self._add_indicator(parts, "  CHOP(14)", latest_t.chop)
            self._add_indicator(parts, "  Vortex+", latest_t.vortex_pos)
            self._add_indicator(parts, "  Vortex-", latest_t.vortex_neg)

            # Momentum
            parts.append("\nMOMENTUM INDICATORS:")
            self._add_indicator(parts, "  CCI(14)", latest_t.cci)
            self._add_indicator(parts, "  Williams %R", latest_t.willr)
            self._add_indicator(parts, "  MFI(14)", latest_t.mfi)
            self._add_indicator(parts, "  ROC(10)", latest_t.roc)
            self._add_indicator(parts, "  Momentum(10)", latest_t.mom)
            self._add_indicator(parts, "  Stoch %K", latest_t.stoch_k)
            self._add_indicator(parts, "  Stoch %D", latest_t.stoch_d)
            self._add_indicator(parts, "  StochRSI %K", latest_t.stochrsi_k)
            self._add_indicator(parts, "  UO", latest_t.uo)
            self._add_indicator(parts, "  TSI", latest_t.tsi)
            self._add_indicator(parts, "  AO", latest_t.ao)

            # Volatility
            parts.append("\nVOLATILITY:")
            self._add_indicator(parts, "  ATR(14)", latest_t.atr)
            self._add_indicator(parts, "  NATR(14)", latest_t.natr)
            self._add_indicator(parts, "  True Range", latest_t.true_range)
            self._add_indicator(parts, "  Donchian Upper", latest_t.donchian_upper)
            self._add_indicator(parts, "  Donchian Lower", latest_t.donchian_lower)
            self._add_indicator(parts, "  Keltner Upper", latest_t.kc_upper)
            self._add_indicator(parts, "  Keltner Lower", latest_t.kc_lower)

            # Volume
            parts.append("\nVOLUME INDICATORS:")
            self._add_indicator(parts, "  OBV", latest_t.obv)
            self._add_indicator(parts, "  A/D", latest_t.ad)
            self._add_indicator(parts, "  CMF(20)", latest_t.cmf)
            self._add_indicator(parts, "  EFI(13)", latest_t.efi)
            self._add_indicator(parts, "  PVT", latest_t.pvt)

            # Statistics
            parts.append("\nSTATISTICS:")
            self._add_indicator(parts, "  Z-Score(30)", latest_t.zscore)
            self._add_indicator(parts, "  Skew(30)", latest_t.skew)
            self._add_indicator(parts, "  Kurtosis(30)", latest_t.kurtosis)
            self._add_indicator(parts, "  Entropy(10)", latest_t.entropy)

            # Ichimoku
            parts.append("\nICHIMOKU CLOUD:")
            self._add_indicator(parts, "  Conversion", latest_t.ichi_conv)
            self._add_indicator(parts, "  Base", latest_t.ichi_base)
            self._add_indicator(parts, "  Span A", latest_t.ichi_span_a)
            self._add_indicator(parts, "  Span B", latest_t.ichi_span_b)

            # Fibonacci
            if latest_t.fib_0:
                parts.append("\nFIBONACCI RETRACEMENT:")
                self._add_indicator(parts, "  0% (High)", latest_t.fib_0)
                self._add_indicator(parts, "  23.6%", latest_t.fib_236)
                self._add_indicator(parts, "  38.2%", latest_t.fib_382)
                self._add_indicator(parts, "  50.0%", latest_t.fib_500)
                self._add_indicator(parts, "  61.8%", latest_t.fib_618)
                self._add_indicator(parts, "  78.6%", latest_t.fib_786)
                self._add_indicator(parts, "  100% (Low)", latest_t.fib_1)

        # ---- 6-Month Trend Table (sampled for context efficiency) ----
        if len(technicals) > 5:
            parts.append("\n--- 6-MONTH TREND DATA (sampled every 5 trading days) ---")
            parts.append("Date | Close | RSI | MACD | SMA50 | SMA200 | ADX | OBV")
            parts.append("-" * 70)

            # Sample every 5 days for trend context
            step = max(1, len(technicals) // 25)  # ~25 data points
            sampled = technicals[::step]
            if technicals[-1] not in sampled:
                sampled.append(technicals[-1])

            for t_row in sampled:
                # Find matching price
                close = "N/A"
                for p in prices:
                    if p.date == t_row.date:
                        close = f"{p.close:.2f}"
                        break

                parts.append(
                    f"{t_row.date} | ${close} | "
                    f"{self._fmt(t_row.rsi)} | {self._fmt(t_row.macd)} | "
                    f"{self._fmt(t_row.sma_50)} | {self._fmt(t_row.sma_200)} | "
                    f"{self._fmt(t_row.adx)} | {self._fmt(t_row.obv)}"
                )

        # ---- Key Crossover Signals (auto-detected) ----
        signals = self._detect_signals(technicals, prices)
        if signals:
            parts.append("\n--- AUTO-DETECTED SIGNALS ---")
            for sig in signals:
                parts.append(f"  • {sig}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _add_indicator(parts: list[str], label: str, value: float | None) -> None:
        if value is not None:
            parts.append(f"{label}: {value:.4f}")

    @staticmethod
    def _fmt(val: float | None) -> str:
        return f"{val:.2f}" if val is not None else "N/A"

    @staticmethod
    def _detect_signals(technicals: list, prices: list) -> list[str]:
        """Auto-detect key crossover and divergence signals."""
        if len(technicals) < 3:
            return []

        signals: list[str] = []
        curr = technicals[-1]
        prev = technicals[-2]

        # RSI extremes
        if curr.rsi is not None:
            if curr.rsi > 70:
                signals.append(f"RSI OVERBOUGHT: {curr.rsi:.1f}")
            elif curr.rsi < 30:
                signals.append(f"RSI OVERSOLD: {curr.rsi:.1f}")

        # MACD crossover
        if (curr.macd is not None and curr.macd_signal is not None and
                prev.macd is not None and prev.macd_signal is not None):
            if prev.macd < prev.macd_signal and curr.macd >= curr.macd_signal:
                signals.append("MACD BULLISH CROSSOVER (MACD crossed above signal)")
            elif prev.macd > prev.macd_signal and curr.macd <= curr.macd_signal:
                signals.append("MACD BEARISH CROSSOVER (MACD crossed below signal)")

        # EMA crossovers
        if curr.ema_9 is not None and curr.ema_21 is not None:
            if prev.ema_9 and prev.ema_21:
                if prev.ema_9 < prev.ema_21 and curr.ema_9 >= curr.ema_21:
                    signals.append("EMA(9/21) GOLDEN CROSSOVER")
                elif prev.ema_9 > prev.ema_21 and curr.ema_9 <= curr.ema_21:
                    signals.append("EMA(9/21) DEATH CROSSOVER")

        # SMA Golden/Death cross
        if curr.sma_50 is not None and curr.sma_200 is not None:
            if prev.sma_50 and prev.sma_200:
                if prev.sma_50 < prev.sma_200 and curr.sma_50 >= curr.sma_200:
                    signals.append("GOLDEN CROSS (SMA50 > SMA200)")
                elif prev.sma_50 > prev.sma_200 and curr.sma_50 <= curr.sma_200:
                    signals.append("DEATH CROSS (SMA50 < SMA200)")

        # Price vs Bollinger Bands
        if prices and curr.bb_upper is not None and curr.bb_lower is not None:
            price = prices[-1].close
            if price > curr.bb_upper:
                signals.append(f"PRICE ABOVE UPPER BOLLINGER (${price:.2f} > ${curr.bb_upper:.2f})")
            elif price < curr.bb_lower:
                signals.append(f"PRICE BELOW LOWER BOLLINGER (${price:.2f} < ${curr.bb_lower:.2f})")

        # ADX trend strength
        if curr.adx is not None:
            if curr.adx > 40:
                signals.append(f"STRONG TREND (ADX={curr.adx:.1f})")
            elif curr.adx < 20:
                signals.append(f"WEAK/NO TREND (ADX={curr.adx:.1f})")

        # Ichimoku cloud position
        if (prices and curr.ichi_span_a is not None and curr.ichi_span_b is not None):
            price = prices[-1].close
            cloud_top = max(curr.ichi_span_a, curr.ichi_span_b)
            cloud_bottom = min(curr.ichi_span_a, curr.ichi_span_b)
            if price > cloud_top:
                signals.append("PRICE ABOVE ICHIMOKU CLOUD (bullish)")
            elif price < cloud_bottom:
                signals.append("PRICE BELOW ICHIMOKU CLOUD (bearish)")
            else:
                signals.append("PRICE INSIDE ICHIMOKU CLOUD (indecision)")

        # Stochastic extremes
        if curr.stoch_k is not None:
            if curr.stoch_k > 80:
                signals.append(f"STOCHASTIC OVERBOUGHT: %K={curr.stoch_k:.1f}")
            elif curr.stoch_k < 20:
                signals.append(f"STOCHASTIC OVERSOLD: %K={curr.stoch_k:.1f}")

        # Volume confirmation
        if curr.cmf is not None:
            if curr.cmf > 0.1:
                signals.append(f"STRONG MONEY FLOW (CMF={curr.cmf:.3f})")
            elif curr.cmf < -0.1:
                signals.append(f"NEGATIVE MONEY FLOW (CMF={curr.cmf:.3f})")

        return signals
