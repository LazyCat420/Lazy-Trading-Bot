import asyncio
from app.services.yfinance_service import YFinanceCollector
from app.utils.logger import logger

async def test_collection():
    print("Testing YFinance data collection...")
    try:
        yf = YFinanceCollector()
        
        # Test basic price fetch
        data = await yf.collect_price_history("AAPL")
        print(f"Price rows collected/updated: {len(data)}")
        
        # Test fundamentals
        fund = await yf.collect_fundamentals("AAPL")
        print(f"Fundamentals: {fund.ticker} (Market Cap: {fund.market_cap})")
        
        print("\nCollection test finished successfully.")
    except Exception as e:
        print(f"Error during collection: {e}")

if __name__ == "__main__":
    asyncio.run(test_collection())
