import asyncio
import sys
import logging
from pathlib import Path
import uuid

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lazy_trader")

# Override settings to use scratch DB
import app.config as config
config.settings.DB_PROFILE = "test"
config.settings.DB_PATH = Path("data/trading_bot_audit_scratch.duckdb")

from app.database import get_db, reset_connection
from app.services.paper_trader import PaperTrader
from app.services.execution_service import ExecutionService
from app.services.circuit_breaker import CircuitBreaker
from app.services.decision_logger import DecisionLogger
from app.models.trade_action import TradeAction

async def test_execution_layer():
    logger.info("=== Starting Phase 4 Execution & Risk Audit ===")
    
    # Initialize components
    paper_trader = PaperTrader()
    # Reset scratch portfolio to cleanly start with $10,000
    paper_trader.reset_portfolio(10000.0)
    
    execution = ExecutionService(paper_trader)
    CircuitBreaker.reset()
    
    logger.info("[1/3] Testing RiskRules & Execution sizing limits...")
    
    # 1. Simulate an LLM Trade Action
    action = TradeAction(
        symbol="DUMMY",
        action="BUY",
        confidence=0.99,
        rationale="Catastrophic hallucination",
        risk_level="MED",
        bot_id="test_bot"
    )
    decision_id = DecisionLogger.log_decision(action, "fake reasoning", status="pending")
    
    try:
        # Pass a mock price of $100 and dry_run=False to execute
        res = await execution.execute(
            action, 
            decision_id, 
            dry_run=False, 
            atr=5.0, 
            current_price=100.0
        )
        logger.info(f"Trade attempt returned: {res}")
    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)

    # Check the portfolio state
    balance = paper_trader.get_cash_balance()
    positions = paper_trader.get_positions()
    logger.info(f"Balance after trade attempt: ${balance}")
    logger.info(f"Positions: {positions}")
    
    if balance < 0:
        logger.error("=> FAILED: Account overdrawn.")
    elif res.get("status") == "executed":
        logger.info(f"RiskRules successfully sized the trade. Cost basis: ${10000.0 - balance}")
    else:
        logger.warning(f"Trade was not executed: {res.get('status')}")
    
    logger.info("[2/3] Testing Circuit Breaker tripping...")
    # Manually simulate a huge drawdown
    CircuitBreaker._trip("test_bot", "Simulated -35% account drawdown")
    
    is_tripped, reason = CircuitBreaker.is_tripped("test_bot")
    if is_tripped:
        logger.info(f"Circuit Breaker tripped correctly: {reason}")
    else:
        logger.error("=> FAILED: Circuit Breaker did not stay tripped!")

    logger.info("[3/3] Testing execution rejection during breaker trip...")
    # Attempt to execute while tripped
    action2 = TradeAction(
        symbol="AAPL", 
        action="BUY", 
        confidence=0.90, 
        rationale="Try buy",
        risk_level="LOW",
        bot_id="test_bot"
    )
    decision_id_2 = DecisionLogger.log_decision(action2, "try buy", status="pending")

    res2 = await execution.execute(
        action2, 
        decision_id_2, 
        dry_run=False, 
        current_price=150.0
    )
    
    logger.info(f"Attempted trade during breaker trip returned: {res2}")
    if res2.get("status") != "circuit_breaker":
        logger.error("=> FAILED: ExecutionService allowed a trade while CircuitBreaker was tripped!")
    else:
        logger.info("ExecutionService correctly blocked trade from the network.")

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
        asyncio.run(test_execution_layer())
    finally:
        reset_connection()
