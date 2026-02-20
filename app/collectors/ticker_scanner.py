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

from app.collectors.ticker_validator import TickerValidator
from app.database import get_db
from app.models.discovery import ScoredTicker
from app.services.llm_service import LLMService
from app.utils.logger import logger

# Maximum chars of transcript to send to the LLM per video.
# Keeps prompt size reasonable while capturing the key content.
_MAX_TRANSCRIPT_CHARS = 6000

_EXTRACTION_PROMPT = """You are a stock ticker extraction tool.

Given the following YouTube video transcript, identify ALL stock tickers
(e.g. AAPL, TSLA, NVDA) that are discussed as investment opportunities,
analysis targets, or trading ideas.

RULES:
- Return ONLY real US stock ticker symbols (NYSE/NASDAQ).
- Do NOT include ETFs, crypto, forex, indices, or commodities unless
  they trade as a stock ticker.
- Do NOT include common English words that happen to look like tickers.
- If no real tickers are mentioned, return an empty list.

VIDEO TITLE: {title}
CHANNEL: {channel}

TRANSCRIPT (truncated):
{transcript}

Return ONLY a JSON list of uppercase ticker strings, e.g.: ["AAPL", "TSLA"]
If none found, return: []"""


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
        self, hours: int = 24,
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
                (title or "untitled")[:50], channel or "unknown",
            )

            # Already-known ticker from this video's pipeline run
            # (skip __MARKET__ placeholder tickers)
            if ticker_col and ticker_col != "__MARKET__":
                known = ticker_col.upper().strip()
                ticker_counts[known] = ticker_counts.get(known, 0) + 5
                ticker_contexts.setdefault(known, []).append(
                    f"[pipeline] {title[:80]}"
                )
                ticker_channels.setdefault(known, set()).add(
                    channel or "unknown",
                )

            # ── LLM extraction ──
            trust_mult = 1.5 if channel in self.TRUSTED_CHANNELS else 1.0
            extracted = await self._llm_extract_tickers(
                title or "", channel or "", transcript,
            )

            for t in extracted:
                score = int(3 * trust_mult)
                ticker_counts[t] = ticker_counts.get(t, 0) + score
                context = f"[LLM:{channel or 'unknown'}] {title[:80]}"
                ticker_contexts.setdefault(t, []).append(context)
                ticker_channels.setdefault(t, set()).add(
                    channel or "unknown",
                )

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
            len(results), elapsed,
        )
        for r in results[:10]:
            logger.info(
                "[YouTube Scanner]   $%s: %.0f pts — %s",
                r.ticker, r.discovery_score,
                r.context_snippets[0] if r.context_snippets else "no context",
            )
        return results

    # ── LLM extraction ──────────────────────────────────────────────

    async def _llm_extract_tickers(
        self, title: str, channel: str, transcript: str,
    ) -> list[str]:
        """Ask the LLM to list stock tickers from a transcript."""
        # Truncate transcript to keep prompt reasonable
        truncated = transcript[:_MAX_TRANSCRIPT_CHARS]

        prompt = _EXTRACTION_PROMPT.format(
            title=title,
            channel=channel,
            transcript=truncated,
        )

        try:
            raw = await self.llm.chat(
                system=(
                    "You are a stock ticker extraction tool. "
                    "Return ONLY valid JSON."
                ),
                user=prompt,
                response_format="json",
            )
            cleaned = LLMService.clean_json_response(raw)
            tickers = json.loads(cleaned)

            if isinstance(tickers, list):
                # Filter to valid-looking ticker symbols
                result = [
                    t.upper().strip()
                    for t in tickers
                    if isinstance(t, str)
                    and 1 <= len(t.strip()) <= 5
                    and t.strip().isalpha()
                ]
                logger.info(
                    "[YouTube Scanner] LLM extracted %d tickers from '%s': %s",
                    len(result), title[:40], result[:10],
                )
                return result

            logger.warning(
                "[YouTube Scanner] LLM returned non-list: %s", type(tickers),
            )
            return []

        except Exception as e:
            logger.warning(
                "[YouTube Scanner] LLM extraction failed for '%s': %s",
                title[:40], e,
            )
            return []
