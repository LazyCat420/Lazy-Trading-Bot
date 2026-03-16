"""Ticker Scanner — LLM-powered ticker extraction from YouTube transcripts.

Reads stored transcripts from DuckDB, sends them to the LLM, and asks
it to identify stock tickers mentioned.  This replaces the old regex
approach which produced massive false-positive rates on general market
transcripts (every common English word matched as a 'ticker').
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from app.config import settings
from app.database import get_db
from app.models.discovery import ScoredTicker
from app.services.llm_service import LLMService
from app.services.ticker_validator import TickerValidator
from app.utils.logger import logger

# Maximum chars of transcript to send to the LLM per video.
# Keeps prompt size reasonable while capturing the key content.
_MAX_TRANSCRIPT_CHARS = 16000

_EXTRACTION_PROMPT = """You are a financial data extraction engine. Your job is to extract EVERY piece of investment-relevant information from this YouTube transcript. Be thorough — do NOT skip information.

TASK 1 — IDENTIFY STOCKS:
Find ALL stocks discussed. Look for:
- Explicit ticker symbols (e.g. "AAPL", "CRS", "NVDA")
- Company names → resolve to NYSE/NASDAQ tickers (e.g. "Intel" → "INTC", "Carpenter Technology" → "CRS")
- Companies mentioned as partners, competitors, investors, or in analyst coverage

RULES FOR TICKERS:
- ONLY real US stock tickers (NYSE/NASDAQ)
- NO ETFs (SPY, QQQ), crypto, forex, indices (DJI, SPX), or commodities
- NO common English words that look like tickers

TASK 2 — EXTRACT ALL TRADING DATA:
Scan the transcript carefully for EVERY piece of the following data. Do NOT return empty fields if the information exists in the transcript.

PRICE LEVELS — scan for ANY dollar amounts, price points, or ranges:
- Current price, recent highs/lows, support/resistance levels
- Historical prices mentioned, 52-week high/low
- Example: "$19 low on Aug 4th", "currently at $37", "$29 gap level"

VALUATION — scan for market cap, P/E, multiples, revenue figures:
- Market cap, PE ratio, price-to-sales, EV/EBITDA
- Revenue, earnings, EPS figures (current or projected)
- Example: "market cap $177B", "14x multiple", "revenue $12-14B"

ANALYST OPINIONS — ratings, upgrades/downgrades, price targets:
- Which firm/analyst said what rating
- Price targets from analysts or the video creator

TECHNICAL ANALYSIS — chart patterns, indicators, key levels:
- Moving averages, MACD, RSI, volume analysis
- Support/resistance levels, gap levels, breakouts
- Chart patterns (head and shoulders, cup and handle, etc.)

CATALYSTS — events that could move the stock:
- Government actions, partnerships, M&A, new products
- Insider buying, institutional investors, major contracts
- Dividends, share buybacks, management changes
- Example: "US govt invested via CHIPS Act", "AMD partnering for chip manufacturing"

RISKS — anything negative or cautious:
- Revenue declining, losing market share, overvaluation concerns
- Macro risks, sector risks, competition threats

SENTIMENT — the overall tone of the video creator:
- Are they bullish, bearish, or cautious?
- What's their recommendation? Buy, sell, hold, wait?

VIDEO TITLE: {title}
CHANNEL: {channel}

TRANSCRIPT (truncated):
{transcript}

Return a JSON object with this structure. FILL IN EVERY FIELD — do not leave arrays empty if data exists:
{{
  "tickers": ["INTC", "AMD", "NVDA"],
  "trading_data": {{
    "sentiment": "bullish" or "bearish" or "neutral" or "mixed",
    "price_levels": ["Current: $37", "Recent low: $19 (Aug 4)", "Key gap level: $29", "52-week high: $X"],
    "valuation": "Market cap $177B, ~14x revenue multiple, revenue $12-14B range, no growth yet",
    "analyst_ratings": ["Creator: cautious buy on dips", "BTIG: Buy"],
    "price_targets": ["Near-term resistance: $40", "Upside potential: $50"],
    "earnings": "Revenue $12-14B, flat/declining. No earnings improvement yet. EPS $X.",
    "technicals": "Gapped above $29 support. Price rose from $19 to $37 in 5 weeks. Illiquid zone above $29.",
    "catalysts": ["US govt invested 10% via CHIPS Act (up 50%)", "AMD partnering for chip manufacturing", "NVDA investing"],
    "risks": ["Revenue still declining", "No fundamental improvement yet", "Could be overvalued at current levels"],
    "key_facts": ["Stock up ~95% from Aug 4 lows ($19 to $37)", "US govt investment already up 50%", "Market cap $177B"],
    "summary": "Intel surged from $19 to $37 on CHIPS Act investment and AMD/NVDA partnerships, but fundamentals unchanged with flat $12-14B revenue. Mixed outlook — strong momentum but valuation stretched."
  }}
}}

If genuinely no trading data in the transcript, set trading_data to null."""


class TickerScanner:
    """Extracts ticker mentions from YouTube transcripts using LLM."""

    # Curated channels get a score multiplier
    TRUSTED_CHANNELS: set[str] = {
        "CNBC Television",
        "Bloomberg Television",
        "Yahoo Finance",
        "Investor's Business Daily",
        "Benzinga",
    }

    def __init__(self) -> None:
        self.validator = TickerValidator()
        self.llm = LLMService()

    async def scan_recent_transcripts(
        self,
        hours: int = 24,
    ) -> list[ScoredTicker]:
        """Scan un-scanned transcripts for ticker mentions using LLM.

        For each transcript:
          1. Send to LLM → get list of tickers
          2. Validate via yfinance
          3. Score by channel trust + mention count

        Marks transcripts as scanned so they are never re-processed.
        """
        start = time.time()
        logger.info("=" * 60)
        logger.info("[YouTube Scanner] LLM-powered scan of un-scanned transcripts")
        logger.info("=" * 60)

        db = get_db()

        rows = db.execute(
            """
            SELECT ticker, video_id, title, channel, raw_transcript
            FROM youtube_transcripts
            WHERE scanned_for_tickers = FALSE
            ORDER BY collected_at DESC
            LIMIT 50
            """,
        ).fetchall()

        if not rows:
            logger.info("[YouTube Scanner] No un-scanned transcripts found")
            return []

        logger.info("[YouTube Scanner] Found %d un-scanned transcripts", len(rows))

        # Collect video_ids for marking as scanned later
        scanned_video_ids: list[tuple[str, str]] = []

        # Aggregate ticker data across all transcripts
        ticker_counts: dict[str, int] = {}
        ticker_contexts: dict[str, list[str]] = {}
        ticker_channels: dict[str, set[str]] = {}

        for ticker_col, video_id, title, channel, transcript in rows:
            scanned_video_ids.append((ticker_col, video_id))

            if not transcript:
                continue

            logger.info(
                "[YouTube Scanner] Asking LLM: '%s' by %s",
                (title or "untitled")[:50],
                channel or "unknown",
            )

            # Already-known ticker from this video's pipeline run
            # (skip __MARKET__ placeholder tickers)
            if ticker_col and ticker_col != "__MARKET__":
                known = ticker_col.upper().strip()
                ticker_counts[known] = ticker_counts.get(known, 0) + 5
                ticker_contexts.setdefault(known, []).append(f"[pipeline] {title[:80]}")
                ticker_channels.setdefault(known, set()).add(
                    channel or "unknown",
                )

            # ── LLM extraction ──
            trust_mult = 1.5 if channel in self.TRUSTED_CHANNELS else 1.0
            extracted, trading_data = await self._llm_extract_tickers(
                title or "",
                channel or "",
                transcript,
            )

            for t in extracted:
                score = int(3 * trust_mult)
                ticker_counts[t] = ticker_counts.get(t, 0) + score
                context = f"[LLM:{channel or 'unknown'}] {title[:80]}"
                ticker_contexts.setdefault(t, []).append(context)
                ticker_channels.setdefault(t, set()).add(
                    channel or "unknown",
                )

            # Store trading data summary if the LLM extracted any
            if trading_data and extracted:
                try:
                    summary_text = json.dumps(trading_data, default=str)[:2000]
                    for t in extracted:
                        db.execute(
                            """
                            INSERT INTO youtube_trading_data
                                (ticker, video_id, title, channel, trading_data, collected_at)
                            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT (ticker, video_id) DO UPDATE
                            SET trading_data = excluded.trading_data,
                                collected_at = CURRENT_TIMESTAMP
                            """,
                            [t, video_id, title[:200], channel[:100], summary_text],
                        )
                except Exception:
                    pass  # Table may not exist yet — non-critical

        # ── Mark all processed transcripts as scanned ──
        if scanned_video_ids:
            for t_col, vid in scanned_video_ids:
                db.execute(
                    "UPDATE youtube_transcripts "
                    "SET scanned_for_tickers = TRUE "
                    "WHERE ticker = ? AND video_id = ?",
                    [t_col, vid],
                )
            logger.info(
                "[YouTube Scanner] Marked %d transcripts as scanned",
                len(scanned_video_ids),
            )

        # Validate candidates via yfinance
        logger.info(
            "[YouTube Scanner] Validating %d LLM-extracted tickers...",
            len(ticker_counts),
        )
        valid_tickers = self.validator.validate_batch(
            list(ticker_counts.keys()),
        )

        # Build scored results
        now = datetime.now()
        results: list[ScoredTicker] = []
        for ticker in valid_tickers:
            channels = ticker_channels.get(ticker, set())
            results.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=float(ticker_counts.get(ticker, 0)),
                    source="youtube",
                    source_detail=", ".join(channels),
                    sentiment_hint="neutral",
                    context_snippets=ticker_contexts.get(ticker, [])[:3],
                    first_seen=now,
                    last_seen=now,
                )
            )

        results.sort(key=lambda x: x.discovery_score, reverse=True)

        elapsed = time.time() - start
        logger.info(
            "[YouTube Scanner] Complete: %d valid tickers in %.1fs",
            len(results),
            elapsed,
        )
        for r in results[:10]:
            logger.info(
                "[YouTube Scanner]   $%s: %.0f pts — %s",
                r.ticker,
                r.discovery_score,
                r.context_snippets[0] if r.context_snippets else "no context",
            )
        return results

    # ── LLM extraction ──────────────────────────────────────────────

    async def _llm_extract_tickers(
        self,
        title: str,
        channel: str,
        transcript: str,
        *,
        bot_id: str = "default",
    ) -> tuple[list[str], dict | None]:
        """Ask the LLM to list stock tickers and extract trading data.

        Uses the per-model AgenticExtractor pipeline. Falls back to the
        legacy hardcoded prompt if agentic extraction fails.

        Returns (tickers, trading_data) tuple.
        """
        # ── Try agentic extraction first ──────────────────────────
        try:
            from app.services.AgenticExtractor import AgenticExtractor

            extractor = AgenticExtractor(bot_id=bot_id)
            result = await extractor.extract_from_transcript(
                transcript=transcript[:_MAX_TRANSCRIPT_CHARS],
                title=title,
                channel=channel,
            )

            tickers = result.get("tickers", [])
            trading_data = result.get("trading_data")

            # Clean and validate tickers
            if isinstance(tickers, list):
                from app.services.ticker_validator import TickerValidator as _TV
                tickers = [
                    _TV.sanitize_ticker(t)
                    for t in tickers
                    if isinstance(t, str)
                    and 1 <= len(t.strip().lstrip("$#")) <= 5
                ]

            steps = result.get("extraction_meta", {}).get("steps_completed", 0)
            logger.info(
                "[YouTube Scanner] Agentic extracted %d tickers from '%s' "
                "(%d steps)%s",
                len(tickers),
                title[:40],
                steps,
                " (+ trading data)" if trading_data else "",
            )
            return tickers, trading_data

        except Exception as e:
            logger.warning(
                "[YouTube Scanner] Agentic extraction failed for '%s': %s — "
                "falling back to legacy prompt",
                title[:40], e,
            )

        # ── Legacy fallback: hardcoded prompt ─────────────────────
        truncated = transcript[:_MAX_TRANSCRIPT_CHARS]
        prompt = _EXTRACTION_PROMPT.format(
            title=title,
            channel=channel,
            transcript=truncated,
        )

        try:
            raw = await self.llm.chat(
                system=(
                    "You are a financial data extraction engine. "
                    "You extract stock tickers from company names AND "
                    "pull ALL investment-relevant data points. "
                    "Return ONLY raw, valid JSON."
                ),
                user=prompt,
                response_format="json",
                temperature=settings.LLM_DISCOVERY_TEMPERATURE,
                audit_step="youtube_ticker_scan_legacy",
                audit_ticker=title[:60] if title else "unknown",
            )
            cleaned = LLMService.clean_json_response(raw)
            parsed = json.loads(cleaned)

            tickers = []
            trading_data = None

            if isinstance(parsed, dict):
                raw_tickers = (
                    parsed.get("tickers")
                    or parsed.get("symbols")
                    or parsed.get("ticker_symbols")
                    or []
                )
                trading_data = parsed.get("trading_data")

                if isinstance(raw_tickers, list):
                    tickers = [
                        t.upper().strip()
                        for t in raw_tickers
                        if isinstance(t, str)
                        and 1 <= len(t.strip()) <= 5
                        and t.strip().isalpha()
                    ]
            elif isinstance(parsed, list):
                tickers = [
                    t.upper().strip()
                    for t in parsed
                    if isinstance(t, str)
                    and 1 <= len(t.strip()) <= 5
                    and t.strip().isalpha()
                ]

            logger.info(
                "[YouTube Scanner] Legacy extracted %d tickers from '%s': %s%s",
                len(tickers),
                title[:40],
                tickers[:10],
                " (+ trading data)" if trading_data else "",
            )
            return tickers, trading_data

        except Exception as e:
            logger.warning(
                "[YouTube Scanner] LLM extraction failed for '%s': %s",
                title[:40],
                e,
            )
            return [], None

