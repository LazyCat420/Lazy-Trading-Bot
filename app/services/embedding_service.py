"""Embedding Service — embeds text chunks via Ollama and stores in DuckDB.

Provides the foundation for RAG (Retrieval-Augmented Generation):
  • chunk_text()      → splits long text into overlapping chunks
  • embed_text()      → embeds a single string via Ollama /api/embed
  • embed_batch()     → batch-embeds multiple strings
  • embed_and_store() → chunk → embed → store in DuckDB embeddings table

Performance architecture (P0–P6 audit fixes):
  • All four source types run in parallel via asyncio.gather
  • Chunks from ALL documents are aggregated into cross-document batches
  • A single reusable httpx.AsyncClient eliminates per-call TCP overhead
  • DuckDB commits every COMMIT_EVERY documents for crash safety
  • News articles are capped at MAX_NEWS_CONTENT_LEN to prevent runaways
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import logger


@track_class_telemetry
class EmbeddingService:
    """Embeds text chunks via Ollama and stores them in DuckDB."""

    DEFAULT_MODEL = "nomic-embed-text:latest"
    CHUNK_SIZE = 2048       # chars per chunk (~512 tokens)
    CHUNK_OVERLAP = 200     # char overlap between chunks
    MAX_BATCH_SIZE = 32     # max texts per /api/embed call
    MIN_CHUNK_LEN = 30      # discard chunks shorter than this (matches SQL filters)

    # Max content length for news articles (chars).  14 000 chars ≈ 8 chunks
    # at CHUNK_SIZE=2048 / overlap=200.  Prevents runaway single-article times.
    MAX_NEWS_CONTENT_LEN = 14_000

    # Commit to DuckDB every N documents (crash-safety vs I/O trade-off)
    COMMIT_EVERY = 50

    def __init__(self, model: str | None = None) -> None:
        self.base_url = settings.OLLAMA_URL.rstrip("/")
        self.model = model or getattr(
            settings, "RAG_EMBEDDING_MODEL", self.DEFAULT_MODEL,
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable httpx client (created once, not per-call)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        """Close the reusable HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

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
                client = await self._get_client()
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
        min_len: int = 30,
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

        Kept for single-document callers (e.g., tests).  The main pipeline
        now uses ``_batch_embed_and_store`` instead.

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
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_type, source_id, chunk_index) DO NOTHING",
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

    # ── Cross-document batch collector (P0) ─────────────────────

    async def _batch_embed_and_store(
        self,
        source_type: str,
        items: list[tuple[str, str | None, str, str]],
    ) -> dict[str, int]:
        """Embed multiple documents in globally-batched Ollama calls.

        Instead of one HTTP call per document, all chunks from all
        documents are flattened into one list and sent in MAX_BATCH_SIZE
        sub-batches.

        Args:
            source_type: e.g. "youtube", "reddit", "news", "decision"
            items: List of (source_id, ticker, metadata, text) tuples.

        Returns:
            {"embedded": int, "total_chunks": int}
        """
        import time as _time

        from app.database import get_db

        if not items:
            return {"embedded": 0, "total_chunks": 0}

        t0 = _time.perf_counter()

        # ── Phase A: Collect & chunk all documents ──────────────
        all_chunks: list[str] = []       # flat chunk texts
        chunk_map: list[tuple[str, str | None, str, int]] = []
        doc_chunk_counts: dict[str, int] = {}

        for source_id, ticker, metadata, text in items:
            chunks = self.chunk_text(text)
            if not chunks:
                continue
            doc_chunk_counts[source_id] = len(chunks)
            for i, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                chunk_map.append((source_id, ticker, metadata, i))

        if not all_chunks:
            return {"embedded": 0, "total_chunks": 0}

        total_batches = (len(all_chunks) + self.MAX_BATCH_SIZE - 1) // self.MAX_BATCH_SIZE
        logger.info(
            "[Embedding] %s: collected %d chunks from %d documents — "
            "sending in %d batches…",
            source_type, len(all_chunks), len(doc_chunk_counts), total_batches,
        )

        # ── Phase B: Batch embed all chunks at once ─────────────
        all_vectors = await self.embed_batch(all_chunks)

        if not all_vectors or len(all_vectors) != len(all_chunks):
            logger.error(
                "[Embedding] %s: vector count mismatch (%d vectors for %d chunks)",
                source_type,
                len(all_vectors) if all_vectors else 0,
                len(all_chunks),
            )
            return {"embedded": 0, "total_chunks": 0}

        # ── Phase C: Batch store with hybrid commits ────────────
        db = get_db()
        stored = 0
        docs_since_commit = 0
        prev_source_id = None

        for chunk_text_val, vec, (source_id, ticker, metadata, chunk_idx) in zip(
            all_chunks, all_vectors, chunk_map,
        ):
            if not vec:
                continue
            try:
                db.execute(
                    "INSERT INTO embeddings "
                    "(source_type, source_id, ticker, chunk_index, "
                    "chunk_text, embedding, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_type, source_id, chunk_index) DO NOTHING",
                    [source_type, source_id, ticker, chunk_idx,
                     chunk_text_val, vec, metadata],
                )
                stored += 1
            except Exception as exc:
                logger.warning(
                    "[Embedding] Failed to store %s chunk %d of %s: %s",
                    source_type, chunk_idx, source_id[:16], exc,
                )

            # Hybrid commit: every COMMIT_EVERY documents (not chunks)
            if source_id != prev_source_id:
                docs_since_commit += 1
                prev_source_id = source_id
                if docs_since_commit >= self.COMMIT_EVERY:
                    db.commit()
                    docs_since_commit = 0

        # Final commit for remaining rows
        if docs_since_commit > 0:
            db.commit()

        elapsed = _time.perf_counter() - t0
        embedded_docs = len(doc_chunk_counts)

        logger.info(
            "[Embedding] %s complete: %d docs → %d chunks stored (%.1fs)",
            source_type, embedded_docs, stored, elapsed,
        )
        return {"embedded": embedded_docs, "total_chunks": stored}

    # ── Source-specific embedding jobs ───────────────────────────

    async def embed_youtube_transcripts(self) -> dict[str, Any]:
        """Embed YouTube transcripts not yet in the embeddings table.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        from app.database import get_db

        db = get_db()

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
        ticker_map: dict[str, list[str]] = {}
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

        # Build items for cross-document batch collector
        items: list[tuple[str, str | None, str, str]] = []
        for vid_id, info in seen_videos.items():
            tickers = ticker_map[vid_id]
            ticker = tickers[0] if len(tickers) == 1 else None
            meta = f"{info['channel']} | {info['title']}"[:200]
            items.append((vid_id, ticker, meta, info["raw_transcript"]))

        result = await self._batch_embed_and_store("youtube", items)

        skipped = len(seen_videos) - result.get("embedded", 0)
        return {
            "embedded": result.get("embedded", 0),
            "skipped": skipped,
            "total_chunks": result.get("total_chunks", 0),
        }

    async def embed_reddit_posts(self) -> dict[str, Any]:
        """Embed Reddit thread data from the reddit_threads table.

        Pulls full thread content (title + body + comments) for richer
        semantic embeddings.  Falls back to discovered_tickers snippets
        if no reddit_threads data is available.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        from app.database import get_db

        db = get_db()

        # ── Primary: embed from reddit_threads table (rich content) ──
        try:
            rows = db.execute("""
                SELECT rt.thread_id, rt.subreddit, rt.title,
                       rt.selftext, rt.comments_json, rt.tickers_found
                FROM reddit_threads rt
                LEFT JOIN (
                    SELECT DISTINCT source_id
                    FROM embeddings
                    WHERE source_type = 'reddit'
                ) e ON rt.thread_id = e.source_id
                WHERE e.source_id IS NULL
                  AND LENGTH(rt.title) > 10
            """).fetchall()
        except Exception:
            rows = []  # Table may not exist yet

        if rows:
            import json as _json

            items: list[tuple[str, str | None, str, str]] = []
            for row in rows:
                thread_id = row[0]
                subreddit = row[1] or "reddit"
                title = row[2] or ""
                selftext = row[3] or ""
                comments_json = row[4] or "[]"
                tickers_found = row[5] or ""

                # Build a rich text blob from the full thread
                text_parts = [f"Thread: {title}"]
                if selftext:
                    text_parts.append(f"Post: {selftext[:3000]}")

                try:
                    comments = _json.loads(comments_json)
                    if isinstance(comments, list):
                        for i, c in enumerate(comments[:10]):
                            text_parts.append(f"Comment {i+1}: {str(c)[:500]}")
                except (_json.JSONDecodeError, TypeError):
                    pass

                text = "\n\n".join(text_parts)
                meta = f"r/{subreddit} | {title[:100]}"[:200]

                # Pick single ticker if only one found
                tickers = [t.strip() for t in tickers_found.split(",") if t.strip()]
                ticker = tickers[0] if len(tickers) == 1 else None

                items.append((thread_id, ticker, meta, text))

            result = await self._batch_embed_and_store("reddit", items)

            skipped = len(rows) - result.get("embedded", 0)
            return {
                "embedded": result.get("embedded", 0),
                "skipped": skipped,
                "total_chunks": result.get("total_chunks", 0),
            }

        # ── Fallback: embed from discovered_tickers (legacy snippets) ──
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

        items = []
        for row in rows:
            ticker, subreddit, snippet, row_id = row[0], row[1], row[2], row[3]
            meta = (subreddit or "reddit")[:200]
            items.append((str(row_id), ticker, meta, snippet))

        result = await self._batch_embed_and_store("reddit", items)

        skipped = len(rows) - result.get("embedded", 0)
        return {
            "embedded": result.get("embedded", 0),
            "skipped": skipped,
            "total_chunks": result.get("total_chunks", 0),
        }

    async def embed_news_articles(self) -> dict[str, Any]:
        """Embed news articles from both news_full_articles and news_articles.

        Content is capped at MAX_NEWS_CONTENT_LEN chars to prevent
        runaway single-article embedding times.

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
        from app.database import get_db

        db = get_db()

        # UNION both tables; P1: cap with LEFT(content, ?)
        rows = db.execute("""
            SELECT article_hash, title, publisher, content, tickers_found
            FROM (
                SELECT nfa.article_hash, nfa.title, nfa.publisher,
                       LEFT(nfa.content, ?) AS content, nfa.tickers_found
                FROM news_full_articles nfa
                WHERE LENGTH(nfa.content) > 50

                UNION

                SELECT na.article_hash, na.title, na.publisher,
                       na.summary AS content, na.ticker AS tickers_found
                FROM news_articles na
                WHERE LENGTH(na.summary) > 50
            ) combined
            LEFT JOIN (
                SELECT DISTINCT source_id
                FROM embeddings
                WHERE source_type = 'news'
            ) e ON combined.article_hash = e.source_id
            WHERE e.source_id IS NULL
        """, [self.MAX_NEWS_CONTENT_LEN]).fetchall()

        if not rows:
            logger.info("[Embedding] No new news articles to embed")
            return {"embedded": 0, "skipped": 0, "total_chunks": 0}

        items: list[tuple[str, str | None, str, str]] = []
        for row in rows:
            article_hash = row[0]
            title = row[1] or ""
            publisher = row[2] or ""
            content = row[3]
            tickers_found = row[4] or ""

            meta = f"{publisher} | {title}"[:200]
            tickers = [t.strip() for t in tickers_found.split(",") if t.strip()]
            ticker = tickers[0] if len(tickers) == 1 else None

            items.append((article_hash, ticker, meta, content))

        result = await self._batch_embed_and_store("news", items)

        skipped = len(rows) - result.get("embedded", 0)
        return {
            "embedded": result.get("embedded", 0),
            "skipped": skipped,
            "total_chunks": result.get("total_chunks", 0),
        }

    async def embed_trade_decisions(self, days: int = 30) -> dict[str, Any]:
        """Embed recent trade decisions for decision memory.

        Args:
            days: How far back to look for decisions (default 30).

        Returns:
            {"embedded": int, "skipped": int, "total_chunks": int}
        """
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

        items: list[tuple[str, str | None, str, str]] = []
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

            items.append((decision_id, symbol, meta[:200], text))

        result = await self._batch_embed_and_store("decision", items)

        skipped = len(rows) - result.get("embedded", 0)
        return {
            "embedded": result.get("embedded", 0),
            "skipped": skipped,
            "total_chunks": result.get("total_chunks", 0),
        }

    async def embed_all_sources(self) -> dict[str, Any]:
        """Orchestrate embedding for all data sources.

        P2: Runs all four sources in **parallel** via asyncio.gather
        so DB-query time of one source overlaps with GPU-embed time
        of another.

        Returns:
            Combined stats dict with per-source breakdowns.
        """
        import asyncio
        import time as _time

        from app.services.event_logger import log_event

        t0 = _time.time()

        log_event(
            "embedding", "embedding_start",
            "Starting RAG embedding for all sources…",
        )

        # P2: Run all four sources concurrently
        logger.info("[Embedding] ▶ Starting all 4 sources in parallel…")
        yt_task = asyncio.create_task(self.embed_youtube_transcripts())
        reddit_task = asyncio.create_task(self.embed_reddit_posts())
        news_task = asyncio.create_task(self.embed_news_articles())
        decisions_task = asyncio.create_task(self.embed_trade_decisions())

        results = await asyncio.gather(
            yt_task, reddit_task, news_task, decisions_task,
            return_exceptions=True,
        )

        # Unpack results (replace exceptions with zero-dicts)
        source_names = ["youtube", "reddit", "news", "decisions"]
        source_results: dict[str, dict] = {}
        for name, res in zip(source_names, results):
            if isinstance(res, Exception):
                logger.warning("[Embedding] %s failed: %s", name, res)
                source_results[name] = {
                    "embedded": 0, "skipped": 0, "total_chunks": 0,
                }
            else:
                source_results[name] = res
            logger.info(
                "[Embedding] ✅ %s: %d embedded, %d chunks",
                name,
                source_results[name].get("embedded", 0),
                source_results[name].get("total_chunks", 0),
            )

        total_chunks = sum(
            r.get("total_chunks", 0) for r in source_results.values()
        )
        total_embedded = sum(
            r.get("embedded", 0) for r in source_results.values()
        )
        elapsed = round(_time.time() - t0, 1)

        logger.info(
            "[Embedding] All sources complete: %d sources → %d chunks (%.1fs)",
            total_embedded, total_chunks, elapsed,
        )

        log_event(
            "embedding", "embedding_complete",
            f"Embedded {total_embedded} sources → {total_chunks} chunks "
            f"in {elapsed}s",
            metadata={
                "total_embedded": total_embedded,
                "total_chunks": total_chunks,
                "elapsed_s": elapsed,
            },
        )

        # Clean up the reusable HTTP client
        await self.close()

        return {
            "youtube": source_results["youtube"],
            "reddit": source_results["reddit"],
            "news": source_results["news"],
            "decisions": source_results["decisions"],
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
            client = await self._get_client()
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

        # Model not found — pull it (use longer timeout)
        logger.info("[Embedding] Pulling model %s...", self.model)
        try:
            async with httpx.AsyncClient(timeout=300.0) as pull_client:
                resp = await pull_client.post(
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
