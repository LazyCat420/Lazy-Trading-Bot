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
from app.services.yfinance_service import YFinanceCollector
from app.services.technical_service import TechnicalComputer
from app.services.risk_service import RiskComputer
from app.services.quant_engine import QuantSignalEngine
from app.services.data_distiller import DataDistiller

async def test_quant_engines(ticker: str = "NVDA"):
    logger.info(f"=== Starting Phase 2 Quant Audit for {ticker} ===")
    
    # 0. Need raw price data first
    try:
        yf = YFinanceCollector()
        logger.info("[0/4] Seeding Raw Data from YFinance for SPY (Benchmark) & NVDA...")
        
        # Benchmark for Risk
        df_spy = await yf.collect_price_history("SPY", period="2y")
        logger.info(f"Seeded {len(df_spy) if df_spy is not None else 0} price rows for SPY")

        # Ticker fundamental + price
        df_fund = await yf.collect_fundamentals(ticker)
        df_prices = await yf.collect_price_history(ticker, period="2y")
        logger.info(f"Seeded fundamentals and {len(df_prices) if df_prices is not None else 0} price rows for {ticker}")
    except Exception as e:
        logger.error(f"YFinance seeding failed: {e}")
        return

    # 1. Technical Service
    try:
        tech_service = TechnicalComputer()
        logger.info("[1/4] Testing TechnicalComputer...")
        tech_rows = await tech_service.compute(ticker)
        tech_len = len(tech_rows) if tech_rows else 0
        logger.info(f"TechnicalComputer: computed {tech_len} TechnicalRows.")
        if tech_len == 0:
            logger.error("=> FAILED: TechnicalComputer returned 0 data rows.")
    except Exception as e:
        logger.error(f"TechnicalComputer failed: {e}")

    # 2. Risk Service
    try:
        risk_service = RiskComputer()
        logger.info("[2/4] Testing RiskComputer...")
        risk_metrics = await risk_service.compute(ticker, benchmark="SPY")
        if risk_metrics:
            logger.info(f"RiskComputer: generated metrics. Beta={risk_metrics.beta}, Sharpe={risk_metrics.sharpe_ratio}")
        else:
            logger.error("=> FAILED: RiskComputer returned None.")
    except Exception as e:
        logger.error(f"RiskComputer failed: {e}")

    # 3. Quant Engine (Combines Tech + Risk into a Scorecard)
    try:
        quant = QuantSignalEngine()
        logger.info("[3/4] Testing QuantSignalEngine...")
        scorecard = quant.compute(ticker)
        if scorecard:
            flags = scorecard.flags
            logger.info(f"QuantSignalEngine: Scorecard generated. Found {len(flags)} flags. Kelly={scorecard.kelly_fraction}")
        else:
            logger.error("=> FAILED: QuantSignalEngine returned None")
    except Exception as e:
        logger.error(f"QuantSignalEngine failed: {e}")

    # 4. Data Distiller
    try:
        distiller = DataDistiller()
        logger.info("[4/4] Testing DataDistiller...")
        payload = distiller.distill_risk(risk_metrics) if risk_metrics else ""
        token_estimate = len(str(payload)) // 4
        logger.info(f"DataDistiller: Risk Payload built. Approx token size: {token_estimate}")
        if not payload:
            logger.error("=> FAILED: DataDistiller returned empty string for risk")
    except Exception as e:
        logger.error(f"DataDistiller failed: {e}")

    logger.info("=== Audit Complete ===")

if __name__ == "__main__":
    # Ensure fresh test DB
    import os
    for f in ["data/trading_bot_audit_scratch.duckdb", "data/trading_bot_audit_scratch.duckdb.wal"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
            
    try:
        asyncio.run(test_quant_engines("NVDA"))
    finally:
        reset_connection()
        sys.exit(0)
