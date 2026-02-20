"""Service to fetch industry peers using the LLM and yfinance data."""

import json
from typing import cast

from app.models.market_data import FundamentalSnapshot
from app.services.llm_service import LLMService

class PeerFetcher:
    """Uses LLM to identify 3 industry peers for a given stock, based on its fundamentals."""

    def __init__(self, llm_service: LLMService) -> None:
        self.llm = llm_service

    async def get_industry_peers(self, ticker: str, fundamentals: FundamentalSnapshot | None) -> list[str]:
        """Ask the LLM for the top 3 competitor ticker symbols based on the sector and industry."""
        sector = fundamentals.sector if fundamentals else "Unknown"
        industry = fundamentals.industry if fundamentals else "Unknown"

        system_prompt = (
            "You are a financial data assistant. Given a stock ticker, its sector, and its industry, "
            "your job is to return exactly 3 closely related competitor stock tickers that are publicly traded "
            "on major US exchanges. \n\n"
            "Respond ONLY with a valid JSON array of strings containing the 3 ticker symbols (e.g., [\"AAPL\", \"MSFT\", \"GOOGL\"]). "
            "Do not include any other text."
        )

        user_prompt = f"Ticker: {ticker}\nSector: {sector}\nIndustry: {industry}\n\nReturn 3 competitor tickers as a JSON array."

        # Fetch from LLM
        try:
            raw_response = await self.llm.chat(
                system=system_prompt,
                user=user_prompt,
                response_format="json",
            )
            cleaned = LLMService.clean_json_response(raw_response)
            peers = json.loads(cleaned)
            if isinstance(peers, list):
                # Ensure they are strings and limit to 3
                return [str(p).strip().upper() for p in peers][:3]
        except Exception as e:
            # If the LLM fails, return empty list
            pass
        return []
