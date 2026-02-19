"""Layer 3 — RAG Answer Engine.

For each question from Layer 2, searches the relevant DuckDB text data
using BM25 keyword ranking, then extracts a concise answer via a short
LLM call.

No vector DB required — BM25 is excellent for financial text where terms
like "earnings", "revenue", "guidance" are highly distinctive.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from app.database import get_db
from app.models.dossier import QAPair
from app.services.llm_service import LLMService
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for search."""
    if not text or len(text) < 10:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + lowercase tokenizer."""
    return re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()


# ---------------------------------------------------------------------------
# Source routing — maps target_source to DuckDB queries
# ---------------------------------------------------------------------------

def _fetch_news_texts(ticker: str) -> list[str]:
    """Fetch news article titles + summaries."""
    db = get_db()
    rows = db.execute(
        "SELECT title, summary FROM news_articles "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 50",
        [ticker],
    ).fetchall()
    return [
        f"{r[0] or ''}: {r[1] or ''}" for r in rows if r[0] or r[1]
    ]


def _fetch_transcript_texts(ticker: str) -> list[str]:
    """Fetch YouTube transcript text.

    First searches for transcripts mentioning this ticker.
    Falls back to recent transcripts if no ticker-specific matches.
    """
    db = get_db()
    # Primary: ticker-specific transcripts
    rows = db.execute(
        "SELECT title, raw_transcript FROM youtube_transcripts "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 10",
        [ticker],
    ).fetchall()
    if rows:
        return [
            f"[{r[0] or 'Untitled'}] {r[1] or ''}" for r in rows if r[1]
        ]

    # Fallback: search ALL transcripts for ticker mentions in the text
    rows = db.execute(
        "SELECT title, raw_transcript FROM youtube_transcripts "
        "WHERE raw_transcript ILIKE ? ORDER BY collected_at DESC LIMIT 5",
        [f"%{ticker}%"],
    ).fetchall()
    if rows:
        return [
            f"[{r[0] or 'Untitled'}] {r[1] or ''}" for r in rows if r[1]
        ]

    # Last resort: get latest transcripts (may discuss the sector)
    rows = db.execute(
        "SELECT title, raw_transcript FROM youtube_transcripts "
        "ORDER BY collected_at DESC LIMIT 3",
    ).fetchall()
    return [
        f"[{r[0] or 'Untitled'}] {r[1] or ''}" for r in rows if r[1]
    ]


def _fetch_fundamental_texts(ticker: str) -> list[str]:
    """Serialize financial data as searchable text."""
    db = get_db()
    texts: list[str] = []

    # Fundamentals snapshot
    row = db.execute(
        "SELECT raw_json FROM fundamentals "
        "WHERE ticker = ? ORDER BY snapshot_date DESC LIMIT 1",
        [ticker],
    ).fetchone()
    if row and row[0]:
        texts.append(f"Fundamentals: {row[0][:3000]}")

    # Financial history
    rows = db.execute(
        "SELECT year, revenue, net_income, gross_margin, operating_margin, "
        "net_margin, eps FROM financial_history "
        "WHERE ticker = ? ORDER BY year DESC LIMIT 5",
        [ticker],
    ).fetchall()
    for r in rows:
        texts.append(
            f"Year {r[0]}: Revenue={r[1]}, NetIncome={r[2]}, "
            f"GrossMargin={r[3]}, OpMargin={r[4]}, NetMargin={r[5]}, EPS={r[6]}"
        )

    # Balance sheet
    rows = db.execute(
        "SELECT year, total_assets, total_liabilities, stockholders_equity, "
        "current_ratio, total_debt, cash_and_equivalents "
        "FROM balance_sheet WHERE ticker = ? ORDER BY year DESC LIMIT 5",
        [ticker],
    ).fetchall()
    for r in rows:
        texts.append(
            f"Balance {r[0]}: Assets={r[1]}, Liab={r[2]}, Equity={r[3]}, "
            f"CurrentRatio={r[4]}, Debt={r[5]}, Cash={r[6]}"
        )

    # Cash flows
    rows = db.execute(
        "SELECT year, operating_cashflow, free_cashflow, "
        "financing_cashflow, dividends_paid "
        "FROM cash_flows WHERE ticker = ? ORDER BY year DESC LIMIT 5",
        [ticker],
    ).fetchall()
    for r in rows:
        texts.append(
            f"CashFlow {r[0]}: OpCF={r[1]}, FCF={r[2]}, "
            f"FinCF={r[3]}, Dividends={r[4]}"
        )

    return texts


def _fetch_technical_texts(ticker: str) -> list[str]:
    """Fetch recent technicals as text lines."""
    db = get_db()
    rows = db.execute(
        "SELECT date, rsi, macd, macd_signal, sma_20, sma_50, sma_200, "
        "bb_upper, bb_lower, atr, adx, obv "
        "FROM technicals WHERE ticker = ? ORDER BY date DESC LIMIT 10",
        [ticker],
    ).fetchall()
    return [
        f"{r[0]}: RSI={r[1]}, MACD={r[2]}, MACDSig={r[3]}, SMA20={r[4]}, "
        f"SMA50={r[5]}, SMA200={r[6]}, BBU={r[7]}, BBL={r[8]}, ATR={r[9]}, "
        f"ADX={r[10]}, OBV={r[11]}"
        for r in rows
    ]


def _fetch_insider_texts(ticker: str) -> list[str]:
    """Fetch insider activity data."""
    db = get_db()
    rows = db.execute(
        "SELECT snapshot_date, net_insider_buying_90d, "
        "institutional_ownership_pct, raw_transactions "
        "FROM insider_activity WHERE ticker = ? "
        "ORDER BY snapshot_date DESC LIMIT 5",
        [ticker],
    ).fetchall()
    texts: list[str] = []
    for r in rows:
        texts.append(
            f"{r[0]}: NetInsiderBuying90d={r[1]}, "
            f"InstitutionalOwnership={r[2]}%"
        )
        if r[3]:
            texts.append(f"Transactions: {r[3][:2000]}")
    return texts


# Map target_source → fetch function
_SOURCE_FETCHERS = {
    "news": _fetch_news_texts,
    "transcripts": _fetch_transcript_texts,
    "fundamentals": _fetch_fundamental_texts,
    "technicals": _fetch_technical_texts,
    "insider": _fetch_insider_texts,
}


ANSWER_SYSTEM_PROMPT = """\
You are a financial research assistant.  Given the context excerpts below,
answer the question in 2-3 concise sentences.  If the data is insufficient,
say so clearly.  Be specific with numbers and dates when available.
"""


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class RAGEngine:
    """Search Phase-1 text data and extract answers via LLM."""

    def __init__(self) -> None:
        self._llm = LLMService()

    async def answer_question(
        self,
        question: str,
        target_source: str,
        ticker: str,
    ) -> QAPair:
        """Search DuckDB data for the relevant source, then extract answer."""
        fetcher = _SOURCE_FETCHERS.get(target_source, _fetch_news_texts)
        raw_texts = fetcher(ticker)

        # Cross-source fallback: if primary source empty, try others
        if not raw_texts:
            logger.info(
                "[RAG] No %s data for %s — trying cross-source fallback",
                target_source,
                ticker,
            )
            for alt_source, alt_fetcher in _SOURCE_FETCHERS.items():
                if alt_source == target_source:
                    continue
                raw_texts = alt_fetcher(ticker)
                if raw_texts:
                    logger.info(
                        "[RAG] Found %d texts from %s (fallback for %s)",
                        len(raw_texts), alt_source, target_source,
                    )
                    break

        if not raw_texts:
            logger.info(
                "[RAG] No data at all for %s — returning empty",
                ticker,
            )
            return QAPair(
                question=question,
                answer="No data available for this source.",
                source=target_source,  # type: ignore[arg-type]
                confidence="low",
            )

        # Chunk all texts
        all_chunks: list[str] = []
        for text in raw_texts:
            all_chunks.extend(chunk_text(text))

        if not all_chunks:
            return QAPair(
                question=question,
                answer="Data was too short to analyze.",
                source=target_source,  # type: ignore[arg-type]
                confidence="low",
            )

        # BM25 ranking
        tokenized_corpus = [_tokenize(c) for c in all_chunks]
        query_tokens = _tokenize(question)

        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(query_tokens)
        top_idx = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:3]
        top_chunks = [all_chunks[i] for i in top_idx]

        # LLM answer extraction
        context = "\n---\n".join(top_chunks)
        user_msg = f"QUESTION: {question}\n\nCONTEXT:\n{context}"

        try:
            raw_answer = await self._llm.chat(
                system=ANSWER_SYSTEM_PROMPT,
                user=user_msg,
                response_format="text",
                max_tokens=512,
            )
            answer = raw_answer.strip()
            confidence = "high" if len(top_chunks) >= 2 else "medium"
        except Exception as exc:
            logger.warning("[RAG] LLM extraction failed: %s", exc)
            answer = "LLM extraction failed — raw data was found but could not be summarized."
            confidence = "low"

        logger.info(
            "[RAG] %s/%s → %d chunks searched, confidence=%s",
            ticker,
            target_source,
            len(all_chunks),
            confidence,
        )

        return QAPair(
            question=question,
            answer=answer,
            source=target_source,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
        )

    async def answer_all(
        self,
        questions: list[dict],
        ticker: str,
    ) -> list[QAPair]:
        """Answer all questions sequentially (each uses its own source)."""
        results: list[QAPair] = []
        for q in questions:
            pair = await self.answer_question(
                question=q["question"],
                target_source=q.get("target_source", "news"),
                ticker=ticker,
            )
            results.append(pair)
        return results
