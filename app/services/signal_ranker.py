"""Signal Ranker — pure-Python anomaly/interest scorer for extracted domain data.

No LLM calls.  Scans the domain_data dict produced by extract_domain_data()
and scores each domain for "interestingness" — extreme values, anomalies,
contradictions, or catalyst events.  Outputs ranked seeds that the
InvestigationAgent will use to drive its ReAct research loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.utils.logger import logger


# ── Seed dataclass ────────────────────────────────────────────────

@dataclass
class Seed:
    """A research seed — a ranked signal that the InvestigationAgent
    should investigate with targeted tool calls."""

    category: str                       # e.g. "debt_concern", "insider_selling"
    summary: str                        # human-readable 1-liner
    score: float                        # 0-1 interestingness
    source_domain: str                  # which domain produced it
    suggested_tools: list[str] = field(default_factory=list)
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Seed({self.category}, score={self.score:.2f}, tools={self.suggested_tools})"


# ── Tool routing map ─────────────────────────────────────────────

CATEGORY_TOOLS: dict[str, list[str]] = {
    "debt_concern":        ["compare_financials", "check_insider_activity"],
    "valuation_extreme":   ["compare_financials", "get_earnings_calendar"],
    "insider_selling":     ["check_insider_activity", "get_technicals_detail"],
    "insider_buying":      ["check_insider_activity", "get_technicals_detail"],
    "earnings_imminent":   ["get_earnings_calendar", "get_technicals_detail"],
    "momentum_extreme":    ["get_technicals_detail", "get_price_history"],
    "bearish_technicals":  ["get_technicals_detail", "get_price_history"],
    "bullish_technicals":  ["get_technicals_detail", "get_price_history"],
    "smart_money_flow":    ["fetch_sec_filings", "check_insider_activity"],
    "news_catalyst":       ["search_news", "get_technicals_detail"],
    "social_momentum":     ["search_reddit_sentiment", "search_news"],
    "congressional_trade": ["check_insider_activity", "search_news"],
    "general_review":      ["get_technicals_detail", "compare_financials"],
}


# ── Number extractor helper ──────────────────────────────────────

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _extract_number(text: str, prefix: str) -> float | None:
    """Find a number after a given prefix in text."""
    idx = text.lower().find(prefix.lower())
    if idx == -1:
        return None
    substring = text[idx + len(prefix): idx + len(prefix) + 40]
    m = _NUM_RE.search(substring)
    if m:
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            return None
    return None


# ══════════════════════════════════════════════════════════════════
# Signal Ranker
# ══════════════════════════════════════════════════════════════════

class SignalRanker:
    """Score domain_data chunks and produce ranked seeds for investigation.

    Usage:
        ranker = SignalRanker()
        seeds = ranker.rank(domain_data, symbol)
        # seeds is a list[Seed] sorted by score descending, max 3-4
    """

    def rank(
        self,
        domain_data: dict[str, str],
        symbol: str,
        max_seeds: int = 4,
    ) -> list[Seed]:
        """Scan all domains and return the top-N most interesting seeds."""
        seeds: list[Seed] = []

        tech_text = domain_data.get("technical", "")
        fund_text = domain_data.get("fundamental", "")
        sent_text = domain_data.get("sentiment", "")
        smart_text = domain_data.get("smart_money", "")
        risk_text = domain_data.get("risk", "")

        # ── Technical signals ─────────────────────────────────
        seeds.extend(self._scan_technicals(tech_text, symbol))

        # ── Fundamental signals ───────────────────────────────
        seeds.extend(self._scan_fundamentals(fund_text, symbol))

        # ── Sentiment / News signals ──────────────────────────
        seeds.extend(self._scan_sentiment(sent_text, symbol))

        # ── Smart money signals ───────────────────────────────
        seeds.extend(self._scan_smart_money(smart_text, symbol))

        # ── Risk signals ──────────────────────────────────────
        seeds.extend(self._scan_risk(risk_text, symbol))

        # Always include at least one general seed if nothing interesting
        if not seeds:
            seeds.append(Seed(
                category="general_review",
                summary=f"No strong signals detected for {symbol} — run baseline review",
                score=0.2,
                source_domain="all",
                suggested_tools=CATEGORY_TOOLS["general_review"],
            ))

        # Sort by score desc, take top N
        seeds.sort(key=lambda s: s.score, reverse=True)
        top = seeds[:max_seeds]

        logger.info(
            "[SignalRanker] %s: %d raw signals → top %d seeds: %s",
            symbol, len(seeds), len(top),
            [(s.category, round(s.score, 2)) for s in top],
        )
        return top

    # ── Domain scanners ───────────────────────────────────────

    def _scan_technicals(self, text: str, symbol: str) -> list[Seed]:
        seeds: list[Seed] = []
        if not text:
            return seeds

        rsi = _extract_number(text, "rsi=")
        if rsi is not None:
            if rsi > 75:
                seeds.append(Seed(
                    category="momentum_extreme",
                    summary=f"RSI={rsi:.0f} — extremely overbought",
                    score=0.8,
                    source_domain="technical",
                    suggested_tools=CATEGORY_TOOLS["momentum_extreme"],
                    raw_evidence={"rsi": rsi},
                ))
            elif rsi < 25:
                seeds.append(Seed(
                    category="momentum_extreme",
                    summary=f"RSI={rsi:.0f} — extremely oversold",
                    score=0.8,
                    source_domain="technical",
                    suggested_tools=CATEGORY_TOOLS["momentum_extreme"],
                    raw_evidence={"rsi": rsi},
                ))

        # MACD histogram direction
        macd_hist = _extract_number(text, "macd_hist=")
        adx = _extract_number(text, "adx=")
        if macd_hist is not None and adx is not None and adx > 25:
            if macd_hist < 0:
                seeds.append(Seed(
                    category="bearish_technicals",
                    summary=f"MACD bearish (hist={macd_hist:.4f}) with strong trend ADX={adx:.0f}",
                    score=0.65,
                    source_domain="technical",
                    suggested_tools=CATEGORY_TOOLS["bearish_technicals"],
                    raw_evidence={"macd_hist": macd_hist, "adx": adx},
                ))
            elif macd_hist > 0:
                seeds.append(Seed(
                    category="bullish_technicals",
                    summary=f"MACD bullish (hist={macd_hist:.4f}) with strong trend ADX={adx:.0f}",
                    score=0.6,
                    source_domain="technical",
                    suggested_tools=CATEGORY_TOOLS["bullish_technicals"],
                    raw_evidence={"macd_hist": macd_hist, "adx": adx},
                ))

        return seeds

    def _scan_fundamentals(self, text: str, symbol: str) -> list[Seed]:
        seeds: list[Seed] = []
        if not text:
            return seeds

        # Debt / leverage
        dte = _extract_number(text, "debt_to_equity:")
        if dte is not None and dte > 1.5:
            seeds.append(Seed(
                category="debt_concern",
                summary=f"High debt/equity={dte:.2f} — leverage risk",
                score=min(0.5 + (dte - 1.5) * 0.15, 0.95),
                source_domain="fundamental",
                suggested_tools=CATEGORY_TOOLS["debt_concern"],
                raw_evidence={"debt_to_equity": dte},
            ))

        # Valuation extremes
        pe = _extract_number(text, "trailing_pe:")
        if pe is not None:
            if pe > 50:
                seeds.append(Seed(
                    category="valuation_extreme",
                    summary=f"P/E={pe:.1f} — extremely overvalued",
                    score=0.7,
                    source_domain="fundamental",
                    suggested_tools=CATEGORY_TOOLS["valuation_extreme"],
                    raw_evidence={"trailing_pe": pe},
                ))
            elif 0 < pe < 8:
                seeds.append(Seed(
                    category="valuation_extreme",
                    summary=f"P/E={pe:.1f} — potentially deep value",
                    score=0.65,
                    source_domain="fundamental",
                    suggested_tools=CATEGORY_TOOLS["valuation_extreme"],
                    raw_evidence={"trailing_pe": pe},
                ))

        # Negative margins
        margin = _extract_number(text, "profit_margin:")
        if margin is not None and margin < 0:
            seeds.append(Seed(
                category="debt_concern",
                summary=f"Negative profit margin={margin:.2%} — cash burn",
                score=0.7,
                source_domain="fundamental",
                suggested_tools=CATEGORY_TOOLS["debt_concern"],
                raw_evidence={"profit_margin": margin},
            ))

        return seeds

    def _scan_sentiment(self, text: str, symbol: str) -> list[Seed]:
        seeds: list[Seed] = []
        if not text:
            return seeds

        # Count news articles
        news_count = text.lower().count("•")
        if news_count >= 3:
            seeds.append(Seed(
                category="news_catalyst",
                summary=f"{news_count} recent news items — potential catalyst event",
                score=0.5 + min(news_count * 0.05, 0.3),
                source_domain="sentiment",
                suggested_tools=CATEGORY_TOOLS["news_catalyst"],
                raw_evidence={"news_count": news_count},
            ))

        # Reddit mention signals
        if "reddit mentions" in text.lower():
            reddit_count = text.lower().count("reddit")
            if reddit_count >= 2:
                seeds.append(Seed(
                    category="social_momentum",
                    summary=f"Active Reddit discussion ({reddit_count} mentions)",
                    score=0.45,
                    source_domain="sentiment",
                    suggested_tools=CATEGORY_TOOLS["social_momentum"],
                    raw_evidence={"reddit_mentions": reddit_count},
                ))

        return seeds

    def _scan_smart_money(self, text: str, symbol: str) -> list[Seed]:
        seeds: list[Seed] = []
        if not text:
            return seeds

        # Net insider buying/selling
        net_buying = _extract_number(text, "net insider buying (90d): $")
        if net_buying is not None:
            if net_buying < -1_000_000:
                seeds.append(Seed(
                    category="insider_selling",
                    summary=f"Heavy insider selling: ${net_buying:,.0f} in 90 days",
                    score=0.75,
                    source_domain="smart_money",
                    suggested_tools=CATEGORY_TOOLS["insider_selling"],
                    raw_evidence={"net_insider_buying_90d": net_buying},
                ))
            elif net_buying > 500_000:
                seeds.append(Seed(
                    category="insider_buying",
                    summary=f"Significant insider buying: ${net_buying:,.0f} in 90 days",
                    score=0.7,
                    source_domain="smart_money",
                    suggested_tools=CATEGORY_TOOLS["insider_buying"],
                    raw_evidence={"net_insider_buying_90d": net_buying},
                ))

        # Congressional trades
        if "congressional trades" in text.lower():
            seeds.append(Seed(
                category="congressional_trade",
                summary="Congressional trading activity detected",
                score=0.6,
                source_domain="smart_money",
                suggested_tools=CATEGORY_TOOLS["congressional_trade"],
            ))

        return seeds

    def _scan_risk(self, text: str, symbol: str) -> list[Seed]:
        seeds: list[Seed] = []
        if not text:
            return seeds

        # Earnings proximity
        days_until = _extract_number(text, "days until:")
        if days_until is not None and days_until <= 7:
            seeds.append(Seed(
                category="earnings_imminent",
                summary=f"Earnings in {int(days_until)} days — high volatility expected",
                score=0.85,
                source_domain="risk",
                suggested_tools=CATEGORY_TOOLS["earnings_imminent"],
                raw_evidence={"days_until_earnings": days_until},
            ))

        # Altman Z-score (bankruptcy risk)
        altman = _extract_number(text, "altman_z:")
        if altman is not None and altman < 1.8:
            seeds.append(Seed(
                category="debt_concern",
                summary=f"Altman Z={altman:.2f} — distress zone (<1.8)",
                score=0.9,
                source_domain="risk",
                suggested_tools=CATEGORY_TOOLS["debt_concern"],
                raw_evidence={"altman_z": altman},
            ))

        # Max drawdown
        drawdown = _extract_number(text, "max_drawdown:")
        if drawdown is not None and drawdown < -0.3:
            seeds.append(Seed(
                category="momentum_extreme",
                summary=f"Max drawdown {drawdown:.1%} — significant downside",
                score=0.6,
                source_domain="risk",
                suggested_tools=CATEGORY_TOOLS["momentum_extreme"],
                raw_evidence={"max_drawdown": drawdown},
            ))

        return seeds
