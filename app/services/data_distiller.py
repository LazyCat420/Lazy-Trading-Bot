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


def _val(obj: Any, key: str, default: Any = None) -> Any:
    """Get a value from either a dict or an object attribute.

    DuckDB rows may arrive as dicts (via dict(zip(cols, row))) or as
    dataclass/namedtuple objects.  This helper handles both transparently.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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

        # Extract close prices (handle both dict and object rows)
        closes = [float(_val(p, "close")) for p in prices if _val(p, "close") is not None]
        if len(closes) < 5:
            parts.append("Insufficient price data for pattern analysis.")
            return "\n".join(parts)

        # ── Current Price Context ──
        latest = closes[-1]
        parts.append(f"Current Price: ${latest:.2f}")

        # Price change over multiple timeframes
        for label, days in [("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126)]:
            if len(closes) >= days:
                change = (closes[-1] / closes[-days] - 1) * 100
                parts.append(f"  {label}: {change:+.1f}%")

        # ── Trend Regime Detection ──
        parts.append("\n--- Trend Regime ---")
        if technicals:
            t = technicals[-1]
            sma20 = _val(t, "sma_20")
            sma50 = _val(t, "sma_50")
            sma200 = _val(t, "sma_200")

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
                parts.append(f"  Distance from SMA200: {(latest / sma200 - 1) * 100:+.1f}%")

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
            rsi = _val(t, "rsi")
            macd = _val(t, "macd")
            macd_signal = _val(t, "macd_signal")
            macd_hist = _val(t, "macd_hist")
            adx = _val(t, "adx")

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
                    hist_vals = [_val(t2, "macd_hist") for t2 in technicals[-5:]]
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
            recent_vols = [float(_val(p, "volume")) for p in prices[-20:] if _val(p, "volume")]
            older_vols = [float(_val(p, "volume")) for p in prices[-60:-20] if _val(p, "volume")]

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
        pe = _val(f, "trailing_pe", 0) or 0
        fpe = _val(f, "forward_pe", 0) or 0
        ps_ratio = _val(f, "price_to_sales", 0) or 0
        pb_ratio = _val(f, "price_to_book", 0) or 0
        peg = _val(f, "peg_ratio", 0) or 0

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
            parts.append(
                f"  P/S: {ps_ratio:.1f} — {'Expensive' if ps_ratio > 10 else 'Fair' if ps_ratio > 3 else 'Value'}"
            )
        if pb_ratio > 0:
            parts.append(
                f"  P/B: {pb_ratio:.1f} — {'Premium' if pb_ratio > 5 else 'Fair' if pb_ratio > 1 else 'Below book value'}"
            )
        if peg > 0:
            if peg < 1:
                parts.append(f"  PEG: {peg:.2f} — UNDERVALUED relative to growth")
            elif peg > 2:
                parts.append(f"  PEG: {peg:.2f} — OVERVALUED relative to growth")

        # ── Revenue Trajectory ──
        if financial_history and len(financial_history) >= 2:
            parts.append("\n--- Revenue Trajectory ---")
            revs = [
                (_val(fh, "year", 0), _val(fh, "revenue", 0))
                for fh in financial_history
                if _val(fh, "revenue")
            ]
            revs.sort()
            if len(revs) >= 2:
                for i in range(1, len(revs)):
                    if revs[i - 1][1] and revs[i - 1][1] > 0:
                        growth = (revs[i][1] / revs[i - 1][1] - 1) * 100
                        parts.append(
                            f"  {revs[i][0]}: ${revs[i][1] / 1e9:.1f}B ({growth:+.1f}% YoY)"
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
            ocf = _val(cf, "operating_cashflow", 0) or 0
            ni = _val(f, "net_income", 0) or 0
            fcf = _val(cf, "free_cashflow", 0) or 0

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
                        f"  OCF/NI: {quality:.1f}x — LOW quality (accruals inflating profits)"
                    )

            if fcf:
                mcap = _val(f, "market_cap", 0) or 0
                if mcap > 0:
                    fcf_yield = (fcf / mcap) * 100
                    parts.append(
                        f"  FCF Yield: {fcf_yield:.1f}% — "
                        f"{'Attractive' if fcf_yield > 5 else 'Normal' if fcf_yield > 2 else 'Low'}"
                    )

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
                    parts.append(f"  Earnings Yield Gap: {eyg:+.1%} — CHEAP vs bonds (buy signal)")
                elif eyg < -0.01:
                    parts.append(f"  Earnings Yield Gap: {eyg:+.1%} — EXPENSIVE vs bonds")
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
            sharpe = _val(rm, "sharpe_ratio", 0) or 0
            sortino = _val(rm, "sortino_ratio", 0) or 0

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
            var95 = _val(rm, "var_95", 0) or 0
            cvar95 = _val(rm, "cvar_95", 0) or 0

            parts.append("\n--- Worst-Case Scenarios (per $10,000) ---")
            parts.append(f"  VaR(95%): ${abs(var95) * 10000:.0f} daily loss on 1-in-20 bad day")
            parts.append(f"  CVaR(95%): ${abs(cvar95) * 10000:.0f} avg loss when exceeding VaR")

            mdd = _val(rm, "max_drawdown", 0) or 0
            cur_dd = _val(rm, "current_drawdown", 0) or 0
            parts.append(f"  Max Historical Drawdown: {mdd * 100:.1f}%")
            parts.append(
                f"  Current Drawdown: {cur_dd * 100:.1f}% "
                f"({'room to deteriorate' if cur_dd > mdd * 0.5 else 'well within historical range'})"
            )

        # ── Position Sizing Guidance ──
        if scorecard:
            parts.append("\n--- Position Sizing (Kelly Criterion) ---")
            parts.append(f"  Full Kelly: {scorecard.kelly_fraction:.1%} of portfolio")
            parts.append(f"  Half-Kelly (recommended): {scorecard.half_kelly:.1%} of portfolio")

            if scorecard.omega_ratio > 1.5:
                parts.append(
                    f"  Omega Ratio: {scorecard.omega_ratio:.2f} — "
                    f"Gains outweigh losses (favorable)"
                )
            elif scorecard.omega_ratio < 0.8:
                parts.append(
                    f"  Omega Ratio: {scorecard.omega_ratio:.2f} — ⚠️ Losses outweigh gains"
                )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # News Distillation
    # ------------------------------------------------------------------

    def distill_news(self, articles: list[Any]) -> str:
        """Distill news articles into a sentiment-weighted summary.

        Merges rows from both news_articles (yfinance summaries) and
        news_full_articles (RSS/EDGAR full text).  Deduplicates by title.
        """
        parts = ["=== PRE-COMPUTED NEWS ANALYSIS ===\n"]

        if not articles:
            parts.append("No recent news articles available.")
            return "\n".join(parts)

        # Deduplicate by title (case-insensitive)
        seen_titles: set[str] = set()
        unique: list[Any] = []
        for a in articles:
            title = str(_val(a, "title", "") or "").strip().lower()
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique.append(a)

        parts.append(f"Total articles: {len(unique)}")

        # Sort by recency (newest first)
        def _pub_key(a: Any) -> str:
            return str(_val(a, "published_at", "") or "")
        unique.sort(key=_pub_key, reverse=True)

        # Top headlines
        parts.append("\n--- Recent Headlines ---")
        for a in unique[:8]:
            title = _val(a, "title", "Untitled")
            publisher = _val(a, "publisher", "")
            summary = _val(a, "summary", "") or _val(a, "content", "")
            pub_str = f" ({publisher})" if publisher else ""
            parts.append(f"• {title}{pub_str}")
            if summary:
                # Truncate long summaries
                snippet = str(summary)[:200]
                if len(str(summary)) > 200:
                    snippet += "…"
                parts.append(f"  → {snippet}")

        # Source breakdown
        sources = {}
        for a in unique:
            src = _val(a, "source", "") or _val(a, "source_feed", "") or "unknown"
            sources[src] = sources.get(src, 0) + 1
        if sources:
            parts.append("\n--- Source Breakdown ---")
            for src, count in sorted(sources.items(), key=lambda x: -x[1]):
                parts.append(f"  {src}: {count} articles")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # YouTube Distillation
    # ------------------------------------------------------------------

    def distill_youtube(
        self,
        transcripts: list[Any],
        trading_data: list[Any] | None = None,
    ) -> str:
        """Distill YouTube transcripts + structured trading data.

        Uses TextRank-style extractive summarization: pick sentences
        with the most keyword overlap as the most representative.
        """
        parts = ["=== PRE-COMPUTED YOUTUBE ANALYSIS ===\n"]

        if not transcripts and not trading_data:
            parts.append("No YouTube data available.")
            return "\n".join(parts)

        # Structured trading data first (higher signal)
        if trading_data:
            parts.append("--- Structured Trading Data ---")
            for td in trading_data[:3]:
                title = _val(td, "title", "")
                channel = _val(td, "channel", "")
                data = _val(td, "trading_data", "")
                parts.append(f"• {title} ({channel})")
                if data:
                    snippet = str(data)[:300]
                    if len(str(data)) > 300:
                        snippet += "…"
                    parts.append(f"  Data: {snippet}")

        # Transcript analysis
        if transcripts:
            parts.append(f"\n--- Transcript Analysis ({len(transcripts)} videos) ---")
            for t in transcripts[:3]:
                title = _val(t, "title", "Unknown")
                channel = _val(t, "channel", "")
                duration = _val(t, "duration_seconds", 0) or 0
                raw = str(_val(t, "raw_transcript", "") or "")

                dur_str = f"{duration // 60}m" if duration else "?"
                parts.append(f"• {title} ({channel}, {dur_str})")

                if raw:
                    # Simple extractive summary: first 3 sentences
                    sentences = [s.strip() for s in raw.replace("\n", ". ").split(". ") if len(s.strip()) > 20]
                    top = sentences[:3]
                    if top:
                        parts.append(f"  Key points: {'. '.join(top)[:300]}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Smart Money Distillation (13F + Congressional Trades)
    # ------------------------------------------------------------------

    def distill_smart_money(
        self,
        holdings_13f: list[Any],
        congress_trades: list[Any],
    ) -> str:
        """Distill institutional (13F) and congressional trading activity."""
        parts = ["=== PRE-COMPUTED SMART MONEY ANALYSIS ===\n"]

        if not holdings_13f and not congress_trades:
            parts.append("No smart money data available.")
            return "\n".join(parts)

        # 13F Holdings
        if holdings_13f:
            parts.append("--- Institutional Holdings (SEC 13F) ---")
            total_value = sum(float(_val(h, "value_usd", 0) or 0) for h in holdings_13f)
            total_shares = sum(int(_val(h, "shares", 0) or 0) for h in holdings_13f)
            parts.append(f"Total filings: {len(holdings_13f)}")
            if total_value > 0:
                parts.append(f"Total institutional value: ${total_value:,.0f}")
                parts.append(f"Total shares held: {total_shares:,}")

            # Top holders by value
            sorted_h = sorted(holdings_13f, key=lambda h: float(_val(h, "value_usd", 0) or 0), reverse=True)
            for h in sorted_h[:5]:
                cik = _val(h, "cik", "?")
                val = float(_val(h, "value_usd", 0) or 0)
                shares = int(_val(h, "shares", 0) or 0)
                quarter = _val(h, "filing_quarter", "?")
                parts.append(f"  CIK {cik}: ${val:,.0f} ({shares:,} shares, Q{quarter})")

        # Congressional Trades
        if congress_trades:
            parts.append("\n--- Congressional Trades ---")
            buys = [t for t in congress_trades if str(_val(t, "tx_type", "")).lower() in ("purchase", "buy")]
            sells = [t for t in congress_trades if str(_val(t, "tx_type", "")).lower() in ("sale", "sell", "sale_full", "sale_partial")]
            parts.append(f"Total trades: {len(congress_trades)} (Buys: {len(buys)}, Sells: {len(sells)})")

            for t in congress_trades[:5]:
                member = _val(t, "member_name", "Unknown")
                chamber = _val(t, "chamber", "?")
                tx_type = _val(t, "tx_type", "?")
                amount = _val(t, "amount_range", "?")
                tx_date = _val(t, "tx_date", "?")
                parts.append(f"  {member} ({chamber}): {tx_type} {amount} on {tx_date}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Reddit Distillation
    # ------------------------------------------------------------------

    def distill_reddit(
        self,
        scores: list[Any],
        snippets: list[Any] | None = None,
        ticker: str | None = None,
    ) -> str:
        """Distill Reddit/social sentiment data from ticker_scores + discovered_tickers.

        When a ticker is provided, also pulls full thread data from the
        reddit_threads table for richer context.
        """
        parts = ["=== PRE-COMPUTED REDDIT ANALYSIS ===\n"]

        if not scores and not snippets:
            parts.append("No Reddit/social data available.")
            return "\n".join(parts)

        # Aggregate scores
        if scores:
            for s in scores[:3]:
                total = _val(s, "total_score", 0) or 0
                reddit = _val(s, "reddit_score", 0) or 0
                mentions = _val(s, "mention_count", 0) or 0
                sentiment = _val(s, "sentiment_hint", "neutral")
                parts.append(f"Social Score: {total:.1f} (Reddit: {reddit:.1f})")
                parts.append(f"Mentions: {mentions}, Sentiment: {sentiment}")

        # ── Rich thread data from reddit_threads table ──
        if ticker:
            try:
                from app.services.reddit_service import RedditCollector
                threads = RedditCollector.get_threads_for_ticker(ticker, limit=5)
                if threads:
                    parts.append(f"\n--- Reddit Threads ({len(threads)}) ---")
                    for t in threads:
                        subreddit = t.get("subreddit", "?")
                        title = t.get("title", "Untitled")
                        score = t.get("score", 0)
                        num_comments = t.get("num_comments", 0)
                        parts.append(
                            f"• r/{subreddit} [{score}↑ {num_comments}💬]: {title[:120]}"
                        )

                        # Include body excerpt if available
                        selftext = t.get("selftext", "")
                        if selftext:
                            parts.append(f"  Post: {selftext[:200]}{'…' if len(selftext) > 200 else ''}")

                        # Top comments
                        comments = t.get("comments", [])
                        if comments:
                            for c in comments[:2]:
                                parts.append(f"  → {str(c)[:150]}")
            except Exception:
                pass  # Graceful fallback

        # Discovery snippets (legacy / fallback)
        if snippets:
            parts.append("\n--- Community Context ---")
            for sn in snippets[:5]:
                context = _val(sn, "context_snippet", "")
                source_detail = _val(sn, "source_detail", "")
                sentiment = _val(sn, "sentiment_hint", "neutral")
                if context:
                    parts.append(f"• [{sentiment}] {str(context)[:150]}")
                    if source_detail:
                        parts.append(f"  Source: {source_detail}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Peer Comparison Distillation
    # ------------------------------------------------------------------

    def distill_peers(
        self,
        peer_fundamentals: list[Any],
        primary_fundamentals: Any | None = None,
    ) -> str:
        """Distill peer comparison data — sector/industry benchmarking."""
        parts = ["=== PRE-COMPUTED PEER ANALYSIS ===\n"]

        if not peer_fundamentals:
            parts.append("No peer comparison data available.")
            return "\n".join(parts)

        parts.append(f"Peers analyzed: {len(peer_fundamentals)}")

        # Key valuation metrics comparison
        metrics = ["pe_ratio", "pb_ratio", "ps_ratio", "ev_ebitda",
                    "profit_margin", "roe", "revenue_growth"]

        for metric in metrics:
            values = []
            for pf in peer_fundamentals:
                val = _val(pf, metric)
                ticker = _val(pf, "ticker", "?")
                if val is not None:
                    values.append((ticker, float(val)))
            if values:
                avg = sum(v for _, v in values) / len(values)
                parts.append(f"\n{metric}:")
                for ticker, val in values:
                    diff = ((val / avg) - 1) * 100 if avg else 0
                    parts.append(f"  {ticker}: {val:.2f} ({diff:+.0f}% vs avg)")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Analyst Consensus Distillation
    # ------------------------------------------------------------------

    def distill_analyst_consensus(self, analyst_rows: list[Any]) -> str:
        """Distill analyst ratings and price targets.

        NO current_price param — upside % is computed at the strategist layer
        where live price is available.
        """
        parts = ["=== PRE-COMPUTED ANALYST CONSENSUS ===\n"]

        if not analyst_rows:
            parts.append("No analyst data available.")
            return "\n".join(parts)

        # Use most recent snapshot
        latest = analyst_rows[-1]

        target_mean = _val(latest, "target_mean")
        target_median = _val(latest, "target_median")
        target_high = _val(latest, "target_high")
        target_low = _val(latest, "target_low")
        num_analysts = _val(latest, "num_analysts", 0) or 0

        parts.append(f"Coverage: {num_analysts} analysts")

        if target_mean:
            parts.append(f"Target Mean: ${target_mean:.2f}")
        if target_median:
            parts.append(f"Target Median: ${target_median:.2f}")
        if target_high and target_low:
            spread = target_high - target_low
            parts.append(f"Target Range: ${target_low:.2f} – ${target_high:.2f} (spread: ${spread:.2f})")

        # Rating distribution
        sb = int(_val(latest, "strong_buy", 0) or 0)
        b = int(_val(latest, "buy", 0) or 0)
        h = int(_val(latest, "hold", 0) or 0)
        s = int(_val(latest, "sell", 0) or 0)
        ss = int(_val(latest, "strong_sell", 0) or 0)
        total_ratings = sb + b + h + s + ss

        if total_ratings > 0:
            parts.append(f"\n--- Rating Distribution ({total_ratings} ratings) ---")
            parts.append(f"  Strong Buy: {sb} | Buy: {b} | Hold: {h} | Sell: {s} | Strong Sell: {ss}")
            bullish_pct = (sb + b) / total_ratings * 100
            bearish_pct = (s + ss) / total_ratings * 100
            parts.append(f"  Bullish: {bullish_pct:.0f}% | Bearish: {bearish_pct:.0f}%")

            if bullish_pct > 70:
                parts.append("  → STRONG BUY CONSENSUS")
            elif bullish_pct > 50:
                parts.append("  → MODERATE BUY CONSENSUS")
            elif bearish_pct > 50:
                parts.append("  → SELL CONSENSUS")
            else:
                parts.append("  → MIXED/HOLD CONSENSUS")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Insider Activity Distillation
    # ------------------------------------------------------------------

    def distill_insider_activity(self, insider_rows: list[Any]) -> str:
        """Distill insider buying/selling activity.

        Parses raw_transactions (JSON VARCHAR) for individual trade details.
        """
        import json as _json

        parts = ["=== PRE-COMPUTED INSIDER ACTIVITY ===\n"]

        if not insider_rows:
            parts.append("No insider activity data available.")
            return "\n".join(parts)

        # Use most recent snapshot
        latest = insider_rows[-1]

        net_buying = _val(latest, "net_insider_buying_90d", 0) or 0
        inst_pct = _val(latest, "institutional_ownership_pct", 0) or 0

        parts.append(f"Net Insider Buying (90d): ${net_buying:,.0f}")
        parts.append(f"Institutional Ownership: {inst_pct:.1f}%")

        if net_buying > 0:
            parts.append("→ INSIDERS ARE NET BUYERS (bullish signal)")
        elif net_buying < 0:
            parts.append("→ INSIDERS ARE NET SELLERS (watch for dilution or pessimism)")
        else:
            parts.append("→ No significant insider trading activity")

        # Parse raw transactions if available
        raw_tx = _val(latest, "raw_transactions", "")
        if raw_tx:
            try:
                transactions = _json.loads(raw_tx) if isinstance(raw_tx, str) else raw_tx
                if isinstance(transactions, list):
                    parts.append(f"\n--- Individual Transactions ({len(transactions)}) ---")
                    for tx in transactions[:5]:
                        if isinstance(tx, dict):
                            name = tx.get("insider", tx.get("name", "Unknown"))
                            action = tx.get("action", tx.get("type", "?"))
                            shares = tx.get("shares", tx.get("qty", "?"))
                            value = tx.get("value", "")
                            parts.append(f"  {name}: {action} {shares} shares")
                            if value:
                                parts.append(f"    Value: ${value:,}" if isinstance(value, (int, float)) else f"    Value: {value}")
            except (_json.JSONDecodeError, TypeError):
                pass  # raw_transactions wasn't valid JSON

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Earnings Catalyst Distillation
    # ------------------------------------------------------------------

    def distill_earnings_catalyst(self, earnings_rows: list[Any]) -> str:
        """Distill earnings calendar and surprise data."""
        parts = ["=== PRE-COMPUTED EARNINGS CATALYST ===\n"]

        if not earnings_rows:
            parts.append("No earnings calendar data available.")
            return "\n".join(parts)

        # Use most recent snapshot
        latest = earnings_rows[-1]

        next_date = _val(latest, "next_earnings_date")
        days_until = _val(latest, "days_until_earnings")
        estimate = _val(latest, "earnings_estimate")
        prev_actual = _val(latest, "previous_actual")
        prev_estimate = _val(latest, "previous_estimate")
        surprise = _val(latest, "surprise_pct")

        if next_date:
            parts.append(f"Next Earnings: {next_date}")
        if days_until is not None:
            parts.append(f"Days Until: {days_until}")
            if days_until <= 14:
                parts.append("⚠️ EARNINGS IMMINENT — elevated volatility expected")
            elif days_until <= 30:
                parts.append("Earnings approaching — options premiums may be elevated")

        if estimate:
            parts.append(f"Consensus Estimate: ${estimate:.2f}")
        if prev_actual:
            parts.append(f"Previous Actual: ${prev_actual:.2f}")

        # Estimate revision detection
        if prev_estimate and estimate:
            revision_pct = (estimate / prev_estimate - 1) * 100 if prev_estimate else 0
            if abs(revision_pct) > 1:
                direction = "UPGRADED" if revision_pct > 0 else "DOWNGRADED"
                parts.append(f"Estimate Revision: {direction} {abs(revision_pct):.1f}% from ${prev_estimate:.2f}")

        # Last earnings surprise
        if surprise is not None:
            if surprise > 5:
                parts.append(f"Last Surprise: +{surprise:.1f}% BEAT (strong)")
            elif surprise > 0:
                parts.append(f"Last Surprise: +{surprise:.1f}% beat")
            elif surprise < -5:
                parts.append(f"Last Surprise: {surprise:.1f}% MISS (significant)")
            elif surprise < 0:
                parts.append(f"Last Surprise: {surprise:.1f}% miss")
            else:
                parts.append("Last Surprise: In-line with estimates")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Cross-Signal Synthesis
    # ------------------------------------------------------------------

    def distill_cross_signals(
        self,
        price_analysis: str,
        fund_analysis: str,
        risk_analysis: str,
        news_analysis: str,
        youtube_analysis: str,
        smart_money_analysis: str,
        reddit_analysis: str,
        peer_analysis: str,
        analyst_consensus_analysis: str,
        insider_activity_analysis: str,
        earnings_catalyst_analysis: str,
    ) -> str:
        """Cross-reference all distilled signals to find convergences/divergences.

        All 11 params are str — distilled text from other methods.
        """
        parts = ["=== CROSS-SIGNAL SYNTHESIS ===\n"]

        # Count available signals
        signals = {
            "price": price_analysis,
            "fundamentals": fund_analysis,
            "risk": risk_analysis,
            "news": news_analysis,
            "youtube": youtube_analysis,
            "smart_money": smart_money_analysis,
            "reddit": reddit_analysis,
            "peers": peer_analysis,
            "analyst": analyst_consensus_analysis,
            "insider": insider_activity_analysis,
            "earnings": earnings_catalyst_analysis,
        }

        available = {k: v for k, v in signals.items() if v and "No " not in v[:20] and "no " not in v[:20]}
        parts.append(f"Signals available: {len(available)}/{len(signals)}")
        if len(available) < len(signals):
            missing = set(signals.keys()) - set(available.keys())
            parts.append(f"Missing: {', '.join(sorted(missing))}")

        # Detect bullish/bearish keywords across all signals
        bullish_kw = ["BUY", "UPTREND", "STRONG", "BULLISH", "BEAT", "UPGRADED", "NET BUYERS", "GOLDEN CROSS"]
        bearish_kw = ["SELL", "DOWNTREND", "BEARISH", "MISS", "DOWNGRADED", "NET SELLERS", "DEATH CROSS", "DANGER"]

        bullish_count = 0
        bearish_count = 0
        bull_sources: list[str] = []
        bear_sources: list[str] = []

        for name, text in available.items():
            upper = text.upper()
            b = sum(1 for kw in bullish_kw if kw in upper)
            s = sum(1 for kw in bearish_kw if kw in upper)
            if b > s:
                bullish_count += 1
                bull_sources.append(name)
            elif s > b:
                bearish_count += 1
                bear_sources.append(name)

        parts.append(f"\n--- Signal Direction ---")
        parts.append(f"Bullish signals: {bullish_count} ({', '.join(bull_sources) if bull_sources else 'none'})")
        parts.append(f"Bearish signals: {bearish_count} ({', '.join(bear_sources) if bear_sources else 'none'})")

        if bullish_count > bearish_count * 2:
            parts.append("→ STRONG CONVERGENCE: Multiple data sources align BULLISH")
        elif bearish_count > bullish_count * 2:
            parts.append("→ STRONG CONVERGENCE: Multiple data sources align BEARISH")
        elif bullish_count > 0 and bearish_count > 0:
            parts.append("→ DIVERGENCE: Mixed signals — warrants deeper investigation")
        elif bullish_count == 0 and bearish_count == 0:
            parts.append("→ NEUTRAL: No strong directional signals detected")

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
            p50 = _val(prev, "sma_50")
            c50 = _val(curr, "sma_50")
            p200 = _val(prev, "sma_200")
            c200 = _val(curr, "sma_200")
            if p50 and c50 and p200 and c200:
                if p50 < p200 and c50 >= c200:
                    signals.append(f"GOLDEN CROSS (SMA50 crossed above SMA200) {10 - i}d ago")
                elif p50 > p200 and c50 <= c200:
                    signals.append(f"DEATH CROSS (SMA50 crossed below SMA200) {10 - i}d ago")

            # MACD crossover
            pm = _val(prev, "macd")
            cm = _val(curr, "macd")
            ps = _val(prev, "macd_signal")
            cs = _val(curr, "macd_signal")
            if pm is not None and cm is not None and ps is not None and cs is not None:
                if pm < ps and cm >= cs:
                    signals.append(f"MACD bullish crossover {10 - i}d ago")
                elif pm > ps and cm <= cs:
                    signals.append(f"MACD bearish crossover {10 - i}d ago")

            # RSI crossing 30/70
            pr = _val(prev, "rsi")
            cr = _val(curr, "rsi")
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

        rsi_first = [_val(t, "rsi") for t in technicals[-20:-mid]]
        rsi_second = [_val(t, "rsi") for t in technicals[-mid:]]

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
    def _cluster_levels(
        levels: list[float], reference: float, threshold: float = 0.02
    ) -> list[float]:
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
