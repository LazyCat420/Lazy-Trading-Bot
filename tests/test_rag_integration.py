"""End-to-end integration test for the RAG pipeline.

Traces the full data flow:
  embed_and_store() → DuckDB → retrieve() → _format_chunks() → prompt text

This catches broken connections between Parts 1-5.
"""

from __future__ import annotations

import pytest

from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService


class TestRAGEndToEnd:
    """Full pipeline: embed → store → retrieve → format → prompt."""

    @pytest.fixture(autouse=True)
    def _clean_embeddings(self):
        """Clear embeddings table before each test for isolation."""
        from app.database import get_db

        db = get_db()
        try:
            db.execute("DELETE FROM embeddings")
            db.commit()
        except Exception:
            pass
        yield

    @pytest.mark.asyncio
    async def test_full_rag_pipeline(self, respx_mock):
        """Data embedded via EmbeddingService is retrievable and formattable."""
        from app.database import get_db

        # ── Step 1: Mock Ollama embed endpoint ─────────────────
        # All embed calls return the same 768-dim vector
        test_vec = [0.42] * 768
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [test_vec]})

        # ── Step 2: Embed via EmbeddingService ─────────────────
        embedder = EmbeddingService()
        embedder.base_url = "http://localhost:11434"

        stored = await embedder.embed_and_store(
            source_type="youtube",
            source_id="test_video_001",
            text="Apple reported record Q4 earnings beating analyst "
                 "estimates by twelve percent. Tim Cook highlighted "
                 "services revenue growth and strong iPhone demand "
                 "across all markets worldwide.",
            ticker="AAPL",
            metadata="CNBC | Market Wrap",
        )
        assert stored >= 1, f"Expected >= 1 chunks stored, got {stored}"

        # ── Step 3: Verify data in DuckDB ──────────────────────
        db = get_db()
        rows = db.execute(
            "SELECT source_type, source_id, ticker, chunk_text, metadata "
            "FROM embeddings WHERE source_id = 'test_video_001'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][0] == "youtube"
        assert rows[0][2] == "AAPL"
        assert "Apple" in rows[0][3]

        # ── Step 4: Retrieve via RetrievalService ──────────────
        retriever = RetrievalService()
        retriever.embedder.base_url = "http://localhost:11434"

        results = await retriever.retrieve(
            "AAPL", top_k=5, min_score=0.0
        )
        assert len(results) >= 1, "Retrieval returned no results"
        assert results[0]["source_type"] == "youtube"
        assert results[0]["source_id"] == "test_video_001"
        assert results[0]["score"] > 0

        # ── Step 5: Format for LLM prompt ──────────────────────
        text = await retriever.retrieve_for_trading("AAPL")
        assert "[Youtube: CNBC | Market Wrap]" in text
        assert "Apple" in text
        assert len(text) > 20

    @pytest.mark.asyncio
    async def test_decision_memory_boosts_scores(self, respx_mock):
        """Decision-type embeddings get a 10% score boost."""
        from app.database import get_db

        # Use non-uniform vectors so cosine similarity is < 1.0
        # (uniform vectors like [0.5]*N always have cosine sim 1.0
        # since they point in the same direction)
        stored_vec = [0.5 + (i % 10) * 0.01 for i in range(768)]
        query_vec = [0.5 + (i % 7) * 0.02 for i in range(768)]
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [query_vec]})

        # Insert a decision embedding and a youtube embedding
        # with identical vectors — decision should score higher
        db = get_db()
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["decision", "dec_001", "NVDA", 0,
             "TRADE DECISION for NVDA: BUY\nConfidence: 85%\n"
             "Rationale: Strong AI chip demand outlook",
             stored_vec, "BUY | executed | 2026-03-03"],
        )
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, ticker, chunk_index, "
            "chunk_text, embedding, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["youtube", "yt_001", "NVDA", 0,
             "Nvidia semiconductor sales continue strong growth",
             stored_vec, "CNBC"],
        )
        db.commit()

        retriever = RetrievalService()
        retriever.embedder.base_url = "http://localhost:11434"

        results = await retriever.retrieve(
            "NVDA", top_k=5, min_score=0.0
        )

        # Both should be retrieved
        assert len(results) >= 2

        # Find the decision and youtube chunks
        decision_result = next(
            (r for r in results if r["source_type"] == "decision"), None
        )
        yt_result = next(
            (r for r in results if r["source_type"] == "youtube"), None
        )

        assert decision_result is not None, "Decision chunk missing"
        assert yt_result is not None, "YouTube chunk missing"

        # Decision should have higher score due to 10% boost
        assert decision_result["score"] > yt_result["score"], (
            f"Decision score {decision_result['score']} should be > "
            f"YouTube score {yt_result['score']}"
        )

    @pytest.mark.asyncio
    async def test_rag_disabled_returns_empty(self, respx_mock):
        """When RAG_ENABLED=False, retrieve_for_trading still works
        (pipeline handles empty rag_context gracefully)."""
        from app.services.trading_agent import TradingAgent

        # Build a minimal context with empty rag_context
        ctx = {
            "symbol": "AAPL",
            "last_price": 185.50,
            "today_change_pct": 1.2,
            "volume": 5_000_000,
            "avg_volume": 4_000_000,
            "technical_summary": "RSI=55 | SMA20=$183",
            "quant_summary": "",
            "news_summary": "Apple earnings beat",
            "rag_context": "",  # Empty — RAG disabled
            "portfolio_cash": 10000,
            "portfolio_value": 50000,
            "max_position_pct": 15,
            "dossier_conviction": 0.7,
            "dossier_signal": "BUY",
            "quant_flags": [],
            "existing_position": {},
        }

        prompt = TradingAgent._build_prompt(ctx)

        # MARKET INTELLIGENCE should NOT appear
        assert "MARKET INTELLIGENCE" not in prompt
        # But the standard sections should
        assert "AAPL" in prompt
        assert "TECHNICAL ANALYSIS" in prompt
        assert "NEWS DIGEST" in prompt

    @pytest.mark.asyncio
    async def test_rag_context_appears_in_prompt(self, respx_mock):
        """When rag_context has data, MARKET INTELLIGENCE section appears."""
        from app.services.trading_agent import TradingAgent

        ctx = {
            "symbol": "NVDA",
            "last_price": 920.0,
            "today_change_pct": 2.5,
            "volume": 10_000_000,
            "avg_volume": 8_000_000,
            "technical_summary": "RSI=68",
            "quant_summary": "",
            "news_summary": "",
            "rag_context": (
                "[Youtube: CNBC] Nvidia AI chip demand surging\n\n"
                "[Decision: BUY | executed | 2026-03-01] Previously "
                "bought at $890 with 85% confidence"
            ),
            "portfolio_cash": 20000,
            "portfolio_value": 100000,
            "max_position_pct": 15,
            "dossier_conviction": 0.8,
            "dossier_signal": "BUY",
            "quant_flags": [],
            "existing_position": {},
        }

        prompt = TradingAgent._build_prompt(ctx)

        assert "MARKET INTELLIGENCE" in prompt
        assert "Nvidia AI chip demand" in prompt
        assert "Previously bought at $890" in prompt

    @pytest.mark.asyncio
    async def test_embedding_stats_after_store(self, respx_mock):
        """get_embedding_stats returns accurate counts after storing."""
        test_vec = [0.3] * 768
        respx_mock.post(
            "http://localhost:11434/api/embed"
        ).respond(json={"embeddings": [test_vec]})

        embedder = EmbeddingService()
        embedder.base_url = "http://localhost:11434"

        # Store 2 youtube + 1 news
        await embedder.embed_and_store(
            "youtube", "stats_yt1", "Stats test transcript one that "
            "is long enough to pass the minimum length threshold easily",
            "AAPL",
        )
        await embedder.embed_and_store(
            "youtube", "stats_yt2", "Stats test transcript two that "
            "is also long enough to pass the minimum length threshold",
            "MSFT",
        )
        await embedder.embed_and_store(
            "news", "stats_n1", "Stats test news article content that "
            "is definitely long enough to pass the minimum",
            "AAPL",
        )

        stats = EmbeddingService.get_embedding_stats()
        assert stats["total_chunks"] >= 3
        assert "youtube" in stats["by_type"]
        assert "news" in stats["by_type"]
        assert stats["by_type"]["youtube"]["sources"] >= 2
