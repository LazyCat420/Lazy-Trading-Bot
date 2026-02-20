import asyncio
import json
from app.services.pipeline_service import PipelineService
from app.utils.logger import logger

async def run_test():
    svc = PipelineService()
    print("Running pipeline for AAPL in quick mode...")
    result = await svc.run("AAPL", mode="quick")
    print(f"Decision: {result.decision.signal if result.decision else 'None'}")
    
    if result.pooled and result.pooled.fundamental:
        print("\nFundamental Report Industry Comparison:")
        print(result.pooled.fundamental.industry_comparison)
    else:
        print("No fundamental report generated.")

if __name__ == "__main__":
    asyncio.run(run_test())
