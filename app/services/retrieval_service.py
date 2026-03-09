"""Retrieval Service — query embeddings and return relevant context for trading.

The 'R' in RAG: takes a ticker, embeds a search query, finds the most
relevant chunks via cosine similarity, and formats them for the LLM prompt.
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.embedding_service import EmbeddingService
from app.utils.logger import logger


class RetrievalService:
    """Retrieve relevant context from embedded data for trading decisions."""

    # Common ticker → company name for richer search queries
    _COMPANY_NAMES: dict[str, str] = {
        "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Google Alphabet",
        "GOOG": "Google Alphabet", "AMZN": "Amazon", "TSLA": "Tesla",
        "NVDA": "Nvidia", "META": "Meta Facebook", "NFLX": "Netflix",
        "AMD": "AMD", "INTC": "Intel", "CRM": "Salesforce",
        "ORCL": "Oracle", "ADBE": "Adobe", "PYPL": "PayPal",
        "SQ": "Block Square", "SHOP": "Shopify", "COIN": "Coinbase",
        "PLTR": "Palantir", "SNOW": "Snowflake", "UBER": "Uber",
        "ABNB": "Airbnb", "RIVN": "Rivian", "LCID": "Lucid Motors",
        "DIS": "Disney", "BA": "Boeing", "JPM": "JPMorgan",
        "V": "Visa", "MA": "Mastercard", "WMT": "Walmart",
        "HD": "Home Depot", "KO": "Coca-Cola", "PEP": "PepsiCo",
        "JNJ": "Johnson Johnson", "PFE": "Pfizer", "UNH": "UnitedHealth",
        "XOM": "Exxon Mobil", "CVX": "Chevron", "LLY": "Eli Lilly",
        "AVGO": "Broadcom", "MU": "Micron", "QCOM": "Qualcomm",
        "ARM": "ARM Holdings", "SMCI": "Super Micro Computer",
    }

    def __init__(self) -> None:
        self.embedder = EmbeddingService()

    async def retrieve(
        self,
        ticker: str,
        query: str | None = None,
        top_k: int = 5,
        min_score: float = 0.3,
        source_types: list[str] | None = None,
        query_vector: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve top-K relevant chunks for a ticker.

        Args:
            ticker: Stock symbol to search for.
            query: Optional custom search query. If None, auto-generated.
            top_k: Max chunks to return.
            min_score: Minimum cosine similarity threshold.
            source_types: Filter by source type(s). None = all.
            query_vector: Optional pre-computed query embedding vector.
                If provided, skips the live embed_text() call.

        Returns:
            List of dicts with text, score, source_type, source_id,
            metadata, and ticker.
        """
        from app.database import get_db

        # Use pre-computed vector or embed live
        if query_vector:
            query_vec = query_vector
            logger.info("[Retrieval] Using cached query vector for %s", ticker)
        else:
            search_query = query or self._build_search_query(ticker)
            query_vec = await self.embedder.embed_text(search_query)
            if not query_vec:
                logger.warning("[Retrieval] Failed to embed query for %s", ticker)
                return []
            logger.info("[Retrieval] Live-embedded query for %s", ticker)

        db = get_db()

        # Build SQL with optional source_type filter
        source_filter = ""
        params: list[Any] = [query_vec, ticker]

        if source_types:
            placeholders = ", ".join("?" for _ in source_types)
            source_filter = f"AND source_type IN ({placeholders})"
            params.extend(source_types)

        # DuckDB cosine similarity — use list_cosine_similarity
        sql = f"""
            SELECT
                chunk_text,
                source_type,
                source_id,
                ticker,
                metadata,
                list_cosine_similarity(embedding, ?::FLOAT[]) as score
            FROM embeddings
            WHERE (ticker = ? OR ticker IS NULL)
              {source_filter}
            ORDER BY score DESC
            LIMIT ?
        """
        # Fetch more than top_k to allow dedup, then trim
        fetch_limit = top_k * 3
        params.append(fetch_limit)

        try:
            rows = db.execute(sql, params).fetchall()
        except Exception as exc:
            logger.error("[Retrieval] DuckDB query failed: %s", exc)
            return []

        # Build result dicts, apply min_score filter
        results: list[dict[str, Any]] = []
        for row in rows:
            score = row[5]
            if score is None or score < min_score:
                continue
            results.append({
                "text": row[0],
                "source_type": row[1],
                "source_id": row[2],
                "ticker": row[3],
                "metadata": row[4] or "",
                "score": round(float(score), 4),
            })

        # Boost decision scores by 10% so the bot's own past
        # experience is prioritized in retrieval results
        for chunk in results:
            if chunk["source_type"] == "decision":
                chunk["score"] = min(
                    round(chunk["score"] * 1.1, 4), 1.0,
                )

        # Deduplicate: keep only best chunk per (source_type, source_id)
        deduped = self._deduplicate(results)

        return deduped[:top_k]

    async def retrieve_for_trading(
        self,
        ticker: str,
        top_k: int | None = None,
        max_chars: int | None = None,
        query_vector: list[float] | None = None,
    ) -> str:
        """Retrieve and format chunks for the LLM trading prompt.

        Convenience method that retrieves, formats with source
        attribution, and caps total length.

        Args:
            ticker: Stock symbol.
            top_k: Override settings.RAG_TOP_K.
            max_chars: Override settings.RAG_MAX_CHARS.
            query_vector: Optional pre-computed query embedding vector.

        Returns:
            Formatted text block ready for LLM prompt, or empty string.
        """
        k = top_k or getattr(settings, "RAG_TOP_K", 5)
        chars = max_chars or getattr(settings, "RAG_MAX_CHARS", 3000)

        chunks = await self.retrieve(ticker, top_k=k, query_vector=query_vector)
        if not chunks:
            logger.info("[Retrieval] No relevant chunks found for %s", ticker)
            return ""

        logger.info(
            "[Retrieval] Retrieved %d chunks for %s (top score=%.3f)",
            len(chunks), ticker, chunks[0]["score"],
        )
        return self._format_chunks(chunks, max_chars=chars)

    # ── Internals ──────────────────────────────────────────────

    @classmethod
    def _build_search_query(cls, ticker: str) -> str:
        """Build a natural-language search query from a ticker.

        Embedding models work better with descriptive text than bare
        ticker symbols. Appends company name if known.
        """
        company = cls._COMPANY_NAMES.get(ticker.upper(), "")
        parts = [ticker]
        if company:
            parts.append(company)
        parts.extend(["stock", "trading", "analysis", "outlook"])
        return " ".join(parts)

    @staticmethod
    def _deduplicate(
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep only the highest-scored chunk per (source_type, source_id).

        This prevents showing 3 overlapping chunks from the same
        YouTube transcript.
        """
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for chunk in chunks:
            key = (chunk["source_type"], chunk["source_id"])
            if key not in seen or chunk["score"] > seen[key]["score"]:
                seen[key] = chunk
        # Return sorted by score descending
        return sorted(seen.values(), key=lambda c: c["score"], reverse=True)

    @staticmethod
    def _format_chunks(
        chunks: list[dict[str, Any]],
        max_chars: int = 3000,
    ) -> str:
        """Format retrieved chunks into attributed text for the LLM.

        Output format:
            [YouTube: CNBC | Market Wrap] Apple reported record...

            [News: Reuters | AAPL Soars] Shares rose 3%...

            [Reddit: r/stocks] Everyone's sleeping on AAPL...
        """
        lines: list[str] = []
        total_len = 0

        for chunk in chunks:
            src = chunk["source_type"].capitalize()
            meta = chunk["metadata"]
            text = chunk["text"].strip()

            header = f"[{src}: {meta}]" if meta else f"[{src}]"
            entry = f"{header} {text}"

            # Check budget
            if total_len + len(entry) > max_chars:
                remaining = max_chars - total_len
                if remaining > 100:
                    # Truncate this chunk to fit
                    entry = entry[:remaining - 3] + "..."
                    lines.append(entry)
                break

            lines.append(entry)
            total_len += len(entry) + 2  # +2 for \n\n separator

        return "\n\n".join(lines)
