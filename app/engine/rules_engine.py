"""Rules Engine — applies the user's trading strategy to the pooled analysis.

The LLM evaluates all rules holistically using the full agent reports.
No deterministic overrides — the LLM has full decision-making power.
"""

from __future__ import annotations

import hashlib
import json

from app.config import settings
from app.engine.aggregator import PooledAnalysis
from app.models.decision import FinalDecision
from app.services.llm_service import LLMService
from app.utils.logger import logger


class RulesEngine:
    """Evaluates pooled agent reports against the user's trading strategy.

    The user's strategy is read from user_config/strategy.md.
    Risk parameters from user_config/risk_params.json.
    """

    def __init__(self) -> None:
        self.llm = LLMService()
        self.prompt_path = settings.PROMPTS_DIR / "decision_maker.md"
        self.strategy_path = settings.USER_CONFIG_DIR / "strategy.md"
        self.risk_params_path = settings.USER_CONFIG_DIR / "risk_params.json"

    def _load_strategy(self) -> str:
        """Load the user's trading strategy from disk."""
        if not self.strategy_path.exists():
            logger.warning("No user strategy found at %s", self.strategy_path)
            return "No trading strategy defined. Use general best practices."
        return self.strategy_path.read_text(encoding="utf-8")

    def _load_risk_params(self) -> dict:
        """Load the user's risk parameters from disk."""
        if not self.risk_params_path.exists():
            return {"max_risk_per_trade_pct": 2.0, "max_position_size_pct": 10.0}
        return json.loads(self.risk_params_path.read_text(encoding="utf-8"))

    def _strategy_hash(self, strategy_text: str) -> str:
        """Hash the strategy for version tracking."""
        return hashlib.md5(strategy_text.encode()).hexdigest()[:12]

    async def evaluate(
        self,
        ticker: str,
        pooled: PooledAnalysis,
    ) -> FinalDecision:
        """Run the decision maker LLM with the user's strategy and all agent reports.

        Returns a FinalDecision with per-rule evaluations.
        The LLM evaluates all rules using the full agent data.
        """
        logger.info("Running rules engine for %s", ticker)

        # Load user config
        strategy_text = self._load_strategy()
        risk_params = self._load_risk_params()
        strategy_version = self._strategy_hash(strategy_text)

        # Load the decision maker prompt template
        if not self.prompt_path.exists():
            raise FileNotFoundError(f"Decision maker prompt not found: {self.prompt_path}")
        template = self.prompt_path.read_text(encoding="utf-8")

        # Get formatted agent reports
        reports = pooled.format_for_decision_maker()

        # Build the system prompt by injecting everything
        schema_json = json.dumps(FinalDecision.model_json_schema(), indent=2)
        system_prompt = template
        system_prompt = system_prompt.replace("{ticker}", ticker)
        system_prompt = system_prompt.replace("{user_strategy}", strategy_text)
        system_prompt = system_prompt.replace(
            "{risk_params}", json.dumps(risk_params, indent=2)
        )
        system_prompt = system_prompt.replace(
            "{technical_report}", reports["technical_report"]
        )
        system_prompt = system_prompt.replace(
            "{fundamental_report}", reports["fundamental_report"]
        )
        system_prompt = system_prompt.replace(
            "{sentiment_report}", reports["sentiment_report"]
        )
        system_prompt = system_prompt.replace(
            "{risk_report}", reports["risk_report"]
        )
        system_prompt = system_prompt.replace("{schema_json}", schema_json)

        user_message = (
            f"Evaluate {ticker} against the trader's strategy. "
            f"Return your decision as JSON.\n\n"
            f"Use the agent reports above to evaluate EACH entry and exit rule. "
            f"If data for a rule is missing or unavailable, treat it as NEUTRAL "
            f"(do not count it against the signal). "
            f"Be decisive — the trader wants to be in the market making trades, "
            f"not sitting on the sidelines."
        )

        raw = await self.llm.chat(
            system=system_prompt,
            user=user_message,
            response_format="json",
        )

        cleaned = LLMService.clean_json_response(raw)

        try:
            decision = FinalDecision.model_validate_json(cleaned)
            decision.strategy_version = strategy_version
            decision.ticker = ticker

            logger.info(
                "Decision for %s: %s (confidence: %.2f)",
                ticker,
                decision.signal,
                decision.confidence,
            )
            return decision
        except Exception as e:
            logger.error(
                "Failed to parse decision for %s: %s\nRaw: %s",
                ticker,
                e,
                cleaned[:500],
            )
            raise
