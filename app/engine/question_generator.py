"""Layer 2 — LLM Question Generator.

Takes a QuantScorecard and asks the LLM to generate 5 targeted follow-up
questions.  Each question specifies which Phase-1 data source should be
searched (news, transcripts, fundamentals, technicals, insider).
"""

from __future__ import annotations

import json

from app.models.dossier import QuantScorecard
from app.services.llm_service import LLMService
from app.utils.logger import logger

SYSTEM_PROMPT = """\
You are a senior quant analyst reviewing a stock scorecard.
Based on the data and anomaly flags, generate exactly 5 follow-up
questions that would help determine if this is a BUY, HOLD, or SELL.

Rules:
- Questions must be ANSWERABLE from: news articles, YouTube transcripts,
  company financials, technical indicators, or insider activity data.
- Each question should target a DIFFERENT data source.
- Prioritize questions about the anomaly flags.
- Be specific: "What caused the volume spike on Feb 14?" not "Why volume?"

Respond ONLY with a JSON array of exactly 5 objects:
[
  {
    "question": "...",
    "target_source": "news" | "transcripts" | "fundamentals" | "technicals" | "insider",
    "priority": "high" | "medium" | "low"
  }
]
"""


class QuestionGenerator:
    """Generate follow-up questions from a quant scorecard via LLM."""

    def __init__(self) -> None:
        self._llm = LLMService()

    async def generate(self, scorecard: QuantScorecard) -> list[dict]:
        """Return a list of 5 question dicts from the LLM.

        Each dict has keys: question, target_source, priority.
        Falls back to hardcoded questions on LLM failure.
        """
        user_msg = scorecard.model_dump_json(indent=2)

        try:
            raw = await self._llm.chat(
                system=SYSTEM_PROMPT,
                user=user_msg,
                response_format="json",
                max_tokens=1024,
            )
            cleaned = LLMService.clean_json_response(raw)
            questions = json.loads(cleaned)

            # Validate structure
            if not isinstance(questions, list):
                raise ValueError("LLM returned non-list")

            valid: list[dict] = []
            for q in questions[:5]:
                if isinstance(q, dict) and "question" in q:
                    valid.append({
                        "question": str(q["question"]),
                        "target_source": str(
                            q.get("target_source", "news")
                        ),
                        "priority": str(q.get("priority", "medium")),
                    })

            if len(valid) < 3:
                raise ValueError(f"Only {len(valid)} valid questions")

            logger.info(
                "[QuestionGen] %s → %d questions generated",
                scorecard.ticker,
                len(valid),
            )
            return valid

        except Exception as exc:
            logger.warning(
                "[QuestionGen] LLM failed for %s (%s), using fallback",
                scorecard.ticker,
                exc,
            )
            return self._fallback_questions(scorecard)

    @staticmethod
    def _fallback_questions(sc: QuantScorecard) -> list[dict]:
        """Deterministic fallback questions based on anomaly flags."""
        questions = [
            {
                "question": (
                    f"What recent news events could explain {sc.ticker}'s "
                    f"current price action (Z-score: {sc.z_score_20d:.2f})?"
                ),
                "target_source": "news",
                "priority": "high",
            },
            {
                "question": (
                    f"What are YouTube finance channels saying about "
                    f"{sc.ticker}'s prospects?"
                ),
                "target_source": "transcripts",
                "priority": "high",
            },
            {
                "question": (
                    f"How does {sc.ticker}'s current free cash flow and debt "
                    f"compare to recent years?"
                ),
                "target_source": "fundamentals",
                "priority": "medium",
            },
            {
                "question": (
                    f"Is {sc.ticker}'s RSI and MACD confirming the current "
                    f"trend direction?"
                ),
                "target_source": "technicals",
                "priority": "medium",
            },
            {
                "question": (
                    f"Have insiders been net buyers or sellers of {sc.ticker} "
                    f"in the last 90 days?"
                ),
                "target_source": "insider",
                "priority": "low",
            },
        ]
        return questions
