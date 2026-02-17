"""Sentiment analysis agent — analyses full transcripts and multi-source news."""

from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.models.agent_reports import SentimentReport


class SentimentAgent(BaseAgent):
    """Analyses sentiment from news articles and YouTube transcripts.

    Now receives FULL YouTube transcripts (no truncation) and news from
    multiple sources (yfinance, Google News, SEC EDGAR) with source tracking.
    """

    prompt_file = "sentiment_analysis.md"
    output_model = SentimentReport

    def format_context(self, ticker: str, context: dict) -> str:
        """Format news and transcripts for the LLM.

        context keys:
            news:        list[NewsArticle]
            transcripts: list[YouTubeTranscript]
        """
        parts = []

        # ---- News Articles (grouped by source) ----
        news = context.get("news", [])
        if news:
            # Group by source
            yf_news = [a for a in news if a.source == "yfinance"]
            google_news = [a for a in news if a.source == "google_news"]
            sec_news = [a for a in news if a.source == "sec_edgar"]
            other_news = [a for a in news if a.source not in ("yfinance", "google_news", "sec_edgar")]

            parts.append(f"=== NEWS ARTICLES ({len(news)} total) ===\n")

            if yf_news:
                parts.append("--- Financial Wire News (yfinance) ---")
                for a in yf_news[:15]:
                    self._format_article(parts, a)

            if google_news:
                parts.append("\n--- General News (Google News) ---")
                for a in google_news[:15]:
                    self._format_article(parts, a)

            if sec_news:
                parts.append("\n--- SEC Filings ---")
                for a in sec_news[:5]:
                    self._format_article(parts, a)

            if other_news:
                parts.append("\n--- Other Sources ---")
                for a in other_news[:10]:
                    self._format_article(parts, a)

        # ---- YouTube Transcripts (FULL — no truncation) ----
        transcripts = context.get("transcripts", [])
        if transcripts:
            parts.append(f"\n=== YOUTUBE TRANSCRIPTS ({len(transcripts)} videos) ===\n")

            for i, t in enumerate(transcripts[:5]):  # Max 5 transcripts
                parts.append(f"--- Video {i + 1}: {t.title} ---")
                parts.append(f"Channel: {t.channel}")
                if t.published_at:
                    parts.append(f"Published: {t.published_at.strftime('%Y-%m-%d %H:%M')}")
                if t.duration_seconds:
                    mins = t.duration_seconds // 60
                    parts.append(f"Duration: {mins} minutes")

                # FULL transcript — no truncation
                transcript_text = t.raw_transcript.strip()
                if transcript_text:
                    parts.append(f"\n[FULL TRANSCRIPT]\n{transcript_text}")
                else:
                    parts.append("\n[No transcript available]")

                parts.append("")  # blank line separator

        if not news and not transcripts:
            parts.append("No news or transcript data available for analysis.")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_article(parts: list[str], article) -> None:
        """Format a single news article entry."""
        date_str = ""
        if article.published_at:
            date_str = f" ({article.published_at.strftime('%Y-%m-%d')})"

        parts.append(f"  • [{article.publisher}]{date_str} {article.title}")
        if article.summary:
            parts.append(f"    Summary: {article.summary[:300]}")
