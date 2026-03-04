"""ExecutionService Trace Simulator."""
import sys
import asyncio
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.services.execution_service import ExecutionService
from app.services.paper_trader import PaperTrader
from app.models.trade_action import TradeAction

async def test_execution():
    trader = PaperTrader(starting_balance=100000, bot_id="default")
    exec_svc = ExecutionService(trader)
    
    action = TradeAction(
        bot_id="default",
        symbol="KO",
        action="BUY",
        confidence=0.85,
        rationale="Testing duplicate key",
        risk_level="MED",
        time_horizon="SWING"
    )
    
    print("Testing BUY execution for KO...")
    result = await exec_svc.execute(
        action=action,
        decision_id="test_decision",
        dry_run=False,
        current_price=100.0,
        atr=2.5
    )
    print("Execution Result:", result)

if __name__ == "__main__":
    asyncio.run(test_execution())
