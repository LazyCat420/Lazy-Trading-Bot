"""Smoke tests for the trading bot project structure and imports."""

from __future__ import annotations



class TestImports:
    """Verify all modules can be imported without errors."""

    def test_config(self) -> None:
        from app.config import settings
        assert settings.LLM_PROVIDER in ("ollama", "openai", "lmstudio")
        assert settings.DB_PATH is not None
        assert settings.PROMPTS_DIR.exists()

    def test_database(self) -> None:
        from app.database import get_db
        db = get_db()
        # Verify tables exist
        tables = db.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]
        assert "price_history" in table_names
        assert "fundamentals" in table_names
        assert "financial_history" in table_names
        assert "technicals" in table_names
        assert "news_articles" in table_names
        assert "youtube_transcripts" in table_names

    def test_market_data_models(self) -> None:
        from app.models.market_data import (
            OHLCVRow,
        )
        # Verify models can be instantiated
        from datetime import date
        row = OHLCVRow(
            ticker="NVDA", date=date.today(),
            open=100, high=110, low=95, close=105, volume=1000000
        )
        assert row.ticker == "NVDA"
        assert row.close == 105

    def test_dossier_models(self) -> None:
        from app.models.dossier import QuantScorecard
        scorecard = QuantScorecard(ticker="NVDA")
        assert scorecard.ticker == "NVDA"
        assert scorecard.sharpe_ratio == 0.0

    def test_trading_models(self) -> None:
        from app.models.trading import Position
        pos = Position(ticker="NVDA", qty=10, avg_entry_price=100.0)
        assert pos.ticker == "NVDA"
        assert pos.qty == 10

    def test_llm_service(self) -> None:
        from app.services.llm_service import LLMService
        llm = LLMService()
        assert llm.provider in ("ollama", "openai", "lmstudio")

        # Test JSON cleaning
        raw = '```json\n{"signal": "BUY"}\n```'
        cleaned = LLMService.clean_json_response(raw)
        assert cleaned == '{"signal": "BUY"}'

    def test_services(self) -> None:
        from app.services.quant_engine import QuantSignalEngine
        from app.services.data_distiller import DataDistiller
        qe = QuantSignalEngine()
        dd = DataDistiller()
        assert qe is not None
        assert dd is not None

    def test_user_config_exists(self) -> None:
        from app.config import settings
        assert (settings.USER_CONFIG_DIR / "strategy.md").exists()
        assert (settings.USER_CONFIG_DIR / "risk_params.json").exists()
        assert (settings.USER_CONFIG_DIR / "watchlist.json").exists()
