import asyncio
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.database import switch_db, get_db
from app.services.technical_service import TechnicalComputer
from app.services.risk_service import RiskComputer

# Use test database
switch_db("test")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hard_quant_test")

async def test_technical_service_nans_and_zeros():
    logger.info("--- Testing Technical Service with NaN and Flat Price Arrays ---")
    
    db = get_db()
    db.execute("DELETE FROM price_history WHERE ticker = 'MOCK_TECH'")
    
    dates = [datetime(2025, 1, 1).date() + timedelta(days=i) for i in range(100)]
    
    # Insert 100 perfectly flat rows
    for i in range(100):
        # Inject NaN into the middle to simulate missing data
        val = None if i in (50, 51) else 100.0
        try:
            db.execute(
                "INSERT INTO price_history (ticker, date, open, high, low, close, adj_close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ['MOCK_TECH', dates[i], val, val, val, val, val, 0]
            )
        except Exception as e:
            logger.error(f"Failed to insert row {i}: {e}")
            
    svc = TechnicalComputer()
    try:
        # compute fetches from DuckDB natively
        result = await svc.compute("MOCK_TECH")
        
        # We just want to ensure it completes and fills NaNs / avoids hard crashes
        if result:
            last_row = result[-1]
            logger.info("TechnicalComputer processed flat array safely. Final MACD: %s", last_row.macd)
        else:
            logger.info("TechnicalComputer processed flat array safely (returned empty list due to nans dropping length).")
            
    except BaseException as e:
        logger.exception("CRASHED:")

async def test_risk_service_massive_gaps():
    logger.info("--- Testing Risk Service with Catastrophic Drawdowns and Outliers ---")
    
    db = get_db()
    db.execute("DELETE FROM price_history WHERE ticker IN ('MOCK_RISK', 'SPY')")
    
    dates = [datetime(2025, 1, 1).date() + timedelta(days=i) for i in range(252)]
    # Normal trend
    closes = np.linspace(100, 150, 252).tolist()
    spy_closes = np.linspace(100, 120, 252).tolist()
    
    # Simulate a flash crash: 99.9% drawdown in one day
    closes[200] = 0.01 
    closes[201] = 0.01
    
    # Simulate an infinite gap up
    closes[220] = 10000.0
    
    for i in range(252):
        c = closes[i]
        sc = spy_closes[i]
        # Insert target ticker
        db.execute(
            "INSERT INTO price_history (ticker, date, open, high, low, close, adj_close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ['MOCK_RISK', dates[i], c, c, c*0.9, c, c, 1000000]
        )
        # Insert SPY benchmark
        db.execute(
            "INSERT INTO price_history (ticker, date, open, high, low, close, adj_close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ['SPY', dates[i], sc, sc, sc*0.9, sc, sc, 1000000]
        )
        
    svc = RiskComputer()
    try:
        # Analyze risk profile
        metrics = await svc.compute("MOCK_RISK")
        logger.info("RiskService handled 99.9%% Flash Crash securely. Max Drawdown Output: %.2f%%", 
                    (metrics.max_drawdown or 0) * 100)
    except BaseException as e:
        logger.exception("CRASHED:")

async def main():
    await test_technical_service_nans_and_zeros()
    await test_risk_service_massive_gaps()

if __name__ == "__main__":
    asyncio.run(main())
