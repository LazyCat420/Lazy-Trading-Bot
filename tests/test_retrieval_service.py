"""Tests for RetrievalService — RAG Part 4."""

from __future__ import annotations

import pytest

from app.services.retrieval_service import RetrievalService


# ── _build_search_query tests (pure function) ────────────────


class TestBuildSearchQuery:
    """No async, no mocking needed."""

    def test_known_ticker(self):
        q = RetrievalService._build_search_query("AAPL")
        assert "AAPL" in q
        assert "Apple" in q
        assert "stock" in q

    def test_unknown_ticker(self):
        q = RetrievalService._build_search_query("ZZZZ")
        assert "ZZZZ" in q
        assert "stock" in q
        assert "analysis" in q

    def test_case_insensitive_lookup(self):
        q = RetrievalService._build_search_query("msft")
        # _COMPANY_NAMES uses uppercase keys, method calls .upper()
        assert "Microsoft" in q


# ── _format_chunks tests (pure function) ─────────────────────


class TestFormatChunks:

    def test_empty_list(self):
        assert RetrievalService._format_chunks([]) == ""

    def test_single_chunk_with_attribution(self):
        chunks = [{
            "text": "Apple beat earnings by 12%",
            "source_type": "youtube",
            "source_id": "v1",
            "ticker": "AAPL",
            "metadata": "CNBC | Market Wrap",
            "score": 0.85,
        }]
        result = RetrievalService._format_chunks(chunks, max_chars=5000)
        assert "[Youtube: CNBC | Market Wrap]" in result
        assert "Apple beat earnings" in result

    def test_max_chars_truncation(self):
        chunks = [
            {
                "text": "A" * 2000,
                "source_type": "news",
                "source_id": "n1",
                "ticker": "AAPL",
                "metadata": "Reuters",
                "score": 0.8,
            },
            {
                "text": "B" * 2000,
                "source_type": "reddit",
                "source_id": "r1",
                "ticker": "AAPL",
                "metadata": "r/stocks",
                "score": 0.7,
            },
        ]
        result = RetrievalService._format_chunks(chunks, max_chars=500)
        assert len(result) <= 500 + 50  # Small margin for header

    def test_no_metadata_header(self):
        chunks = [{
            "text": "Some text about stocks",
            "source_type": "reddit",
            "source_id": "r1",
            "ticker": "TSLA",
            "metadata": "",
            "score": 0.6,
        }]
        result = RetrievalService._format_chunks(chunks)
        assert "[Reddit]" in result


# ── _deduplicate tests ───────────────────────────────────────


class TestDeduplicate:

    def test_keeps_highest_score(self):
        chunks = [
            {"source_type": "youtube", "source_id": "v1", "score": 0.9,
             "text": "chunk1", "ticker": "AAPL", "metadata": ""},
            {"source_type": "youtube", "source_id": "v1", "score": 0.7,
             "text": "chunk2", "ticker": "AAPL", "metadata": ""},
        ]
        result = RetrievalService._deduplicate(chunks)
        assert len(result) == 1
        assert result[0]["score"] == 0.9

    def test_different_sources_kept(self):
        chunks = [
            {"source_type": "youtube", "source_id": "v1", "score": 0.9,
             "text": "yt", "ticker": "AAPL", "metadata": ""},
            {"source_type": "news", "source_id": "n1", "score": 0.8,
             "text": "news", "ticker": "AAPL", "metadata": ""},
        ]
        result = RetrievalService._deduplicate(chunks)
        assert len(result) == 2


# ── retrieve tests (mocked Ollama + DuckDB) ──────────────────


class TestRetrieve:

    @pytest.mark.asyncio
    async def test_retrieve_returns_scored_chunks(self, respx_mock):
        """Retrieve with known embeddings returns sorted results."""
        from app.database import get_db

        db = get_db()

        # Insert test embeddings (768-dim vectors)
        vec_aapl = [0.8] * 768
        vec_msft = [0.1] * 768
        vec_general = [0.7] * 768

        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["youtube", "v1", "AAPL", 0,
             "Apple earnings beat estimates", vec_aapl, "CNBC"],
        )
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["reddit", "r1", "MSFT", 0,
             "Microsoft Azure growing", vec_msft, "r/stocks"],
        )
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["news", "n1", None, 0,
             "Market outlook bullish today", vec_general, "Reuters"],
        )
        db.commit()

        # Mock query embedding to match AAPL vector
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [[0.8] * 768]})

        svc = RetrievalService()
        svc.embedder.base_url = "http://localhost:11434"
        results = await svc.retrieve("AAPL", top_k=5, min_score=0.0)

        # Should get AAPL and general market chunks, not MSFT
        assert len(results) >= 1
        # AAPL chunk should score highest (identical vector)
        assert results[0]["text"] == "Apple earnings beat estimates"

    @pytest.mark.asyncio
    async def test_retrieve_includes_general_market(self, respx_mock):
        """General market chunks (ticker=NULL) included in results."""
        from app.database import get_db

        db = get_db()
        vec = [0.5] * 768
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["youtube", "v2", None, 0,
             "Overall market conditions improving", vec, "Bloomberg"],
        )
        db.commit()

        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [[0.5] * 768]})

        svc = RetrievalService()
        svc.embedder.base_url = "http://localhost:11434"
        results = await svc.retrieve("AAPL", top_k=5, min_score=0.0)

        assert any(r["ticker"] is None for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_for_trading_formats_output(self, respx_mock):
        """retrieve_for_trading returns formatted text with attribution."""
        from app.database import get_db

        db = get_db()
        vec = [0.9] * 768
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["youtube", "v3", "NVDA", 0,
             "Nvidia AI chip demand continues to outpace supply",
             vec, "CNBC | Tech Report"],
        )
        db.commit()

        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [[0.9] * 768]})

        svc = RetrievalService()
        svc.embedder.base_url = "http://localhost:11434"
        text = await svc.retrieve_for_trading("NVDA")

        assert "[Youtube:" in text
        assert "Nvidia AI chip" in text

    @pytest.mark.asyncio
    async def test_retrieve_empty_embeddings(self, respx_mock):
        """Returns empty when query embedding fails."""
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(status_code=500)

        svc = RetrievalService()
        svc.embedder.base_url = "http://localhost:11434"
        results = await svc.retrieve("AAPL")
        assert results == []


# ── cached vector tests (no Ollama needed) ────────────────────


class TestCachedVectorRetrieval:
    """Tests that pre-computed query vectors bypass live embedding."""

    def test_retrieve_with_cached_vector_skips_embed(self):
        """Passing query_vector skips embed_text entirely."""
        import asyncio

        from app.database import get_db

        db = get_db()

        # Insert a test embedding
        vec = [0.8] * 768
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["youtube", "cached_test", "AAPL", 0,
             "Apple reports record quarter", vec, "CNBC"],
        )
        db.commit()

        svc = RetrievalService()
        # Point embedder to invalid URL — if it tries to call Ollama, it'll fail
        svc.embedder.base_url = "http://unreachable:99999"

        # Pass the same vector as query_vector — no Ollama call needed
        results = asyncio.run(svc.retrieve(
            "AAPL", top_k=5, min_score=0.0, query_vector=vec,
        ))

        # Should succeed using the cached vector
        assert len(results) >= 1
        assert results[0]["text"] == "Apple reports record quarter"
        assert results[0]["score"] > 0.99  # Identical vectors → ~1.0

    def test_retrieve_for_trading_with_cached_vector(self):
        """retrieve_for_trading works with pre-computed vector."""
        import asyncio

        from app.database import get_db

        db = get_db()

        vec = [0.7] * 768
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["news", "cached_trading", "NVDA", 0,
             "Nvidia GPU demand surges", vec, "Reuters | Tech"],
        )
        db.commit()

        svc = RetrievalService()
        svc.embedder.base_url = "http://unreachable:99999"

        text = asyncio.run(svc.retrieve_for_trading(
            "NVDA", query_vector=vec,
        ))

        assert "[News: Reuters | Tech]" in text
        assert "Nvidia GPU demand" in text

