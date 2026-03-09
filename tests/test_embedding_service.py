"""Tests for EmbeddingService — RAG Part 1 foundation."""

from __future__ import annotations

import pytest

from app.services.embedding_service import EmbeddingService


# ── chunk_text tests ─────────────────────────────────────────────


class TestChunkText:
    """Pure function tests — no async, no mocking needed."""

    def test_empty_text_returns_empty(self):
        assert EmbeddingService.chunk_text("") == []

    def test_none_returns_empty(self):
        assert EmbeddingService.chunk_text(None) == []  # type: ignore[arg-type]

    def test_short_text_single_chunk(self):
        text = "This is a short sentence about AAPL stock price movements today."
        chunks = EmbeddingService.chunk_text(text, chunk_size=2048)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_multiple_chunks(self):
        # Build text > 2048 chars with paragraph breaks
        paragraphs = [f"Paragraph {i}. " * 20 for i in range(10)]
        text = "\n\n".join(paragraphs)
        assert len(text) > 2048

        chunks = EmbeddingService.chunk_text(text, chunk_size=2048, overlap=200)
        assert len(chunks) >= 2
        # Each chunk should be at most chunk_size + overlap
        for chunk in chunks:
            assert len(chunk) <= 2048 + 200 + 50  # Small margin for paragraph breaks

    def test_overlap_creates_continuity(self):
        """Chunks should share some text at boundaries."""
        text = "A" * 1000 + "\n\n" + "B" * 1000 + "\n\n" + "C" * 1000
        chunks = EmbeddingService.chunk_text(text, chunk_size=1500, overlap=200)
        assert len(chunks) >= 2
        # Second chunk should contain overlap from first
        if len(chunks) > 1:
            # The overlap means there's shared content
            end_of_first = chunks[0][-200:]
            assert any(
                end_of_first[i:i + 50] in chunks[1]
                for i in range(0, len(end_of_first) - 50, 10)
            ) or len(chunks[0]) < 200  # Short chunks won't overlap

    def test_min_length_filter(self):
        """Chunks shorter than min_len are discarded."""
        text = "Hi"
        chunks = EmbeddingService.chunk_text(text, min_len=50)
        assert len(chunks) == 0

    def test_single_long_paragraph_no_breaks(self):
        """A single paragraph longer than chunk_size gets split by chars."""
        text = "A" * 5000  # No paragraph breaks
        chunks = EmbeddingService.chunk_text(text, chunk_size=2048, overlap=200)
        assert len(chunks) >= 2
        # Verify all text is covered
        total_unique = sum(len(c) for c in chunks)
        # With overlap, total chars > original, but all original chars are covered
        assert total_unique >= len(text)

    def test_realistic_transcript_chunk(self):
        """Simulates a YouTube transcript with natural paragraph breaks."""
        transcript = (
            "Welcome to today's market update. "
            "The S&P 500 is up 1.2% today driven by tech stocks.\n\n"
            "Apple reported strong earnings last night. Revenue was up 8% "
            "year over year, beating analyst estimates by $2 billion. "
            "The services segment continued to grow at 20% annually.\n\n"
            "In other news, the Federal Reserve signaled that rate cuts "
            "may come sooner than expected. Bond yields dropped 10 basis "
            "points on the announcement.\n\n"
            "Looking at Tesla, the stock is down 3% after delivery "
            "numbers came in below expectations. Analysts are mixed "
            "on whether this is a temporary blip or a trend.\n\n"
            "For our trading portfolio, we're watching NVDA closely. "
            "The AI chip demand continues to outpace supply."
        )
        chunks = EmbeddingService.chunk_text(transcript, chunk_size=300, overlap=50)
        assert len(chunks) >= 2
        assert all(len(c) >= 50 for c in chunks)


# ── embed_batch tests (mocked Ollama) ──────────────────────────


class TestEmbedBatch:
    """Async tests with mocked Ollama /api/embed endpoint."""

    @pytest.mark.asyncio
    async def test_embed_batch_returns_vectors(self, respx_mock):
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(
            json={"embeddings": [[0.1] * 768, [0.2] * 768]},
        )

        svc = EmbeddingService(model="nomic-embed-text:latest")
        # Override base_url for test
        svc.base_url = "http://localhost:11434"

        results = await svc.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert len(results[0]) == 768
        assert len(results[1]) == 768

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self):
        svc = EmbeddingService()
        results = await svc.embed_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_batch_handles_error(self, respx_mock):
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(status_code=500)

        svc = EmbeddingService()
        svc.base_url = "http://localhost:11434"

        results = await svc.embed_batch(["test"])
        assert len(results) == 1
        assert results[0] == []  # Empty vector on failure

    @pytest.mark.asyncio
    async def test_embed_text_single(self, respx_mock):
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(
            json={"embeddings": [[0.5] * 768]},
        )

        svc = EmbeddingService()
        svc.base_url = "http://localhost:11434"

        vec = await svc.embed_text("test sentence")
        assert len(vec) == 768
        assert vec[0] == 0.5


# ── get_embedding_stats tests ──────────────────────────────────


class TestEmbeddingStats:

    def test_stats_empty_table(self):
        """Stats from empty table returns zeroes."""
        stats = EmbeddingService.get_embedding_stats()
        assert stats["total_chunks"] == 0
        assert stats["total_sources"] == 0
