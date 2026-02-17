"""Ticker Scanner — extracts ticker mentions from YouTube transcripts in DuckDB.

Reads stored transcripts, chunks them, uses regex + validation to find
stock tickers mentioned in financial YouTube videos.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from app.collectors.ticker_validator import TickerValidator
from app.database import get_db
from app.models.discovery import ScoredTicker
from app.utils.logger import logger


class TickerScanner:
    """Extracts ticker mentions from YouTube transcripts stored in DuckDB."""

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

    def scan_recent_transcripts(self, hours: int = 24) -> list[ScoredTicker]:
        """Scan transcripts from the last N hours for ticker mentions.

        Returns scored ticker list sorted by score descending.
        """
        start = time.time()
        logger.info("=" * 60)
        logger.info("[YouTube Scanner] Scanning transcripts from last %dh", hours)
        logger.info("=" * 60)

        db = get_db()

        # Query recent transcripts
        rows = db.execute(
            """
            SELECT ticker, video_id, title, channel, raw_transcript
            FROM youtube_transcripts
            WHERE collected_at >= CURRENT_TIMESTAMP - INTERVAL ? HOUR
            ORDER BY collected_at DESC
            LIMIT 10
            """,
            [hours],
        ).fetchall()

        if not rows:
            logger.info("[YouTube Scanner] No transcripts found in last %dh", hours)
            return []

        logger.info("[YouTube Scanner] Found %d transcripts to scan", len(rows))

        # Track ticker mentions across all transcripts
        ticker_counts: dict[str, int] = {}
        ticker_contexts: dict[str, list[str]] = {}
        ticker_channels: dict[str, set[str]] = {}

        for ticker_col, video_id, title, channel, transcript in rows:
            if not transcript:
                continue

            logger.info(
                "[YouTube Scanner] Scanning: '%s' by %s",
                (title or "untitled")[:50], channel or "unknown",
            )

            # Already-known ticker from this video's pipeline run
            if ticker_col:
                known = ticker_col.upper().strip()
                ticker_counts[known] = ticker_counts.get(known, 0) + 5
                ticker_contexts.setdefault(known, []).append(
                    f"[pipeline] {title[:80]}"
                )
                ticker_channels.setdefault(known, set()).add(channel or "unknown")

            # Score multiplier for trusted channels
            trust_mult = 1.5 if channel in self.TRUSTED_CHANNELS else 1.0

            # Extract tickers from title (high weight)
            title_tickers = self._extract_tickers(title or "")
            for t in title_tickers:
                score = int(3 * trust_mult)
                ticker_counts[t] = ticker_counts.get(t, 0) + score
                ticker_contexts.setdefault(t, []).append(f"[title] {title[:80]}")
                ticker_channels.setdefault(t, set()).add(channel or "unknown")

            # Extract tickers from transcript (lower weight, but many mentions)
            transcript_tickers = self._extract_tickers(transcript)
            for t in set(transcript_tickers):
                # Count occurrences in transcript for weighting
                count = transcript_tickers.count(t)
                score = int(min(count, 5) * trust_mult)  # Cap at 5 mentions
                ticker_counts[t] = ticker_counts.get(t, 0) + score
                # Get a snippet around the first mention
                snippet = self._get_context_snippet(transcript, t)
                if snippet:
                    ticker_contexts.setdefault(t, []).append(
                        f"[transcript] {snippet}"
                    )
                ticker_channels.setdefault(t, set()).add(channel or "unknown")

        # Validate all candidates
        logger.info(
            "[YouTube Scanner] Validating %d candidate tickers...",
            len(ticker_counts),
        )
        valid_tickers = self.validator.validate_batch(list(ticker_counts.keys()))

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

    # ── Helpers ──────────────────────────────────────────────────────

    def _extract_tickers(self, text: str) -> list[str]:
        """Extract potential tickers: $SYMBOL or UPPERCASE 2-5 chars."""
        if not text:
            return []

        # Match $TICKER or standalone UPPERCASE words (2-5 chars)
        raw = re.findall(r"(?:\$|\b)([A-Z]{2,5})\b", text.upper())
        return [
            t for t in raw
            if t.isalpha() and t not in TickerValidator.EXCLUSION_LIST
        ]

    def _get_context_snippet(self, text: str, ticker: str, window: int = 60) -> str:
        """Get a short snippet of text around the first mention of a ticker."""
        idx = text.upper().find(ticker)
        if idx == -1:
            return ""
        start = max(0, idx - window)
        end = min(len(text), idx + len(ticker) + window)
        snippet = text[start:end].replace("\n", " ").strip()
        return f"...{snippet}..."
