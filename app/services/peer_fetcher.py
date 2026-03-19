"""Service to fetch industry peers using the LLM and yfinance data."""

from app.services.unified_logger import track_class_telemetry, track_telemetry
import json

from app.models.market_data import FundamentalSnapshot
from app.services.llm_service import LLMService
from app.services.ticker_validator import TickerValidator
from app.utils.logger import logger


@track_class_telemetry
class PeerFetcher:
    """Uses LLM to identify 3 industry peers for a given stock, validated via yfinance."""

    def __init__(self, llm_service: LLMService) -> None:
        self.llm = llm_service
        self.validator = TickerValidator()

    async def get_industry_peers(self, ticker: str, fundamentals: FundamentalSnapshot | None) -> list[str]:
        """Ask the LLM for the top 3 competitor ticker symbols based on the sector and industry.

        Returns only validated tickers that exist on US exchanges.
        """
        sector = fundamentals.sector if fundamentals else "Unknown"
        industry = fundamentals.industry if fundamentals else "Unknown"
        company_name = fundamentals.description[:100] if fundamentals and fundamentals.description else ticker

        system_prompt = (
            "You are a financial analyst specializing in competitive analysis. "
            "Given a stock ticker, its company name, sector, and industry, "
            "identify exactly 3 direct competitor stocks that are publicly traded "
            "on major US exchanges (NYSE or NASDAQ).\n\n"
            "RULES:\n"
            "- Return ONLY real, actively traded US stock tickers\n"
            "- Choose DIRECT competitors in the same industry, not vaguely related companies\n"
            "- Do NOT return ETFs, indices (DJI, SPX), or delisted stocks\n"
            "- Do NOT make up ticker symbols — only use real ones you are confident about\n\n"
            "EXAMPLES:\n"
            '- AAPL (Apple, Technology, Consumer Electronics) → ["MSFT", "GOOGL", "SAMSUNG" is wrong, use "DELL"]\n'
            '- CRS (Carpenter Technology, Industrials, Specialty Metals) → ["ATI", "HAYN", "KALU"]\n'
            '- NYT (New York Times, Communication Services, Publishing) → ["NWSA", "GCI", "LEE"]\n\n'
            "Respond ONLY with a valid JSON array of 3 ticker strings."
        )

        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Company: {company_name}\n"
            f"Sector: {sector}\n"
            f"Industry: {industry}\n\n"
            f"Return 3 direct competitor tickers as a JSON array."
        )

        try:
            raw_response = await self.llm.chat(
                system=system_prompt,
                user=user_prompt,
                response_format="json",
                audit_step="peer_discovery",
                audit_ticker=ticker,
            )
            cleaned = LLMService.clean_json_response(raw_response)
            peers = json.loads(cleaned)

            if isinstance(peers, dict):
                # Handle {"tickers": [...]} or {"competitors": [...]}
                peers = (
                    peers.get("tickers")
                    or peers.get("competitors")
                    or peers.get("peers")
                    or next(iter(peers.values()), [])
                )

            if isinstance(peers, list):
                # Known junk symbols that LLMs hallucinate as "peers"
                JUNK_SYMBOLS = {
                    "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "ARKK",
                    "DJI", "SPX", "IXIC", "VIX", "BTC", "ETH", "USD",
                }
                # Filter to valid-looking symbols, exclude self and junk
                candidates = [
                    str(p).strip().upper()
                    for p in peers
                    if isinstance(p, str)
                    and 2 <= len(p.strip()) <= 5
                    and p.strip().isalpha()
                    and str(p).strip().upper() != ticker.upper()
                    and str(p).strip().upper() not in JUNK_SYMBOLS
                ][:5]  # Allow up to 5 candidates for validation

                if candidates:
                    # Validate via yfinance — only return real tickers
                    valid = self.validator.validate_batch(candidates)
                    if valid:
                        logger.info(
                            "[PeerFetcher] %s peers: %s (validated from %s)",
                            ticker, valid[:3], candidates,
                        )
                        return valid[:3]
                    else:
                        logger.warning(
                            "[PeerFetcher] No valid peers for %s — LLM returned %s",
                            ticker, candidates,
                        )
                        return []

        except Exception as exc:
            logger.warning("[PeerFetcher] Failed for %s: %s", ticker, exc)

        return []
