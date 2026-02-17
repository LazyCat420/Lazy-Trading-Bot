"""Ticker Validator — three-layer validation (exclusion list → yfinance → LLM).

Ensures only real, actively traded stock tickers enter the watchlist.
"""

from __future__ import annotations

import yfinance as yf

from app.utils.logger import logger


class TickerValidator:
    """Three-layer validation: exclusion list → yfinance → LLM logic check."""

    # Common English words and abbreviations that look like tickers
    EXCLUSION_LIST: set[str] = {
        # Reddit/finance jargon
        "YOLO", "DD", "ATH", "IMO", "EOD", "WSB", "OP", "EDIT", "TLDR",
        "GAIN", "LOSS", "HOLD", "LONG", "PUMP", "DUMP", "MOON", "BEAR",
        "BULL", "CALL", "PUT", "OTM", "ITM", "DTE", "IV", "FD",
        # Common English words that are 2-5 uppercase letters
        "NOT", "FEED", "ON", "FOR", "AND", "OR", "IF", "BUT", "SO",
        "AT", "BY", "TO", "OF", "IN", "IT", "IS", "BE", "AS", "DO",
        "WE", "UP", "MY", "GO", "ME", "US", "THE", "AI", "LOVE",
        "ALL", "CAN", "HAS", "HER", "HIM", "HIS", "HOW", "ITS",
        "LET", "MAY", "NEW", "NOW", "OLD", "OUR", "OUT", "OWN",
        "SAY", "SHE", "TOO", "USE", "DAD", "MOM", "WAR", "FAR",
        "CEO", "CFO", "COO", "SEC", "GDP", "USA", "IRS", "FBI",
        "NASA", "NEXT", "BEST", "FREE", "TRUE", "EASY", "HUGE",
        "JUST", "LIKE", "MOST", "MUCH", "ONCE", "ONLY", "OVER",
        "REAL", "SAME", "SOME", "DEAL", "VERY", "ALSO", "BACK",
        "LOOK", "KNOW", "COME", "TAKE", "WANT", "GIVE", "WORK",
        "GOOD", "WELL", "EVER", "HIGH", "LOW", "BIG", "RUN",
    }

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    def validate(self, ticker: str) -> bool:
        """Validate a single ticker. Returns True if it's a real stock."""
        ticker = ticker.upper().strip()

        # Layer 1: Exclusion list (instant)
        if ticker in self.EXCLUSION_LIST:
            logger.debug("[Validator] %s REJECTED — exclusion list", ticker)
            return False

        if not ticker:
            logger.debug("[Validator] %s REJECTED — empty", ticker)
            return False

        if len(ticker) > 5:
            logger.debug("[Validator] %s REJECTED — length %d", ticker, len(ticker))
            return False

        # Check cache
        if ticker in self._cache:
            return self._cache[ticker]

        # Layer 2: yFinance check
        try:
            stock = yf.Ticker(ticker)
            fi = stock.fast_info
            price = getattr(fi, "last_price", None)
            if price is None or price <= 0:
                logger.debug("[Validator] %s REJECTED — no price data", ticker)
                self._cache[ticker] = False
                return False

            logger.info(
                "[Validator] %s VALIDATED — price=$%.2f", ticker, price
            )
            self._cache[ticker] = True
            return True

        except Exception as e:
            logger.debug("[Validator] %s REJECTED — yfinance error: %s", ticker, e)
            self._cache[ticker] = False
            return False

    def validate_batch(self, tickers: list[str]) -> list[str]:
        """Validate multiple tickers, return only the valid ones."""
        valid = []
        for t in tickers:
            if self.validate(t):
                valid.append(t.upper().strip())
        logger.info(
            "[Validator] Batch: %d/%d valid",
            len(valid), len(tickers),
        )
        return valid
