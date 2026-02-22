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

    def test_agent_report_models(self) -> None:
        from app.models.agent_reports import (
            TechnicalReport,
        )
        report = TechnicalReport(
            ticker="NVDA",
            trend="UPTREND",
            momentum="BULLISH",
            signal="BUY",
            confidence=0.8,
            reasoning="Test",
        )
        assert report.signal == "BUY"
        assert 0 <= report.confidence <= 1

    def test_decision_model(self) -> None:
        from app.models.decision import RuleEvaluation
        rule = RuleEvaluation(
            rule_text="RSI between 40-65",
            is_met=True,
            evidence="RSI is 55",
            data_source="TechnicalAgent",
        )
        assert rule.is_met is True

    def test_llm_service(self) -> None:
        from app.services.llm_service import LLMService
        llm = LLMService()
        assert llm.provider in ("ollama", "openai", "lmstudio")

        # Test JSON cleaning
        raw = '```json\n{"signal": "BUY"}\n```'
        cleaned = LLMService.clean_json_response(raw)
        assert cleaned == '{"signal": "BUY"}'

    def test_agents(self) -> None:
        from app.agents.technical_agent import TechnicalAgent
        from app.agents.fundamental_agent import FundamentalAgent
        from app.agents.sentiment_agent import SentimentAgent
        from app.agents.risk_agent import RiskAgent

        # Verify agents can be instantiated and their prompts exist
        ta = TechnicalAgent()
        fa = FundamentalAgent()
        sa = SentimentAgent()
        ra = RiskAgent()

        assert ta.prompt_path.exists()
        assert fa.prompt_path.exists()
        assert sa.prompt_path.exists()
        assert ra.prompt_path.exists()

    def test_engine(self) -> None:
        from app.engine.aggregator import Aggregator

        agg = Aggregator()
        pooled = agg.pool("NVDA")
        assert pooled.ticker == "NVDA"

    def test_user_config_exists(self) -> None:
        from app.config import settings
        assert (settings.USER_CONFIG_DIR / "strategy.md").exists()
        assert (settings.USER_CONFIG_DIR / "risk_params.json").exists()
        assert (settings.USER_CONFIG_DIR / "watchlist.json").exists()
