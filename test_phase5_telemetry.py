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
from app.services.autonomous_loop import AutonomousLoop

async def test_telemetry():
    logger.info("=== Starting Phase 5 Telemetry Audit ===")
    
    # We will run a shortened LLM-only loop but disable time-consuming phases.
    loop = AutonomousLoop(bot_id="test_bot", model_name="test_model")
    loop.set_phase_toggles({
        "discovery": False,
        "import": False,
        "collection": False,
        "embedding": False,
        "analysis": False,
        "trading": False
    })
    
    logger.info("Running empty loop to trigger telemetry...")
    await loop.run_shared_phases()
    
    logger.info("Querying pipeline_telemetry table...")
    db = get_db()
    rows = db.execute("SELECT step_name, status, duration_ms FROM pipeline_telemetry LIMIT 20").fetchall()
    
    if not rows:
        logger.error("=> FAILED: No telemetry rows found in DuckDB after pipeline run!")
    else:
        logger.info(f"SUCCESS! Found {len(rows)} telemetry records:")
        for r in rows:
            logger.info(f" - {r[0]} ({r[1]}) took {r[2]}ms")
            
    logger.info("=== Audit Complete ===")

if __name__ == "__main__":
    import os
    # We won't delete the DB this time to avoid breaking active background tools if any, just clear telemetry
    try:
        get_db().execute("DROP TABLE IF EXISTS pipeline_telemetry")
    except Exception:
        pass
        
    try:
        asyncio.run(test_telemetry())
    finally:
        reset_connection()
