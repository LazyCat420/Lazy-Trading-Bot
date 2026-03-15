"""Embedding Service — embeds text chunks via Ollama and stores in DuckDB.

Provides the foundation for RAG (Retrieval-Augmented Generation):
  • chunk_text()      → splits long text into overlapping chunks
  • embed_text()      → embeds a single string via Ollama /api/embed
  • embed_batch()     → batch-embeds multiple strings
  • embed_and_store() → chunk → embed → store in DuckDB embeddings table
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.utils.logger import logger


class EmbeddingService:
    """Embeds text chunks via Ollama and stores them in DuckDB."""

    DEFAULT_MODEL = "nomic-embed-text:latest"
    CHUNK_SIZE = 2048       # chars per chunk (~512 tokens)
    CHUNK_OVERLAP = 200     # char overlap between chunks
    MAX_BATCH_SIZE = 32     # max texts per /api/embed call
    MIN_CHUNK_LEN = 50      # discard chunks shorter than this

    def __init__(self, model: str | None = None) -> None:
        self.base_url = settings.OLLAMA_URL.rstrip("/")
        self.model = model or getattr(
            settings, "RAG_EMBEDDING_MODEL", self.DEFAULT_MODEL,
        )

    # ── Core: embed text via Ollama ────────────────────────────

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string. Returns vector."""
        result = await self.embed_batch([text])
        return result[0] if result else []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts via Ollama /api/embed.

        Splits into sub-batches of MAX_BATCH_SIZE to avoid overloading.
        Returns list of vectors, one per input text.
        """
        if not texts:
            return []

        import asyncio
        import time as _time

        all_vectors: list[list[float]] = []
        total_batches = (len(texts) + self.MAX_BATCH_SIZE - 1) // self.MAX_BATCH_SIZE

        for batch_idx, i in enumerate(range(0, len(texts), self.MAX_BATCH_SIZE)):
            batch = texts[i : i + self.MAX_BATCH_SIZE]
            if total_batches > 1:
                logger.info(
                    "[Embedding] Batch %d/%d (%d texts) — sending to Ollama…",
                    batch_idx + 1, total_batches, len(batch),
                )

            # Heartbeat for long embedding batches
            async def _hb(batch_num: int) -> None:
                elapsed = 0
                while True:
                    await asyncio.sleep(30)
                    elapsed += 30
                    logger.info(
                        "[Embedding] ⏳ Still embedding batch %d/%d (%ds elapsed)…",
                        batch_num, total_batches, elapsed,
                    )

            hb_task = asyncio.create_task(_hb(batch_idx + 1))
            t0 = _time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model, "input": batch},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    embeddings = data.get("embeddings", [])
                    all_vectors.extend(embeddings)
                    elapsed = _time.perf_counter() - t0
                    if total_batches > 1:
                        logger.info(
                            "[Embedding] ✅ Batch %d/%d done (%.1fs, %d vectors)",
                            batch_idx + 1, total_batches, elapsed, len(embeddings),
                        )
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[Embedding] Ollama /api/embed failed (HTTP %d): %s",
                    exc.response.status_code,
                    exc.response.text[:300],
                )
                # Pad with empty vectors so indices stay aligned
                all_vectors.extend([] for _ in batch)
            except Exception as exc:
                logger.error("[Embedding] Ollama embed request failed: %s", exc)
                all_vectors.extend([] for _ in batch)
            finally:
                hb_task.cancel()

        return all_vectors

    # ── Chunking ───────────────────────────────────────────────

    @staticmethod
    def chunk_text(
        text: str,
        chunk_size: int = 2048,
        overlap: int = 200,
        min_len: int = 50,
    ) -> list[str]:
        """Split long text into overlapping chunks.

        Strategy:
        1. Split on double-newline (paragraph breaks) first.
        2. Accumulate paragraphs until chunk_size is reached.
        3. Emit chunk, then start next chunk with `overlap` chars
           from the end of the previous chunk for continuity.

        Args:
            text: Input text.
            chunk_size: Max chars per chunk.
            overlap: Char overlap between consecutive chunks.
            min_len: Discard chunks shorter than this.

        Returns:
            List of chunk strings.
        """
        if not text or len(text.strip()) < min_len:
            return [text.strip()] if text and len(text.strip()) >= min_len else []

        # If text fits in one chunk, return as-is
        text = text.strip()
        if len(text) <= chunk_size:
            return [text]

        # Split on paragraphs first
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # If adding this paragraph would exceed chunk_size
            if current and len(current) + len(para) + 2 > chunk_size:
                if len(current) >= min_len:
                    chunks.append(current.strip())
                # Start new chunk with overlap from end of previous
                if overlap > 0 and current:
                    current = current[-overlap:] + "\n\n" + para
                else:
                    current = para
            else:
                current = current + "\n\n" + para if current else para

        # Emit final chunk
        if current.strip() and len(current.strip()) >= min_len:
            chunks.append(current.strip())

        # If we only got one chunk that's still too long (single paragraph,
        # no \n\n breaks), split by char boundary with overlap
        if len(chunks) == 1 and len(chunks[0]) > chunk_size:
            big = chunks[0]
            chunks = []
            pos = 0
            while pos < len(big):
                end = min(pos + chunk_size, len(big))
                piece = big[pos:end].strip()
                if len(piece) >= min_len:
                    chunks.append(piece)
                pos = end - overlap if end < len(big) else end
        elif not chunks and len(text) > chunk_size:
            pos = 0
            while pos < len(text):
                end = min(pos + chunk_size, len(text))
                piece = text[pos:end].strip()
                if len(piece) >= min_len:
                    chunks.append(piece)
                pos = end - overlap if end < len(text) else end

        return chunks

    # ── Store in DuckDB ────────────────────────────────────────

    async def embed_and_store(
        self,
        source_type: str,
        source_id: str,
        text: str,
        ticker: str | None = None,
        metadata: str = "",
    ) -> int:
        """Chunk text, embed all chunks, and store in DuckDB.

        Returns:
            Number of chunks successfully stored.
        """
        from app.database import get_db

        chunks = self.chunk_text(text)
        if not chunks:
            return 0

        vectors = await self.embed_batch(chunks)
        if not vectors:
            return 0

        db = get_db()
        stored = 0

        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            if not vec:  # Skip failed embeddings
                continue
            try:
                db.execute(
                    "INSERT INTO embeddings "
                    "(source_type, source_id, ticker, chunk_index, "
                    "chunk_text, embedding, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [source_type, source_id, ticker, i, chunk, vec, metadata],
                )
                stored += 1
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to store chunk %d of %s/%s: %s",
                    i, source_type, source_id, exc,
                )

        if stored:
            db.commit()

        return stored

    # ── Source-specific embedding jobs ───────────────────────────

    async def embed_youtube_transcripts(self) -> dict[str, Any]:
        """Embed YouTube transcripts not yet in the embeddings table.

        Queries youtube_transcripts for rows with raw_transcript content,
        skips any already embedded (by video_id), chunks, embeds, and stores.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        import asyncio

        from app.database import get_db

        db = get_db()

        # Find unembedded transcripts
        rows = db.execute("""
            SELECT DISTINCT yt.video_id, yt.title, yt.channel,
                            yt.raw_transcript, yt.ticker
            FROM youtube_transcripts yt
            LEFT JOIN (
                SELECT DISTINCT source_id
                FROM embeddings
                WHERE source_type = 'youtube'
            ) e ON yt.video_id = e.source_id
            WHERE yt.raw_transcript IS NOT NULL
              AND LENGTH(yt.raw_transcript) > 50
              AND e.source_id IS NULL
            ORDER BY yt.collected_at DESC
        """).fetchall()

        if not rows:
            logger.info("[Embedding] No new YouTube transcripts to embed")
            return {"embedded": 0, "skipped": 0, "total_chunks": 0}

        # Deduplicate by video_id (same video may appear under multiple tickers)
        seen_videos: dict[str, dict] = {}
        ticker_map: dict[str, list[str]] = {}  # video_id → list of tickers
        for row in rows:
            vid_id = row[0]
            if vid_id not in seen_videos:
                seen_videos[vid_id] = {
                    "video_id": row[0],
                    "title": row[1] or "",
                    "channel": row[2] or "",
                    "raw_transcript": row[3],
                }
                ticker_map[vid_id] = []
            ticker_map[vid_id].append(row[4])

        total_embedded = 0
        total_chunks = 0
        total_vids = len(seen_videos)

        for idx, (vid_id, info) in enumerate(seen_videos.items(), 1):
            transcript = info["raw_transcript"]
            meta = f"{info['channel']} | {info['title']}"

            # Decide ticker: if only one ticker, use it; if multiple, NULL
            tickers = ticker_map[vid_id]
            ticker = tickers[0] if len(tickers) == 1 else None

            try:
                stored = await self.embed_and_store(
                    source_type="youtube",
                    source_id=vid_id,
                    text=transcript,
                    ticker=ticker,
                    metadata=meta[:200],
                )
                total_chunks += stored
                if stored > 0:
                    total_embedded += 1
                logger.info(
                    "[Embedding] YouTube %d/%d: %s → %d chunks",
                    idx, total_vids, vid_id[:12], stored,
                )
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to embed YouTube %s: %s",
                    vid_id, exc,
                )

            # Brief pause between videos to avoid overwhelming Ollama
            await asyncio.sleep(0.1)

        skipped = total_vids - total_embedded
        logger.info(
            "[Embedding] YouTube complete: %d embedded, %d skipped, %d chunks",
            total_embedded, skipped, total_chunks,
        )
        return {
            "embedded": total_embedded,
            "skipped": skipped,
            "total_chunks": total_chunks,
        }

    async def embed_reddit_posts(self) -> dict[str, Any]:
        """Embed Reddit post snippets from discovered_tickers table.

        Reddit posts are typically short (<500 chars), so most won't
        need chunking. Each post is ticker-specific.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        import asyncio

        from app.database import get_db

        db = get_db()

        # Use CAST(rowid AS VARCHAR) as a stable source_id
        # since discovered_tickers has no single unique column
        rows = db.execute("""
            SELECT dt.ticker, dt.source_detail, dt.context_snippet,
                   CAST(dt.rowid AS VARCHAR) as row_id
            FROM discovered_tickers dt
            LEFT JOIN (
                SELECT DISTINCT source_id
                FROM embeddings
                WHERE source_type = 'reddit'
            ) e ON CAST(dt.rowid AS VARCHAR) = e.source_id
            WHERE dt.source LIKE '%reddit%'
              AND LENGTH(dt.context_snippet) > 30
              AND e.source_id IS NULL
        """).fetchall()

        if not rows:
            logger.info("[Embedding] No new Reddit posts to embed")
            return {"embedded": 0, "skipped": 0, "total_chunks": 0}

        total_embedded = 0
        total_chunks = 0

        for row in rows:
            ticker, subreddit, snippet, row_id = row[0], row[1], row[2], row[3]
            meta = subreddit or "reddit"

            try:
                stored = await self.embed_and_store(
                    source_type="reddit",
                    source_id=str(row_id),
                    text=snippet,
                    ticker=ticker,
                    metadata=meta[:200],
                )
                total_chunks += stored
                if stored > 0:
                    total_embedded += 1
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to embed Reddit row %s: %s",
                    row_id, exc,
                )
            await asyncio.sleep(0.05)

        skipped = len(rows) - total_embedded
        logger.info(
            "[Embedding] Reddit complete: %d embedded, %d skipped, %d chunks",
            total_embedded, skipped, total_chunks,
        )
        return {
            "embedded": total_embedded,
            "skipped": skipped,
            "total_chunks": total_chunks,
        }

    async def embed_news_articles(self) -> dict[str, Any]:
        """Embed full news articles from news_full_articles table.

        Articles are longer and may need chunking. Multi-ticker articles
        are stored with ticker=NULL so all tickers can retrieve them.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        import asyncio

        from app.database import get_db

        db = get_db()

        rows = db.execute("""
            SELECT nfa.article_hash, nfa.title, nfa.publisher,
                   nfa.content, nfa.tickers_found
            FROM news_full_articles nfa
            LEFT JOIN (
                SELECT DISTINCT source_id
                FROM embeddings
                WHERE source_type = 'news'
            ) e ON nfa.article_hash = e.source_id
            WHERE LENGTH(nfa.content) > 50
              AND e.source_id IS NULL
        """).fetchall()

        if not rows:
            logger.info("[Embedding] No new news articles to embed")
            return {"embedded": 0, "skipped": 0, "total_chunks": 0}

        total_embedded = 0
        total_chunks = 0

        for row in rows:
            article_hash = row[0]
            title = row[1] or ""
            publisher = row[2] or ""
            content = row[3]
            tickers_found = row[4] or ""

            meta = f"{publisher} | {title}"[:200]

            # Multi-ticker articles → store as general market (ticker=NULL)
            # Single-ticker → store with that ticker
            tickers = [
                t.strip() for t in tickers_found.split(",") if t.strip()
            ]
            ticker = tickers[0] if len(tickers) == 1 else None

            try:
                stored = await self.embed_and_store(
                    source_type="news",
                    source_id=article_hash,
                    text=content,
                    ticker=ticker,
                    metadata=meta,
                )
                total_chunks += stored
                if stored > 0:
                    total_embedded += 1
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to embed news %s: %s",
                    article_hash[:12], exc,
                )
            await asyncio.sleep(0.05)

        skipped = len(rows) - total_embedded
        logger.info(
            "[Embedding] News complete: %d embedded, %d skipped, %d chunks",
            total_embedded, skipped, total_chunks,
        )
        return {
            "embedded": total_embedded,
            "skipped": skipped,
            "total_chunks": total_chunks,
        }

    async def embed_trade_decisions(self, days: int = 30) -> dict[str, Any]:
        """Embed recent trade decisions for decision memory.

        Builds descriptive text from action, confidence, rationale,
        and risk notes. Only embeds decisions not already in embeddings.

        Args:
            days: How far back to look for decisions (default 30).

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        import asyncio
        from datetime import datetime, timedelta

        from app.database import get_db

        db = get_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        rows = db.execute("""
            SELECT td.id, td.symbol, td.action, td.confidence,
                   td.rationale, td.risk_level, td.risk_notes,
                   td.time_horizon, td.status, td.ts
            FROM trade_decisions td
            LEFT JOIN (
                SELECT DISTINCT source_id
                FROM embeddings
                WHERE source_type = 'decision'
            ) e ON td.id = e.source_id
            WHERE td.ts >= ?::TIMESTAMP
              AND td.rationale IS NOT NULL
              AND LENGTH(td.rationale) > 20
              AND e.source_id IS NULL
            ORDER BY td.ts DESC
        """, [cutoff]).fetchall()

        if not rows:
            logger.info("[Embedding] No new trade decisions to embed")
            return {"embedded": 0, "skipped": 0, "total_chunks": 0}

        total_embedded = 0
        total_chunks = 0

        for row in rows:
            decision_id = row[0]
            symbol = row[1]
            action = row[2] or "UNKNOWN"
            confidence = row[3] or 0
            rationale = row[4] or ""
            risk_level = row[5] or "MED"
            risk_notes = row[6] or ""
            time_horizon = row[7] or "SWING"
            status = row[8] or "pending"
            ts = row[9]

            # Build descriptive text for embedding
            text_parts = [
                f"TRADE DECISION for {symbol}: {action}",
                f"Confidence: {confidence:.0%}" if confidence else "",
                f"Time Horizon: {time_horizon}",
                f"Risk: {risk_level}",
                f"Rationale: {rationale}",
            ]
            if risk_notes:
                text_parts.append(f"Risk Notes: {risk_notes}")
            if status == "rejected":
                text_parts.append("Status: REJECTED")

            text = "\n".join(p for p in text_parts if p)
            meta = f"{action} | {status} | {str(ts)[:10]}"

            try:
                stored = await self.embed_and_store(
                    source_type="decision",
                    source_id=decision_id,
                    text=text,
                    ticker=symbol,
                    metadata=meta[:200],
                )
                total_chunks += stored
                if stored > 0:
                    total_embedded += 1
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to embed decision %s: %s",
                    decision_id[:12], exc,
                )
            await asyncio.sleep(0.05)

        skipped = len(rows) - total_embedded
        logger.info(
            "[Embedding] Decisions complete: %d embedded, %d skipped, "
            "%d chunks",
            total_embedded, skipped, total_chunks,
        )
        return {
            "embedded": total_embedded,
            "skipped": skipped,
            "total_chunks": total_chunks,
        }

    async def embed_all_sources(self) -> dict[str, Any]:
        """Orchestrate embedding for all data sources.

        Runs YouTube, Reddit, News, and Decisions in sequence.

        Returns:
            Combined stats dict with per-source breakdowns.
        """
        import time as _time

        t0 = _time.time()

        logger.info("[Embedding] ▶ Phase 1/4: YouTube transcripts…")
        yt = await self.embed_youtube_transcripts()
        yt_elapsed = round(_time.time() - t0, 1)
        logger.info(
            "[Embedding] ✅ YouTube done: %d embedded, %d chunks (%.1fs)",
            yt.get("embedded", 0), yt.get("total_chunks", 0), yt_elapsed,
        )

        logger.info("[Embedding] ▶ Phase 2/4: Reddit posts…")
        t_reddit = _time.time()
        reddit = await self.embed_reddit_posts()
        reddit_elapsed = round(_time.time() - t_reddit, 1)
        logger.info(
            "[Embedding] ✅ Reddit done: %d embedded, %d chunks (%.1fs)",
            reddit.get("embedded", 0), reddit.get("total_chunks", 0), reddit_elapsed,
        )

        logger.info("[Embedding] ▶ Phase 3/4: News articles…")
        t_news = _time.time()
        news = await self.embed_news_articles()
        news_elapsed = round(_time.time() - t_news, 1)
        logger.info(
            "[Embedding] ✅ News done: %d embedded, %d chunks (%.1fs)",
            news.get("embedded", 0), news.get("total_chunks", 0), news_elapsed,
        )

        logger.info("[Embedding] ▶ Phase 4/4: Trade decisions…")
        t_dec = _time.time()
        decisions = await self.embed_trade_decisions()
        dec_elapsed = round(_time.time() - t_dec, 1)
        logger.info(
            "[Embedding] ✅ Decisions done: %d embedded, %d chunks (%.1fs)",
            decisions.get("embedded", 0), decisions.get("total_chunks", 0), dec_elapsed,
        )

        total_chunks = (
            yt.get("total_chunks", 0)
            + reddit.get("total_chunks", 0)
            + news.get("total_chunks", 0)
            + decisions.get("total_chunks", 0)
        )
        total_embedded = (
            yt.get("embedded", 0)
            + reddit.get("embedded", 0)
            + news.get("embedded", 0)
            + decisions.get("embedded", 0)
        )
        elapsed = round(_time.time() - t0, 1)

        logger.info(
            "[Embedding] All sources complete: %d sources → %d chunks (%.1fs)",
            total_embedded, total_chunks, elapsed,
        )
        return {
            "youtube": yt,
            "reddit": reddit,
            "news": news,
            "decisions": decisions,
            "total_chunks": total_chunks,
            "total_embedded": total_embedded,
            "elapsed_s": elapsed,
        }

    # ── Model management ───────────────────────────────────────

    async def ensure_model_loaded(self) -> bool:
        """Auto-pull the embedding model if it's not available in Ollama.

        Returns True if model is ready, False on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Check if model exists via /api/show
                resp = await client.post(
                    f"{self.base_url}/api/show",
                    json={"name": self.model},
                )
                if resp.status_code == 200:
                    logger.info(
                        "[Embedding] Model %s already available", self.model,
                    )
                    return True
        except Exception:
            pass

        # Model not found — pull it
        logger.info("[Embedding] Pulling model %s...", self.model)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/pull",
                    json={"name": self.model, "stream": False},
                )
                resp.raise_for_status()
                logger.info(
                    "[Embedding] ✅ Model %s pulled successfully", self.model,
                )
                return True
        except Exception as exc:
            logger.error(
                "[Embedding] ❌ Failed to pull model %s: %s",
                self.model, exc,
            )
            return False

    async def precompute_query_vectors(
        self,
        tickers: list[str],
    ) -> dict[str, list[float]]:
        """Pre-compute search query vectors for a list of tickers.

        Uses the same query-building logic as RetrievalService so the
        vectors are identical to what would be generated at retrieval time.

        This should be called during the embedding phase while the
        embedding model is loaded, BEFORE the LLM model takes over VRAM.

        Args:
            tickers: List of stock symbols.

        Returns:
            Dict mapping ticker → embedding vector. Missing/failed
            tickers are omitted.
        """
        if not tickers:
            return {}

        # Import here to avoid circular dependency
        from app.services.retrieval_service import RetrievalService

        # Build search queries using the same logic as retrieval
        queries = {
            t: RetrievalService._build_search_query(t)
            for t in tickers
        }

        # Batch embed all queries at once
        query_list = list(queries.values())
        ticker_list = list(queries.keys())

        vectors = await self.embed_batch(query_list)

        cache: dict[str, list[float]] = {}
        for ticker, vec in zip(ticker_list, vectors):
            if vec:  # Skip failed embeddings
                cache[ticker] = vec

        logger.info(
            "[Embedding] Pre-computed query vectors for %d/%d tickers",
            len(cache), len(tickers),
        )
        return cache

    # ── Stats / diagnostics ────────────────────────────────────

    @staticmethod
    def get_embedding_stats() -> dict[str, Any]:
        """Return counts of embeddings by source type."""
        from app.database import get_db

        db = get_db()
        try:
            rows = db.execute(
                "SELECT source_type, COUNT(*) as cnt, "
                "COUNT(DISTINCT source_id) as sources "
                "FROM embeddings GROUP BY source_type"
            ).fetchall()
            stats: dict[str, Any] = {
                "total_chunks": 0,
                "total_sources": 0,
                "by_type": {},
            }
            for row in rows:
                src_type, cnt, sources = row[0], row[1], row[2]
                stats["by_type"][src_type] = {
                    "chunks": cnt,
                    "sources": sources,
                }
                stats["total_chunks"] += cnt
                stats["total_sources"] += sources
            return stats
        except Exception:
            return {"total_chunks": 0, "total_sources": 0, "by_type": {}}
