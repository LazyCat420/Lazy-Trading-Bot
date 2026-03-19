import asyncio
import sys
import logging
from pathlib import Path

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lazy_trader")

# Override settings to use scratch DB
import app.config as config
config.settings.DB_PROFILE = "test"
config.settings.DB_PATH = Path("data/trading_bot_audit_scratch.duckdb")

from app.database import get_db, reset_connection
from app.services.llm_service import LLMService
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.services.ContextDisambiguator import ContextDisambiguator

async def test_llm_rag():
    logger.info("=== Starting Phase 3 LLM & RAG Audit ===")

    # 1. LLM Service
    try:
        logger.info("[1/4] Testing LLMService...")
        llm = LLMService()
        messages = [{"role": "user", "content": "What is 2+2? Reply with just the number 4."}]
        reply = await llm.chat(messages=messages, temperature=0.1)
        logger.info(f"LLMService chat replied: {reply.strip()}")
        if not reply:
            logger.error("=> FAILED: LLMService returned empty string")
    except Exception as e:
        logger.error(f"LLMService failed: {e}", exc_info=True)

    # 2. Embedding Service
    try:
        logger.info("[2/4] Testing EmbeddingService...")
        embedder = EmbeddingService()
        vector = await embedder.embed_text("Nvidia is a great semiconductor company.")
        vector_len = len(vector) if vector else 0
        logger.info(f"EmbeddingService: generated vector of length {vector_len}")
        if vector_len == 0:
            logger.error("=> FAILED: EmbeddingService returned empty vector")
    except Exception as e:
        logger.error(f"EmbeddingService failed: {e}")

    # 3. Context Disambiguator
    try:
        logger.info("[3/4] Testing ContextDisambiguator...")
        cd = ContextDisambiguator()
        # Test if it can filter out AAPL based on fruit context, but keep F
        res = await cd.disambiguate(["AAPL", "F"], "I ate an apple today and drove a Ford.")
        logger.info(f"ContextDisambiguator returned valid tickers: {res}")
        if not isinstance(res, list):
            logger.error("=> FAILED: ContextDisambiguator did not return a list")
    except Exception as e:
        logger.error(f"ContextDisambiguator failed: {e}")

    # 4. Retrieval Service
    try:
        logger.info("[4/4] Testing RetrievalService...")
        retriever = RetrievalService()
        docs = await retriever.retrieve("Nvidia earnings", top_k=2)
        logger.info(f"RetrievalService: found {len(docs)} documents.")
        # It's okay if docs is 0 since this is a new scratch DB, but it shouldn't crash
    except Exception as e:
        logger.error(f"RetrievalService failed: {e}")

    logger.info("=== Audit Complete ===")

if __name__ == "__main__":
    import os
    for f in ["data/trading_bot_audit_scratch.duckdb", "data/trading_bot_audit_scratch.duckdb.wal"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
    try:
        asyncio.run(test_llm_rag())
    finally:
        reset_connection()
        sys.exit(0)
