"""Execution Trace Script."""
import asyncio
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.services.trading_pipeline_service import TradingPipelineService
from app.services.bot_registry import BotRegistry

async def run_trace():
    bot = BotRegistry.get_bot("default")
    svc = TradingPipelineService(bot_id="default", llm_model="nemotron-3-nano")
    
    # Let's force it to just process ONE ticker that already threw an error: KO
    # We will just patch the discovery list to only return 'KO'
    print("Testing pipeline with KO...")
    
    # We don't want to actually run the LLM, we just want to execute a TradeAction 
    # as if the LLM returned it.
    from app.services.paper_trader import PaperTrader
    from app.services.execution_service import ExecutionService
    from app.models.trade_action import TradeAction
    from app.services.decision_logger import DecisionLogger
    import traceback
    
    try:
        trader = PaperTrader(bot_id="default")
        exec_svc = ExecutionService(trader)
        
        # log dummy decision
        d_id = DecisionLogger.log_decision(
            bot_id="default",
            symbol="KO",
            action="BUY",
            raw_response="simulated",
            rationale="simulate trace"
        )
        
        action = TradeAction(
            bot_id="default",
            symbol="KO",
            action="BUY",
            confidence=0.99,
            rationale="Testing crash",
        )
        
        # Mocking prices because markets might be closed
        result = await exec_svc.execute(
            action=action,
            decision_id=d_id,
            dry_run=False,
            current_price=60.0,
            atr=1.5
        )
        print("RESULT:")
        print(result)
        
    except Exception as e:
        print("CAUGHT EXCEPTION:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_trace())
